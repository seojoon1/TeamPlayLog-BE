from datetime import datetime, timedelta, timezone

from app import clock
from app.db import SessionLocal
from app.jobs import run_jobs
from app.notify import deliver_at


def jobs():
    db = SessionLocal()
    try:
        run_jobs(db)
    finally:
        db.close()


def kinds(user):
    return [n["kind"] for n in user.notifications()]


# ---- 알림 규칙 ----------------------------------------------------------------


def test_quiet_hours_delay_to_8am():
    night = datetime(2026, 11, 10, 15, 30, tzinfo=timezone.utc)  # 00:30 KST
    assert deliver_at(night) == datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc)  # 08:00 KST
    late = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)  # 23:30 KST
    assert deliver_at(late) == datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc)
    day = datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)  # 14:00 KST
    assert deliver_at(day) == day


def test_notifications_hidden_until_delivery_time(team4):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    clock.freeze(datetime(2026, 11, 10, 15, 30, tzinfo=timezone.utc))  # 새벽
    minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "note": "새벽 작업"})
    assert "CONFIRM_REQUEST" not in kinds(jiyun)
    clock.freeze(datetime(2026, 11, 10, 23, 1, tzinfo=timezone.utc))  # 08:01
    assert "CONFIRM_REQUEST" in kinds(jiyun)


def test_due_reminders_only_to_assignee(team4):
    tid, ids = team4["tid"], team4["ids"]
    leader, minsu, jiyun = team4["leader"], *team4["members"][:2]
    leader.post(f"/teams/{tid}/tasks", {"title": "자료 조사", "due_at": "2026-11-11T03:00:00Z", "assignee_member_ids": [ids["minsu"]]})
    jobs()  # 마감 22시간 전
    assert kinds(minsu).count("DUE_SOON") == 1
    assert "DUE_SOON" not in kinds(jiyun)
    jobs()
    assert kinds(minsu).count("DUE_SOON") == 1  # 한 번만
    clock.travel(timedelta(hours=23))
    jobs()
    assert kinds(minsu).count("OVERDUE") == 1
    assert "OVERDUE" not in kinds(leader)


def test_unconfirmed_after_72h(team4):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    rid = minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "note": "자료"}).json()["id"]
    clock.travel(timedelta(hours=71))
    jobs()
    assert minsu.get(f"/teams/{tid}/records").json()["items"][0]["status"] == "PENDING"
    clock.travel(timedelta(hours=2))
    jobs()
    assert minsu.get(f"/teams/{tid}/records").json()["items"][0]["status"] == "UNCONFIRMED"
    # 미확인 기록도 나중에 확인할 수 있다
    assert jiyun.post(f"/records/{rid}/confirmations", {"decision": "CONFIRM"}).json()["status"] == "CONFIRMED"


def test_agreement_reminder_max_three(team4):
    leader, tid, ids = team4["leader"], team4["tid"], team4["ids"]
    minsu = team4["members"][0]
    leader.put(f"/teams/{tid}/agreement", {"roles": [{"member_id": ids["minsu"], "role_name": "조사"}]})
    leader.post(f"/teams/{tid}/agreement/submit")
    for _ in range(5):
        clock.travel(timedelta(hours=24))
        jobs()
    titles = [n["title"] for n in minsu.notifications() if n["kind"] == "AGREEMENT_CONSENT"]
    assert titles.count("역할 약속 동의를 기다리고 있어요") == 3


def test_mark_read(team4):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "note": "자료"})
    n = jiyun.notifications()[0]
    assert jiyun.patch(f"/notifications/{n['id']}/read").status_code == 204
    assert minsu.patch(f"/notifications/{n['id']}/read").status_code == 404
    assert jiyun.get("/notifications", params={"unread_only": "true"}).json()["items"] == []


# ---- F-08 기여 리포트 ------------------------------------------------------------


def _records(team4):
    tid = team4["tid"]
    minsu, jiyun = team4["members"][:2]
    a = minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "link_url": "https://example.com/a", "note": "자료 조사"}).json()["id"]
    minsu.post(f"/teams/{tid}/records", {"type": "MEETING", "note": "회의 참석"})
    jiyun.post(f"/records/{a}/confirmations", {"decision": "CONFIRM"})


def test_report_content_has_no_totals(team4):
    _records(team4)
    rep = team4["leader"].post(f"/teams/{team4['tid']}/reports", {}).json()
    content = rep["content"]
    minsu = next(m for m in content["members"] if m["name"] == "minsu")
    assert [e["note"] for e in minsu["confirmed"]["OUTPUT"]] == ["자료 조사"]
    assert minsu["confirmed"]["OUTPUT"][0]["confirmed_by"] == ["jiyun"]
    assert [e["note"] for e in minsu["unconfirmed"]["MEETING"]] == ["회의 참석"]
    assert minsu["unconfirmed"]["MEETING"][0]["no_output"] is True
    member_data = str(content["members"])  # 안내 문구는 빼고 팀원 데이터만 검사
    for word in ("total", "score", "rank", "count", "합계", "순위"):
        assert word not in member_data
    assert "기여의 크기를 뜻하지 않으며" in content["notice"]


def test_report_submit_rules(team4):
    leader, tid = team4["leader"], team4["tid"]
    minsu, jiyun, hyeon = team4["members"]
    _records(team4)
    rep = leader.post(f"/teams/{tid}/reports", {}).json()
    rid = rep["id"]
    assert rep["can_submit"] is False
    assert minsu.post(f"/reports/{rid}/submit").status_code == 403
    assert leader.post(f"/reports/{rid}/submit").json()["code"] == "CANNOT_SUBMIT"
    minsu.post(f"/reports/{rid}/views", {"objection": "회의 기록이 하나 빠졌어요"})
    jiyun.post(f"/reports/{rid}/views", {})
    rep = hyeon.post(f"/reports/{rid}/views", {}).json()
    assert rep["can_submit"] is False  # 조장이 아니므로
    rep = leader.get(f"/reports/{rid}").json()
    assert rep["can_submit"] is True
    rep = leader.post(f"/reports/{rid}/submit").json()
    assert rep["status"] == "SUBMITTED"
    assert any(v["objection"] == "회의 기록이 하나 빠졌어요" for v in rep["views"])
    # 제출 후에는 의견을 바꿀 수 없다
    assert minsu.post(f"/reports/{rid}/views", {"objection": "수정"}).status_code == 409
    pdf = leader.get(f"/reports/{rid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert pdf.headers["content-type"] == "application/pdf"


def test_report_submit_after_48h_without_all_views(team4):
    leader, tid = team4["leader"], team4["tid"]
    rid = leader.post(f"/teams/{tid}/reports", {}).json()["id"]
    clock.travel(timedelta(hours=24, minutes=1))
    jobs()
    assert "REPORT_VIEW_REQUEST" in kinds(team4["members"][0])
    clock.travel(timedelta(hours=24))
    rep = leader.post(f"/reports/{rid}/submit").json()
    assert rep["status"] == "SUBMITTED"
    assert sum(v["viewed_at"] is None for v in rep["views"]) == 3


def test_draft_pdf_preview(team4):
    _records(team4)
    rid = team4["leader"].post(f"/teams/{team4['tid']}/reports", {}).json()["id"]
    pdf = team4["members"][0].get(f"/reports/{rid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and len(pdf.content) > 5000
