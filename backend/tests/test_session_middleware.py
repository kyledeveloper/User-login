from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from account_identity.api.session_middleware import ServerSessionMiddleware
from account_identity.application.session_store import SQLiteSessionStore


def test_cookie_contains_only_random_id_and_session_stays_server_side(tmp_path):
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    app = FastAPI()

    @app.get("/login-complete")
    async def complete(request: Request):
        request.session["sub"] = "account-subject-123"
        request.state.rotate_session = True
        return {"ok": True}

    @app.get("/me")
    async def me(request: Request):
        return {"sub": request.session.get("sub")}

    app.add_middleware(ServerSessionMiddleware, store=store, secure=True)
    client = TestClient(app, base_url="https://testserver")
    response = client.get("/login-complete")
    cookie = response.headers["set-cookie"]
    cookie_value = cookie.split(";", 1)[0].split("=", 1)[1]
    assert "account-subject-123" not in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
    assert client.get("/me").json() == {"sub": "account-subject-123"}
    assert store.load(cookie_value)["sub"] == "account-subject-123"


def test_session_rotation_invalidates_old_id(tmp_path):
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    old_id = store.new_id()
    store.save(old_id, {"sub": "pre-auth"})
    app = FastAPI()

    @app.get("/rotate")
    async def rotate(request: Request):
        request.session["sub"] = "post-auth"
        request.state.rotate_session = True
        return {"ok": True}

    app.add_middleware(ServerSessionMiddleware, store=store)
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set("account_session", old_id, path="/")
    response = client.get("/rotate")
    new_id = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    assert new_id != old_id
    assert store.load(old_id) == {}
    assert store.load(new_id)["sub"] == "post-auth"
