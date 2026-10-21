from fastapi.testclient import TestClient

from account_identity.api.main import create_api
from account_identity.application.session_store import SQLiteSessionStore


def configure(monkeypatch, tmp_path):
    values = {
        "OIDC_ISSUER": "https://id.example/realms/shared",
        "OIDC_CLIENT_ID": "portal",
        "OIDC_CLIENT_SECRET": "test-client-secret",
        "OIDC_REDIRECT_URI": "https://app.example/auth/callback",
        "OIDC_POST_LOGOUT_URI": "https://app.example/",
        "APP_ORIGIN": "https://app.example",
        "APP_HOME": "https://app.example/",
        "SESSION_DB": str(tmp_path / "sessions.sqlite3"),
        "COOKIE_SECURE": "true",
        "ENABLE_MEMBERSHIP": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_identity_api_requires_a_server_session(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    client = TestClient(create_api())
    assert client.get("/health/ready").json() == {"ready": True}
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/csrf").status_code == 401


def test_session_responses_are_never_cacheable(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    sid = store.new_id()
    store.save(sid, {"sub": "user-1", "iss": "https://id.example/realms/shared",
                     "csrf": "csrf-token", "email": "user@example.com"})
    client = TestClient(create_api(), base_url="https://app.example")
    client.cookies.set("account_session", sid, domain="app.example")
    assert client.get("/api/me").headers["cache-control"] == "no-store"
    assert client.get("/api/csrf").headers["cache-control"] == "no-store"


def test_logout_rejects_cross_origin_even_with_session_cookie(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    sid = store.new_id()
    store.save(sid, {"sub": "user-1", "iss": "https://id.example/realms/shared",
                     "csrf": "expected-token"})
    client = TestClient(create_api(), base_url="https://app.example")
    client.cookies.set("account_session", sid, domain="app.example")
    response = client.post("/auth/logout", headers={"origin": "https://attacker.example",
                                    "x-csrf-token": "expected-token"})
    assert response.status_code == 403


def test_membership_claims_are_bound_to_bff_client_and_failure_is_closed(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("OIDC_CLIENT_ID", "alpha")
    monkeypatch.setenv("ENABLE_MEMBERSHIP", "true")

    class Provider:
        def __init__(self):
            self.calls = []

        def claims_for(self, client_id, subject):
            self.calls.append((client_id, subject))
            raise RuntimeError("offline")

    provider = Provider()
    app = create_api(membership_factory=lambda _: provider)
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    sid = store.new_id()
    store.save(sid, {"sub": "user-1", "iss": "https://id.example/realms/shared"})
    client = TestClient(app, base_url="https://app.example")
    client.cookies.set("account_session", sid, domain="app.example")
    response = client.get("/api/membership?client_id=beta")
    assert response.status_code == 200
    assert response.json() == {}
    assert provider.calls == [("alpha", "user-1")]
    assert client.get("/api/me").status_code == 200


def test_backchannel_logout_route_revokes_only_verified_session(monkeypatch, tmp_path):
    import time
    configure(monkeypatch, tmp_path)

    class Verifier:
        async def verify(self, token):
            assert token == "signed-logout-token"
            return {"jti": "logout-event-1", "sub": "user-1", "sid": "provider-session-1",
                    "exp": int(time.time()) + 60}

    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    first, second = store.new_id(), store.new_id()
    store.save(first, {"sub": "user-1", "sid": "provider-session-1"})
    store.save(second, {"sub": "user-1", "sid": "provider-session-2"})
    client = TestClient(create_api(logout_verifier=Verifier()))
    response = client.post("/auth/backchannel-logout", data={"logout_token": "signed-logout-token"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert store.load(first) == {}
    assert store.load(second)["sub"] == "user-1"


def test_backchannel_logout_rejects_json_body(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    client = TestClient(create_api())
    assert client.post("/auth/backchannel-logout", json={"logout_token": "x"}).status_code == 400
