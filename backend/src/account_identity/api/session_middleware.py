"""Minimal server-side ASGI session middleware for Authlib state and RP sessions."""
from http.cookies import SimpleCookie
from starlette.responses import Response


class ServerSessionMiddleware:
    def __init__(self, app, store, cookie_name="account_session", secure=True):
        self.app = app
        self.store = store
        self.cookie_name = cookie_name
        self.secure = secure

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get(b"cookie", b"").decode("latin1"))
        except Exception:
            cookie = SimpleCookie()
        morsel = cookie.get(self.cookie_name)
        original_id = morsel.value if morsel else None
        session_id = original_id
        session = self.store.load(original_id)
        scope.setdefault("state", {})
        scope["session"] = session
        scope["state"]["rotate_session"] = False

        async def send_with_session(message):
            if message["type"] == "http.response.start":
                rotate = scope["state"].get("rotate_session", False)
                if rotate:
                    self.store.delete(original_id)
                    session_id = self.store.new_id() if session else None
                elif session:
                    session_id = original_id or self.store.new_id()
                else:
                    session_id = None
                    self.store.delete(original_id)
                response_headers = list(message.get("headers", []))
                if scope.get("path", "").startswith("/api/"):
                    response_headers.append((b"cache-control", b"no-store"))
                if session_id:
                    saved = self.store.save(session_id, session)
                    if saved:
                        value = f"{self.cookie_name}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={self.store.ttl_seconds}"
                        if self.secure:
                            value += "; Secure"
                    else:
                        value = f"{self.cookie_name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                        if self.secure:
                            value += "; Secure"
                else:
                    value = f"{self.cookie_name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                    if self.secure:
                        value += "; Secure"
                response_headers.append((b"set-cookie", value.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_with_session)
