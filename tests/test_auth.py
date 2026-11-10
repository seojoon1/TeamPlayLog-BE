from datetime import timedelta

from app import clock


def test_signup_login_me(client, make_user):
    u = make_user("minsu")
    assert u.get("/me").json()["display_name"] == "minsu"
    r = client.post("/api/v1/auth/login", json={"email": "MINSU@test.kr", "password": "password1"})
    assert r.status_code == 200
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    assert r.status_code == 200


def test_duplicate_email(client, make_user):
    make_user("minsu")
    r = client.post("/api/v1/auth/signup", json={"email": "minsu@test.kr", "password": "password1", "display_name": "dup"})
    assert r.status_code == 409 and r.json()["code"] == "EMAIL_TAKEN"


def test_validation_error_format(client):
    r = client.post("/api/v1/auth/signup", json={"email": "x", "password": "1", "display_name": "a"})
    body = r.json()
    assert r.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert {f["field"] for f in body["fields"]} == {"email", "password", "display_name"}


def test_login_lock_after_five_failures(client, make_user):
    make_user("minsu")
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "minsu@test.kr", "password": "wrong"})
    r = client.post("/api/v1/auth/login", json={"email": "minsu@test.kr", "password": "password1"})
    assert r.status_code == 429 and r.json()["code"] == "LOGIN_LOCKED"
    clock.travel(timedelta(minutes=11))
    assert client.post("/api/v1/auth/login", json={"email": "minsu@test.kr", "password": "password1"}).status_code == 200


def test_access_token_expires_after_60_minutes(make_user):
    u = make_user("minsu")
    clock.travel(timedelta(minutes=59))
    assert u.c.get("/api/v1/me", headers=u.h).status_code == 200
    clock.travel(timedelta(minutes=2))
    assert u.c.get("/api/v1/me", headers=u.h).status_code == 401


def test_refresh_token_cannot_be_used_as_access(client, make_user):
    make_user("minsu")
    r = client.post("/api/v1/auth/login", json={"email": "minsu@test.kr", "password": "password1"}).json()
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {r['refresh_token']}"}).status_code == 401


def test_delete_me_keeps_team_records(team4):
    minsu = team4["members"][0]
    tid = team4["tid"]
    assert minsu.post(f"/teams/{tid}/records", {"type": "OUTPUT", "note": "자료 조사"}).status_code == 201
    assert minsu.delete("/me").status_code == 204
    feed = team4["leader"].get(f"/teams/{tid}/records").json()["items"]
    assert feed[0]["author_name"] == "탈퇴한 팀원"
    assert minsu.c.get("/api/v1/me", headers=minsu.h).status_code == 401


def test_requires_login(client):
    r = client.get("/api/v1/teams")
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED"
