import os
import tempfile
from datetime import datetime, timezone

_tmp = tempfile.mkdtemp(prefix="tpl-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["REPORT_DIR"] = f"{_tmp}/reports"
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import clock  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

# 2026-11-10 14:00 KST (조용한 시간이 아닌 낮)
DAYTIME = datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    clock.freeze(DAYTIME)
    yield
    clock.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class User:
    def __init__(self, client: TestClient, name: str):
        self.c = client
        self.name = name
        email = f"{name}@test.kr"
        self.email = email
        r = client.post("/api/v1/auth/signup", json={"email": email, "password": "password1", "display_name": name})
        assert r.status_code == 201, r.text
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def _call(self, method, url, **kw):
        r = self.c.request(method, "/api/v1" + url, headers=self.h, **kw)
        if r.status_code == 401:  # 시계를 옮겨 토큰이 만료됐으면 다시 로그인
            login = self.c.post("/api/v1/auth/login", json={"email": self.email, "password": "password1"})
            if login.status_code == 200:
                self.h = {"Authorization": f"Bearer {login.json()['access_token']}"}
                r = self.c.request(method, "/api/v1" + url, headers=self.h, **kw)
        return r

    def get(self, url, **kw):
        return self._call("GET", url, **kw)

    def post(self, url, json=None, **kw):
        return self._call("POST", url, json=json, **kw)

    def put(self, url, json=None, **kw):
        return self._call("PUT", url, json=json, **kw)

    def patch(self, url, json=None, **kw):
        return self._call("PATCH", url, json=json, **kw)

    def delete(self, url, **kw):
        return self._call("DELETE", url, **kw)

    def notifications(self):
        return self.get("/notifications").json()["items"]


@pytest.fixture
def make_user(client):
    return lambda name: User(client, name)


def iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def team4(make_user):
    """조장 1명과 팀원 3명이 있는 팀."""
    leader = make_user("leader")
    team = leader.post("/teams", {"course_name": "모바일프로그래밍", "project_name": "발표", "final_due_at": "2026-11-30T09:00:00Z"}).json()
    code = team["invite_code"]
    members = [make_user(n) for n in ("minsu", "jiyun", "hyeon")]
    for u in members:
        assert u.post("/teams/join", {"invite_code": code}).status_code == 200
    team = leader.get(f"/teams/{team['id']}").json()
    ids = {m["display_name"]: m["member_id"] for m in team["members"]}
    return {"leader": leader, "members": members, "team": team, "tid": team["id"], "ids": ids}
