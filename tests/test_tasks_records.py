from datetime import timedelta

from app import clock

DUE = "2026-11-14T09:00:00Z"


def _task(user, tid, title="자료 조사", due=DUE, **kw):
    r = user.post(f"/teams/{tid}/tasks", {"title": title, "due_at": due, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---- F-04 할 일 ---------------------------------------------------------------


def test_member_creates_only_own_tasks(team4):
    tid, ids = team4["tid"], team4["ids"]
    minsu = team4["members"][0]
    t = _task(minsu, tid)
    assert [a["display_name"] for a in t["assignees"]] == ["minsu"]
    r = minsu.post(f"/teams/{tid}/tasks", {"title": "남의 일", "due_at": DUE, "assignee_member_ids": [ids["jiyun"]]})
    assert r.status_code == 403
    t = _task(team4["leader"], tid, "슬라이드", assignee_member_ids=[ids["jiyun"]])
    assert t["assignees"][0]["display_name"] == "jiyun"
    assert any(n["kind"] == "TASK_ASSIGNED" for n in team4["members"][1].notifications())


def test_done_requires_record(team4):
    tid = team4["tid"]
    minsu = team4["members"][0]
    t = _task(minsu, tid)
    r = minsu.patch(f"/tasks/{t['id']}", {"status": "DONE"})
    assert r.status_code == 409 and r.json()["code"] == "NEEDS_RECORD"
    minsu.post(f"/teams/{tid}/records", {"task_id": t["id"], "type": "OUTPUT", "link_url": "https://example.com/doc"})
    r = minsu.patch(f"/tasks/{t['id']}", {"status": "DONE"})
    assert r.status_code == 200 and r.json()["status"] == "DONE"
    board = minsu.get(f"/teams/{tid}/tasks").json()
    assert board["tasks_done"] == 1 and board["d_day"] == 20


def test_only_assignee_or_leader_edits(team4):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    t = _task(minsu, tid)
    assert jiyun.patch(f"/tasks/{t['id']}", {"title": "몰래 수정"}).status_code == 403
    assert team4["leader"].patch(f"/tasks/{t['id']}", {"title": "조장 수정"}).status_code == 200


def test_overdue_and_due_change_history(team4):
    tid = team4["tid"]
    minsu = team4["members"][0]
    t = _task(minsu, tid)
    clock.travel(timedelta(days=5))  # 11.15, 마감 지남
    board = minsu.get(f"/teams/{tid}/tasks").json()
    assert board["tasks"][0]["status"] == "OVERDUE"
    assert minsu.patch(f"/tasks/{t['id']}", {"status": "OVERDUE"}).status_code == 422
    r = minsu.patch(f"/tasks/{t['id']}", {"due_at": "2026-11-18T09:00:00Z"}).json()
    assert r["status"] == "TODO"
    detail = minsu.get(f"/tasks/{t['id']}").json()
    assert [e["kind"] for e in detail["events"]] == ["DUE_CHANGED"]


def test_board_mine_filter(team4):
    tid, ids = team4["tid"], team4["ids"]
    leader, minsu = team4["leader"], team4["members"][0]
    _task(minsu, tid, "내 일")
    _task(leader, tid, "남의 일", assignee_member_ids=[ids["jiyun"]])
    _task(leader, tid, "다 같이", for_everyone=True)
    mine = [t["title"] for t in minsu.get(f"/teams/{tid}/tasks", params={"mine": "true"}).json()["tasks"]]
    assert sorted(mine) == ["내 일", "다 같이"]


# ---- F-05 기록 / F-06 확인 --------------------------------------------------------


def test_record_needs_output_or_note(team4):
    tid = team4["tid"]
    minsu = team4["members"][0]
    r = minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT"})
    assert r.status_code == 422
    r = minsu.post(f"/teams/{tid}/records", {"type": "MEETING", "note": "2차 회의 참석"}).json()
    assert r["has_output"] is False and r["status"] == "PENDING"
    # 다른 팀원 3명에게 확인 요청이 간다
    for u in team4["members"][1:] + [team4["leader"]]:
        assert any(n["kind"] == "CONFIRM_REQUEST" for n in u.notifications())
    assert not any(n["kind"] == "CONFIRM_REQUEST" for n in minsu.notifications())


def test_late_record_flag(team4):
    tid = team4["tid"]
    minsu = team4["members"][0]
    t = _task(minsu, tid)
    clock.travel(timedelta(days=5))
    r = minsu.post(f"/teams/{tid}/records", {"task_id": t["id"], "type": "OUTPUT", "note": "늦게 올림"}).json()
    assert r["is_late"] is True


def test_confirm_reject_and_recheck(team4):
    tid = team4["tid"]
    minsu, jiyun, hyeon = team4["members"]
    rec = minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "link_url": "https://example.com/a"}).json()
    rid = rec["id"]
    r = minsu.post(f"/records/{rid}/confirmations", {"decision": "CONFIRM"})
    assert r.status_code == 409 and r.json()["code"] == "SELF_CONFIRM"
    assert jiyun.get(f"/teams/{tid}/inbox").json()[0]["id"] == rid
    r = jiyun.post(f"/records/{rid}/confirmations", {"decision": "CONFIRM"}).json()
    assert r["status"] == "CONFIRMED"
    assert jiyun.get(f"/teams/{tid}/inbox").json() == []
    # 거절은 사유가 필요하고, 거절이 있으면 보류
    assert hyeon.post(f"/records/{rid}/confirmations", {"decision": "REJECT"}).status_code == 422
    r = hyeon.post(f"/records/{rid}/confirmations", {"decision": "REJECT", "reason_type": "MISMATCH", "reason_text": "링크가 다른 문서"}).json()
    assert r["status"] == "ON_HOLD"
    assert any(n["kind"] == "RECORD_REJECTED" for n in minsu.notifications())
    # 작성자가 고치면 이전 판단은 모두 무효가 되고 다시 확인 대기
    r = minsu.patch(f"/records/{rid}", {"link_url": "https://example.com/b"}).json()
    assert r["status"] == "PENDING" and r["revision_count"] == 1
    assert all(c["valid"] is False for c in r["confirmations"])
    r = jiyun.post(f"/records/{rid}/confirmations", {"decision": "CONFIRM"}).json()
    assert r["status"] == "CONFIRMED"
    # 확인된 기록을 고치면 재확인 필요
    r = minsu.patch(f"/records/{rid}", {"note": "최종본"}).json()
    assert r["status"] == "RECHECK"


def test_withdraw_hides_from_feed(team4):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    rid = minsu.post(f"/teams/{tid}/records", {"type": "REVIEW", "note": "검토"}).json()["id"]
    assert jiyun.post(f"/records/{rid}/withdraw").status_code == 403
    assert minsu.post(f"/records/{rid}/withdraw").json()["status"] == "WITHDRAWN"
    assert minsu.get(f"/teams/{tid}/records").json()["items"] == []


def test_feed_pagination(team4):
    tid = team4["tid"]
    minsu = team4["members"][0]
    for i in range(5):
        minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "note": f"기록 {i}"})
        clock.travel(timedelta(minutes=1))
    p1 = minsu.get(f"/teams/{tid}/records", params={"limit": 2}).json()
    p2 = minsu.get(f"/teams/{tid}/records", params={"limit": 2, "cursor": p1["next_cursor"]}).json()
    p3 = minsu.get(f"/teams/{tid}/records", params={"limit": 2, "cursor": p2["next_cursor"]}).json()
    notes = [r["note"] for r in p1["items"] + p2["items"] + p3["items"]]
    assert notes == ["기록 4", "기록 3", "기록 2", "기록 1", "기록 0"]
    assert p3["next_cursor"] is None


def test_mutual_confirm_warning(team4):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    for i in range(5):
        a = minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "note": f"m{i}"}).json()["id"]
        b = jiyun.post(f"/teams/{tid}/records", {"type": "OUTPUT", "note": f"j{i}"}).json()["id"]
        jiyun.post(f"/records/{a}/confirmations", {"decision": "CONFIRM"})
        minsu.post(f"/records/{b}/confirmations", {"decision": "CONFIRM"})
    warnings = [n for n in team4["leader"].notifications() if n["kind"] == "MUTUAL_CONFIRM_WARNING"]
    assert len(warnings) == 1


def test_file_upload_and_access(team4, make_user, client):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    assert minsu.post("/files/upload-url", {"filename": "virus.exe", "content_type": "x", "size": 10}).status_code == 422
    u = minsu.post("/files/upload-url", {"filename": "자료.pdf", "content_type": "application/pdf", "size": 9}).json()
    assert jiyun.c.put(u["upload_url"], content=b"%PDF-test", headers=jiyun.h).status_code == 403  # 남의 주소
    assert client.put(u["upload_url"], content=b"%PDF-test", headers=minsu.h).status_code == 204
    rec = minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "file_key": u["file_key"]}).json()
    assert rec["has_output"] is True
    # 같은 팀원은 받을 수 있고, 다른 팀 사람은 못 받는다
    assert client.get(u["upload_url"], headers=jiyun.h).content == b"%PDF-test"
    outsider = make_user("outsider")
    assert client.get(u["upload_url"], headers=outsider.h).status_code == 404
    # 남이 올린 파일 키로는 기록을 만들 수 없다
    assert jiyun.post(f"/teams/{tid}/records", {"type": "OUTPUT", "file_key": u["file_key"]}).status_code == 422
