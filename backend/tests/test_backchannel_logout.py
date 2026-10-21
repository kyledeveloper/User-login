import asyncio
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from joserfc import jwt, jwk

from account_identity.application.backchannel_logout import BackchannelLogoutVerifier
from account_identity.application.session_store import SQLiteSessionStore


@pytest.fixture
def issuer_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo)
    public_jwk = jwk.import_key(
        public_pem, "RSA", parameters={"kid": "current-key", "use": "sig", "alg": "RS256"}
    )
    return private_pem, {"keys": [public_jwk.as_dict()]}


def signed_logout(private_key, **overrides):
    now = int(time.time())
    claims = {"iss": "https://id.example/realms/shared", "aud": "alpha", "iat": now,
              "exp": now + 60, "jti": "logout-1", "sub": "user-1", "sid": "sso-1",
              "events": {"http://schemas.openid.net/event/backchannel-logout": {}}}
    claims.update(overrides)
    return jwt.encode({"alg": "RS256", "kid": "current-key", "typ": "logout+jwt"},
                      claims, jwk.import_key(private_key, "RSA"))


async def _value(data):
    return data


def test_verifier_accepts_only_signed_logout_purpose_and_expected_audience(issuer_keys):
    private, jwks = issuer_keys
    verifier = BackchannelLogoutVerifier(
        "https://id.example/realms/shared", "alpha", jwks_loader=lambda: _value(jwks))
    claims = asyncio.run(verifier.verify(signed_logout(private)))
    assert claims["sub"] == "user-1" and claims["sid"] == "sso-1"


@pytest.mark.parametrize("changes", [
    {"aud": "beta"},
    {"iss": "https://evil.example"},
    {"nonce": "wrong-token-purpose"},
    {"events": {}},
    {"sub": None, "sid": None},
    {"exp": int(time.time()) - 10},
    {"exp": int(time.time()) + 600, "iat": int(time.time()) - 10},
])
def test_verifier_rejects_invalid_or_cross_purpose_tokens(issuer_keys, changes):
    private, jwks = issuer_keys
    verifier = BackchannelLogoutVerifier(
        "https://id.example/realms/shared", "alpha", jwks_loader=lambda: _value(jwks))
    with pytest.raises(ValueError):
        asyncio.run(verifier.verify(signed_logout(private, **changes)))


def test_verified_logout_revokes_matching_sessions_once(tmp_path):
    store = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3"))
    first = store.new_id()
    second = store.new_id()
    other_user = store.new_id()
    store.save(first, {"sub": "user-1", "sid": "sso-1"})
    store.save(second, {"sub": "user-1", "sid": "sso-2"})
    store.save(other_user, {"sub": "user-2", "sid": "sso-1"})
    now = int(time.time())
    assert store.apply_backchannel_logout("logout-1", "user-1", "sso-1", now + 60, now=now) == 1
    assert store.load(first) == {}
    assert store.load(second)["sub"] == "user-1"
    assert store.load(other_user)["sub"] == "user-2"
    assert store.apply_backchannel_logout("logout-1", "user-1", "sso-1", now + 60, now=now) == 0
