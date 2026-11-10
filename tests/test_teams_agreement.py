from datetime import timedelta

from app import clock


def test_create_and_join(team4):
    t = team4["team"]
    assert len(t["members"]) == 4
    assert t["invite_code"] and len(t["invite_code"]) == 6
    # 팀원에게는 초대 코드가 보이지 않는다
    assert team4["members"][0].get(f"/teams/{t['id']}").json()["invite_code"] is None
    assert len(team4["members"][0].get("/teams").json()) == 1


def test_join_errors(make_user):
    leader = make_user("leader")
    t = leader.post("/teams", {"course_name": "c", "project_name": "p", "final_due_at": "2026-11-30T09:00:00Z", "max_members": 2}).json()
    a, b = make_user("aa"), make_user("bb")
    assert a.post("/teams/join", {"invite_code": "ZZZZZZ"}).json()["code"] == "NOT_FOUND"
    assert a.post("/teams/join", {"invite_code": t["invite_code"].lower()}).status_code == 200  # 소문자도 허용
    assert a.post("/teams/join", {"invite_code": t["invite_code"]}).json()["code"] == "ALREADY_MEMBER"
    assert b.post("/teams/join", {"invite_code": t["invite_code"]}).json()["code"] == "TEAM_FULL"


def test_invite_expires_and_regenerates(make_user):
    leader = make_user("leader")
    t = leader.post("/teams", {"course_name": "c", "project_name": "p", "final_due_at": "2026-11-30T09:00:00Z"}).json()
    clock.travel(timedelta(days=8))
    a = make_user("aa")
    assert a.post("/teams/join", {"invite_code": t["invite_code"]}).json()["code"] == "INVITE_EXPIRED"
    new = leader.post(f"/teams/{t['id']}/invite-code").json()["invite_code"]
    assert a.post("/teams/join", {"invite_code": new}).status_code == 200


def test_other_team_is_invisible(team4, make_user):
    outsider = make_user("outsider")
    assert outsider.get(f"/teams/{team4['tid']}").status_code == 404
    assert outsider.get(f"/teams/{team4['tid']}/tasks").status_code == 404


def test_leader_must_transfer_before_leaving(team4):
    leader, tid, ids = team4["leader"], team4["tid"], team4["ids"]
    assert leader.delete(f"/teams/{tid}/members/me").json()["code"] == "TRANSFER_LEADER_FIRST"
    assert team4["members"][0].patch(f"/teams/{tid}/leader", {"member_id": ids["jiyun"]}).status_code == 403
    assert leader.patch(f"/teams/{tid}/leader", {"member_id": ids["minsu"]}).status_code == 200
    assert leader.delete(f"/teams/{tid}/members/me").status_code == 204
    assert team4["members"][0].get(f"/teams/{tid}").json()["my_role"] == "LEADER"


def _agreement(ids):
    return {
        "roles": [
            {"member_id": ids["leader"], "role_name": "취합"},
            {"member_id": ids["minsu"], "role_name": "자료 조사"},
            {"member_id": ids["jiyun"], "role_name": "슬라이드"},
            {"member_id": ids["hyeon"], "role_name": "발표"},
        ],
        "tasks": [
            {"assignee_member_id": ids["minsu"], "title": "자료 조사", "due_at": "2026-11-14T09:00:00Z"},
            {"assignee_member_id": ids["jiyun"], "title": "슬라이드", "due_at": "2026-11-16T09:00:00Z"},
        ],
        "reply_hours": 24,
        "meeting_rule": "회의 주 1회",
        "free_rules": ["늦으면 미리 알리기"],
    }


def test_agreement_flow_creates_tasks(team4):
    leader, tid, ids = team4["leader"], team4["tid"], team4["ids"]
    minsu, jiyun, hyeon = team4["members"]
    assert minsu.put(f"/teams/{tid}/agreement", _agreement(ids)).status_code == 403  # 조장만
    ag = leader.put(f"/teams/{tid}/agreement", _agreement(ids)).json()
    assert ag["status"] == "DRAFT" and ag["version"] == 1
    ag = leader.post(f"/teams/{tid}/agreement/submit").json()
    assert ag["status"] == "PENDING"
    assert any(n["kind"] == "AGREEMENT_CONSENT" for n in minsu.notifications())
    # 아직 확정 전에는 보드에 약속 할 일이 없다
    assert leader.get(f"/teams/{tid}/tasks").json()["tasks"] == []
    minsu.post(f"/teams/{tid}/agreement/consent", {"agreed": True})
    jiyun.post(f"/teams/{tid}/agreement/consent", {"agreed": True})
    ag = hyeon.post(f"/teams/{tid}/agreement/consent", {"agreed": True}).json()
    assert ag["status"] == "CONFIRMED"
    board = leader.get(f"/teams/{tid}/tasks").json()
    assert board["agreement_confirmed"] is True
    assert [t["title"] for t in board["tasks"]] == ["자료 조사", "슬라이드"]
    assert board["tasks"][0]["assignees"][0]["display_name"] == "minsu"


def test_change_request_needs_reason_and_notifies_leader(team4):
    leader, tid, ids = team4["leader"], team4["tid"], team4["ids"]
    minsu, jiyun = team4["members"][:2]
    leader.put(f"/teams/{tid}/agreement", _agreement(ids))
    leader.post(f"/teams/{tid}/agreement/submit")
    assert minsu.post(f"/teams/{tid}/agreement/consent", {"agreed": False}).status_code == 422
    ag = minsu.post(f"/teams/{tid}/agreement/consent", {"agreed": False, "change_request": "마감을 하루 늦춰 주세요"}).json()
    assert ag["status"] == "PENDING"
    assert any("마감을 하루" in n["body"] for n in leader.notifications())
    # 수정 요청 사유는 조장과 본인에게만 보인다
    mine = next(c for c in jiyun.get(f"/teams/{tid}/agreement").json()["current"]["consents"] if c["display_name"] == "minsu")
    assert mine["agreed"] is False and mine["change_request"] is None


def _confirm_all(team4):
    leader, tid, ids = team4["leader"], team4["tid"], team4["ids"]
    leader.put(f"/teams/{tid}/agreement", _agreement(ids))
    leader.post(f"/teams/{tid}/agreement/submit")
    for u in team4["members"]:
        u.post(f"/teams/{tid}/agreement/consent", {"agreed": True})


def test_edit_after_confirm_makes_new_version_and_keeps_tasks(team4):
    leader, tid, ids = team4["leader"], team4["tid"], team4["ids"]
    _confirm_all(team4)
    body = _agreement(ids)
    body["tasks"][0]["due_at"] = "2026-11-15T09:00:00Z"
    ag = leader.put(f"/teams/{tid}/agreement", body).json()
    assert ag["version"] == 2 and ag["status"] == "DRAFT"
    hist = leader.get(f"/teams/{tid}/agreement").json()
    assert [v["status"] for v in hist["versions"]] == ["DRAFT", "CONFIRMED"]
    leader.post(f"/teams/{tid}/agreement/submit")
    for u in team4["members"]:
        u.post(f"/teams/{tid}/agreement/consent", {"agreed": True})
    hist = leader.get(f"/teams/{tid}/agreement").json()
    assert [v["status"] for v in hist["versions"]] == ["CONFIRMED", "SUPERSEDED"]
    tasks = leader.get(f"/teams/{tid}/tasks").json()["tasks"]
    assert len(tasks) == 2  # 같은 할 일을 다시 만들지 않는다
    assert next(t for t in tasks if t["title"] == "자료 조사")["due_at"].startswith("2026-11-15")


def test_new_member_resets_confirmed_agreement(team4, make_user):
    tid = team4["tid"]
    _confirm_all(team4)
    newbie = make_user("newbie")
    newbie.post("/teams/join", {"invite_code": team4["team"]["invite_code"]})
    cur = newbie.get(f"/teams/{tid}/agreement").json()["current"]
    assert cur["status"] == "PENDING"
    assert newbie.get(f"/teams/{tid}/tasks").json()["agreement_confirmed"] is False
    cur = newbie.post(f"/teams/{tid}/agreement/consent", {"agreed": True}).json()
    assert cur["status"] == "CONFIRMED"
    assert len(newbie.get(f"/teams/{tid}/tasks").json()["tasks"]) == 2
