"""OIDC back-channel logout token validation using the issuer's fixed JWKS URL."""
import asyncio
import base64
import json
import time
from urllib.parse import urlsplit

import httpx2 as httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry


_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
class JWKSUnavailable(Exception):
    """The issuer's signing keys cannot currently be verified."""


class BackchannelLogoutVerifier:
    def __init__(self, issuer, client_id, jwks_uri=None, jwks_loader=None,
                 cache_seconds=300, refresh_cooldown=10, allow_insecure_local=False):
        if not issuer or not client_id or (not jwks_uri and not jwks_loader):
            raise ValueError("issuer, client id and JWKS source are required")
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.jwks_uri = jwks_uri
        self.jwks_loader = jwks_loader
        self.cache_seconds = cache_seconds
        self.refresh_cooldown = refresh_cooldown
        self.allow_insecure_local = allow_insecure_local
        self._jwks = None
        self._keys = None
        self._expires_at = 0
        self._last_unknown_kid_refresh = 0
        self._lock = None

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _load_jwks(self):
        try:
            if self.jwks_loader:
                data = await self.jwks_loader()
            else:
                parsed = urlsplit(self.jwks_uri)
                local_http = (self.allow_insecure_local and parsed.scheme == "http"
                              and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
                if (parsed.scheme != "https" and not local_http) or not parsed.hostname:
                    raise ValueError("JWKS URI must use HTTPS")
                async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
                    response = await client.get(self.jwks_uri)
                    response.raise_for_status()
                    if len(response.content) > 1024 * 1024:
                        raise ValueError("JWKS response is too large")
                    data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise JWKSUnavailable("issuer keys unavailable") from error
        keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(keys, list) or not keys or len(keys) > 100:
            raise JWKSUnavailable("invalid JWKS document")
        seen = set()
        for key in keys:
            if (not isinstance(key, dict) or key.get("kty") != "RSA"
                    or key.get("use", "sig") != "sig"
                    or key.get("alg", "RS256") != "RS256"
                    or not isinstance(key.get("kid"), str) or not key["kid"]
                    or key["kid"] in seen or "d" in key):
                raise JWKSUnavailable("unsupported JWKS signing key")
            seen.add(key["kid"])
        self._jwks = data
        self._keys = KeySet.import_key_set(data)
        self._expires_at = time.monotonic() + self.cache_seconds

    async def _ensure_jwks(self):
        if self._jwks is not None and time.monotonic() < self._expires_at:
            return
        async with self._get_lock():
            if self._jwks is None or time.monotonic() >= self._expires_at:
                await self._load_jwks()

    async def verify(self, token):
        if not isinstance(token, str) or len(token) > 16384:
            raise ValueError("invalid logout token")
        await self._ensure_jwks()
        try:
            decoded = jwt.decode(token, self._keys, algorithms=["RS256"])
        except Exception as first_error:
            header = self._read_header(token)
            kid = header.get("kid")
            known_kids = {key.get("kid") for key in self._jwks["keys"]}
            now = time.monotonic()
            if (not kid or kid in known_kids
                    or now - self._last_unknown_kid_refresh < self.refresh_cooldown):
                raise ValueError("invalid logout signature") from first_error
            async with self._get_lock():
                if now - self._last_unknown_kid_refresh >= self.refresh_cooldown:
                    self._last_unknown_kid_refresh = now
                    await self._load_jwks()
            try:
                decoded = jwt.decode(token, self._keys, algorithms=["RS256"])
            except Exception as error:
                raise ValueError("invalid logout signature") from error
        header = decoded.header
        claims = decoded.claims
        if header.get("alg") != "RS256" or any(name in header for name in ("jku", "x5u")):
            raise ValueError("untrusted logout token header")
        if header.get("typ") not in (None, "JWT", "logout+jwt"):
            raise ValueError("invalid logout token type")
        registry = JWTClaimsRegistry(
            now=int(time.time()),
            leeway=0,
            iss={"essential": True, "value": self.issuer},
            aud={"essential": True, "values": [self.client_id]},
            iat={"essential": True},
            exp={"essential": True},
            jti={"essential": True},
        )
        try:
            registry.validate(claims)
        except Exception as error:
            raise ValueError("invalid logout token claims") from error
        current = int(time.time())
        if (claims["exp"] - claims["iat"] > 300
                or current - claims["iat"] > 300):
            raise ValueError("logout token lifetime is too long")
        if "nonce" in claims:
            raise ValueError("logout token cannot contain nonce")
        if not claims.get("sub") and not claims.get("sid"):
            raise ValueError("logout token requires sub or sid")
        if (claims.get("sub") is not None and not isinstance(claims.get("sub"), str)
                or claims.get("sid") is not None and not isinstance(claims.get("sid"), str)):
            raise ValueError("invalid logout session identifier")
        events = claims.get("events")
        if not isinstance(events, dict) or not isinstance(events.get(_LOGOUT_EVENT), dict):
            raise ValueError("logout event claim is missing")
        if not isinstance(claims.get("jti"), str) or not claims["jti"] or len(claims["jti"]) > 255:
            raise ValueError("invalid logout token id")
        return dict(claims)

    @staticmethod
    def _read_header(token):
        try:
            encoded = token.split(".", 1)[0]
            if len(encoded) > 4096:
                return {}
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError, UnicodeError):
            return {}
