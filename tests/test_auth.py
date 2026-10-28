from helpers import STRONG_PASSWORD, signup
from sqlalchemy import select

from jenius.api.deps import COOKIE_NAME
from jenius.models import Session, User


def test_signup_signs_you_in_and_stores_only_hashes(client, database):
    user = signup(client)
    assert user["email"] == "sam@example.com" and user["plan"] == "free"
    token = client.cookies.get(COOKIE_NAME)
    assert token and client.get("/api/auth/me").json()["name"] == "Sam Rao"
    with database.sessions() as db:
        stored = db.scalar(select(Session))
        assert stored.token_hash != token and len(stored.token_hash) == 64
        assert db.scalar(select(User)).password_hash.startswith("$argon2id$")


def test_signup_rules(client):
    signup(client)
    again = client.post("/api/auth/signup", json={"name": "X", "email": "SAM@example.com", "password": STRONG_PASSWORD})
    assert again.status_code == 409
    weak = client.post("/api/auth/signup", json={"name": "X", "email": "x@example.com", "password": "password123"})
    assert weak.status_code == 422 and "too common" in weak.json()["error"]
    short = client.post("/api/auth/signup", json={"name": "X", "email": "y@example.com", "password": "abc"})
    assert short.status_code == 422


def test_login_logout(client):
    signup(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    wrong = client.post("/api/auth/login", json={"email": "sam@example.com", "password": "not-the-password"})
    assert wrong.status_code == 401
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever-123"})
    assert unknown.status_code == 401 and unknown.json()["error"] == wrong.json()["error"]  # no account hints
    ok = client.post("/api/auth/login", json={"email": "Sam@Example.com", "password": STRONG_PASSWORD})
    assert ok.status_code == 200 and client.get("/api/auth/me").status_code == 200


def test_login_is_rate_limited(client):
    signup(client)
    statuses = [client.post("/api/auth/login", json={"email": "sam@example.com", "password": "wrong-pass"}).status_code
                for _ in range(10)]
    assert statuses[:8] == [401] * 8 and statuses[8] == 429


def test_password_reset(client, monkeypatch):
    sent = {}
    monkeypatch.setattr("jenius.api.routes.auth.send_password_reset",
                        lambda settings, to, name, link: sent.update(to=to, link=link))
    signup(client)
    old_cookie = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()

    assert client.post("/api/auth/password-reset/request", json={"email": "nobody@example.com"}).status_code == 200
    assert not sent  # no email for unknown addresses, same answer
    client.post("/api/auth/password-reset/request", json={"email": "sam@example.com"})
    assert sent["link"].startswith("http://testserver/#reset=")
    token = sent["link"].split("#reset=")[1]

    done = client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "a-brand-new-secret"})
    assert done.status_code == 200
    reused = client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "another-new-secret"})
    assert reused.status_code == 400
    new_cookie = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, old_cookie)
    assert client.get("/api/auth/me").status_code == 401  # old sessions were signed out
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, new_cookie)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/login", json={"email": "sam@example.com", "password": "a-brand-new-secret"}).status_code == 200


def test_delete_account_removes_everything(client, database):
    signup(client)
    wrong = client.request("DELETE", "/api/auth/account", json={"password": "nope-nope-nope"})
    assert wrong.status_code == 401
    assert client.request("DELETE", "/api/auth/account", json={"password": STRONG_PASSWORD}).status_code == 200
    with database.sessions() as db:
        assert db.scalar(select(User)) is None and db.scalar(select(Session)) is None
