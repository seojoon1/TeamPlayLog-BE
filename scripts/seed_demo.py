"""시연용 데이터 넣기 (로컬 개발 서버 전용).

    .venv/Scripts/python scripts/seed_demo.py [서버 주소, 기본 http://localhost:8000]
    SEED_BASE_DATE=2026-11-19 .venv/Scripts/python scripts/seed_demo.py <서버 주소>   # 마감일을 그날 기준으로

4명 팀(조장·민수·지윤·현우)과 역할 약속, 할 일, 기록을 만든다. 이미 있으면 로그인만 한다.
시연 계정 비밀번호는 모두 아래 DEMO_PASSWORD이다. 실제 서비스 계정에 쓰지 말 것.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api/v1"
DEMO_PASSWORD = "demo1234!"
PEOPLE = [("leader", "김조장"), ("minsu", "민수"), ("jiyun", "지윤"), ("hyeon", "현우")]

c = httpx.Client(timeout=20)


def user(key: str, name: str) -> dict:
    email = f"{key}@demo.teamplaylog"
    r = c.post(f"{BASE}/auth/signup", json={"email": email, "password": DEMO_PASSWORD, "display_name": name})
    if r.status_code == 409:
        r = c.post(f"{BASE}/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def call(h, method, path, json=None):
    r = c.request(method, BASE + path, headers=h, json=json)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path} -> {r.status_code} {r.text}")
    return r.json() if r.content else None


KST = timezone(timedelta(hours=9))
# 기준 날짜. 환경 변수 SEED_BASE_DATE=2026-11-19 처럼 주면 그날을 '오늘'로 보고 마감일을 정한다 (심사 기간에 맞출 때).
_base = os.environ.get("SEED_BASE_DATE")
BASE_DAY = datetime.fromisoformat(_base).replace(tzinfo=KST) if _base else datetime.now(KST)


def at(days: float, hour: int = 18) -> str:
    """기준 날짜(KST) 기준 days일 뒤 hour시(KST)를 UTC ISO로."""
    d = BASE_DAY.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return d.astimezone(timezone.utc).isoformat()


H = {k: user(k, n) for k, n in PEOPLE}
if call(H["leader"], "GET", "/teams"):
    print("이미 시연 팀이 있습니다. 로그인 계정만 확인했습니다.")
    sys.exit(0)

team = call(H["leader"], "POST", "/teams", {"course_name": "모바일프로그래밍", "project_name": "중간 발표", "final_due_at": at(9, 10)})
tid = team["id"]
for k in ("minsu", "jiyun", "hyeon"):
    call(H[k], "POST", "/teams/join", {"invite_code": team["invite_code"]})
ids = {m["display_name"]: m["member_id"] for m in call(H["leader"], "GET", f"/teams/{tid}")["members"]}

call(H["leader"], "PUT", f"/teams/{tid}/agreement", {
    "roles": [
        {"member_id": ids["김조장"], "role_name": "취합·일정"},
        {"member_id": ids["민수"], "role_name": "자료 조사"},
        {"member_id": ids["지윤"], "role_name": "슬라이드"},
        {"member_id": ids["현우"], "role_name": "대본·발표"},
    ],
    "tasks": [
        {"assignee_member_id": ids["민수"], "title": "관련 논문·기사 조사", "due_at": at(2)},
        {"assignee_member_id": ids["지윤"], "title": "발표 슬라이드 초안", "due_at": at(4)},
        {"assignee_member_id": ids["현우"], "title": "발표 대본 작성", "due_at": at(-1, 22)},
    ],
    "reply_hours": 24,
    "meeting_rule": "회의 주 1회 (수요일)",
    "free_rules": ["늦을 것 같으면 하루 전에 알리기"],
})
call(H["leader"], "POST", f"/teams/{tid}/agreement/submit")
for k in ("minsu", "jiyun", "hyeon"):
    call(H[k], "POST", f"/teams/{tid}/agreement/consent", {"agreed": True})

call(H["leader"], "POST", f"/teams/{tid}/tasks", {"title": "발표 리허설", "due_at": at(7, 20), "for_everyone": True})
call(H["minsu"], "POST", f"/teams/{tid}/tasks", {"title": "참고 자료 목록 정리", "due_at": at(5)})

tasks = {t["title"]: t["id"] for t in call(H["leader"], "GET", f"/teams/{tid}/tasks")["tasks"]}
r1 = call(H["minsu"], "POST", f"/teams/{tid}/records", {
    "task_id": tasks["관련 논문·기사 조사"], "type": "OUTPUT",
    "link_url": "https://drive.google.com/demo-research", "note": "논문 3편과 기사 5개 핵심 요약",
})
call(H["jiyun"], "POST", f"/records/{r1['id']}/confirmations", {"decision": "CONFIRM"})
call(H["minsu"], "PATCH", f"/tasks/{tasks['관련 논문·기사 조사']}", {"status": "DONE"})
call(H["jiyun"], "POST", f"/teams/{tid}/records", {
    "task_id": tasks["발표 슬라이드 초안"], "type": "OUTPUT",
    "link_url": "https://www.figma.com/demo-slides", "note": "슬라이드 1~8장 초안",
})
call(H["hyeon"], "POST", f"/teams/{tid}/records", {"task_id": tasks["발표 대본 작성"], "type": "OUTPUT", "note": "대본 앞부분 작성 중"})
call(H["leader"], "POST", f"/teams/{tid}/records", {"type": "MEETING", "note": "2차 회의 진행, 역할 재확인"})
call(H["minsu"], "PATCH", f"/tasks/{tasks['참고 자료 목록 정리']}", {"status": "DOING"})

print("시연 팀을 만들었습니다:", team["project_name"], "/ 초대 코드", team["invite_code"])
print("로그인: minsu@demo.teamplaylog 등 (비밀번호는 이 파일의 DEMO_PASSWORD)")
