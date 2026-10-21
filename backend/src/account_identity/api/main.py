"""Example relying-party BFF backed by the shared Keycloak issuer."""
import os
import secrets
import hmac
from urllib.parse import urlencode, parse_qs
import httpx2 as httpx

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from account_identity.bootstrap import build_app
from account_identity.application.session_store import SQLiteSessionStore
from account_identity.application.oidc_config import (
    is_trusted_logout_endpoint, validate_settings,
)
from account_identity.application.backchannel_logout import BackchannelLogoutVerifier, JWKSUnavailable
from account_identity.api.session_middleware import ServerSessionMiddleware


def create_api(membership_factory=None, logout_verifier=None):
    settings = validate_settings(os.environ)
    issuer = settings.issuer
    membership = None
    if os.environ.get("ENABLE_MEMBERSHIP") == "true":
        if membership_factory is None:
            raise RuntimeError("membership is enabled but no provider factory was composed")
        membership = membership_factory(os.environ.get("MEMBERSHIP_DB", "membership.sqlite3"))
    core = build_app(membership)
    sessions = SQLiteSessionStore(os.environ.get("SESSION_DB", "sessions.sqlite3"))
    verifier = logout_verifier or BackchannelLogoutVerifier(
        issuer, settings.client_id,
        jwks_uri=issuer + "/protocol/openid-connect/certs",
        allow_insecure_local=not settings.cookie_secure)
    oauth = OAuth()
    oauth.register(
        name="identity", client_id=settings.client_id,
        client_secret=settings.client_secret,
        server_metadata_url=issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
    )
    app = FastAPI()
    app.add_middleware(ServerSessionMiddleware, store=sessions, secure=settings.cookie_secure)
    app.state.core = core

    @app.get("/auth/login")
    async def login(request: Request):
        callback = settings.redirect_uri
        return await oauth.identity.authorize_redirect(request, callback,
                                                        code_challenge_method="S256")

    @app.get("/auth/callback")
    async def callback(request: Request):
        try:
            token = await oauth.identity.authorize_access_token(request)
            claims = token.get("userinfo")
        except Exception as exc:
            request.session.clear()
            raise HTTPException(status_code=401, detail="identity sign-in failed") from exc
        if not claims or not claims.get("sub") or claims.get("iss") != issuer.rstrip("/"):
            request.session.clear()
            raise HTTPException(status_code=401, detail="invalid identity claims")
        if claims.get("email_verified") is not True:
            request.session.clear()
            raise HTTPException(status_code=403, detail="email verification required")
        request.session.clear()
        request.session["sub"] = claims["sub"]
        request.session["iss"] = claims["iss"]
        request.session["sid"] = claims.get("sid")
        request.session["email"] = claims.get("email") if claims.get("email_verified") else None
        request.session["csrf"] = secrets.token_urlsafe(32)
        request.state.rotate_session = True
        return RedirectResponse(settings.app_home, status_code=303)

    @app.get("/api/me")
    async def me(request: Request):
        subject = request.session.get("sub")
        if not subject or request.session.get("iss") != issuer.rstrip("/"):
            raise HTTPException(status_code=401, detail="not signed in")
        return {"sub": subject, "email": request.session.get("email")}

    @app.get("/api/membership")
    async def membership_claims(request: Request):
        subject = request.session.get("sub")
        if not subject or request.session.get("iss") != issuer.rstrip("/"):
            raise HTTPException(status_code=401, detail="not signed in")
        return core.safe_membership_claims(settings.client_id, subject)

    @app.get("/api/csrf")
    async def csrf(request: Request):
        if not request.session.get("sub"):
            raise HTTPException(status_code=401, detail="not signed in")
        return {"token": request.session["csrf"]}

    @app.post("/auth/logout")
    async def logout(request: Request):
        expected_origin = settings.app_origin.rstrip("/")
        origin = request.headers.get("origin", "").rstrip("/")
        supplied_csrf = request.headers.get("x-csrf-token", "")
        if not expected_origin or origin != expected_origin or not hmac.compare_digest(
                supplied_csrf, request.session.get("csrf", "")):
            raise HTTPException(status_code=403, detail="request verification failed")
        metadata = await oauth.identity.load_server_metadata()
        logout_endpoint = metadata.get("end_session_endpoint")
        if not logout_endpoint:
            raise HTTPException(status_code=503, detail="identity logout unavailable")
        if not is_trusted_logout_endpoint(logout_endpoint, issuer, settings.cookie_secure):
            raise HTTPException(status_code=503, detail="identity logout unavailable")
        request.session.clear()
        params = {"client_id": settings.client_id,
                  "post_logout_redirect_uri": settings.post_logout_uri}
        request.state.rotate_session = True
        return {"logout_url": logout_endpoint + "?" + urlencode(params)}

    @app.post("/auth/backchannel-logout")
    async def backchannel_logout(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            raise HTTPException(status_code=400, detail="invalid logout request")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32768:
                raise HTTPException(status_code=400, detail="invalid logout request")
        try:
            values = parse_qs(bytes(body).decode("ascii"), keep_blank_values=True, max_num_fields=4)
        except (UnicodeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid logout request")
        tokens = values.get("logout_token", [])
        if len(tokens) != 1 or not tokens[0]:
            raise HTTPException(status_code=400, detail="invalid logout request")
        try:
            claims = await verifier.verify(tokens[0])
        except (httpx.HTTPError, JWKSUnavailable) as error:
            raise HTTPException(status_code=503, detail="identity keys unavailable") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid logout token") from error
        sessions.apply_backchannel_logout(claims["jti"], claims.get("sub"), claims.get("sid"),
                                          claims["exp"])
        return {"ok": True}

    @app.get("/health/ready")
    async def ready():
        return {"ready": core.core_ready}

    return app


app = create_api() if os.environ.get("OIDC_ISSUER") else FastAPI()
