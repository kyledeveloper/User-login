from account_identity.application.oidc_config import (
    is_trusted_logout_endpoint, validate_settings,
)


def local_settings():
    return {
        "OIDC_ISSUER": "http://localhost:8080/realms/shared",
        "OIDC_CLIENT_ID": "account-portal",
        "OIDC_CLIENT_SECRET": "local-dev-client-secret",
        "OIDC_REDIRECT_URI": "http://localhost:8000/auth/callback",
        "OIDC_POST_LOGOUT_URI": "http://localhost:5173/",
        "APP_ORIGIN": "http://localhost:5173",
        "APP_HOME": "http://localhost:5173/",
        "COOKIE_SECURE": "false",
    }


def test_local_http_configuration_is_accepted_only_for_loopback():
    assert validate_settings(local_settings()).issuer == "http://localhost:8080/realms/shared"
    unsafe = local_settings()
    unsafe["APP_ORIGIN"] = "http://portal.example"
    unsafe["APP_HOME"] = "http://portal.example/"
    try:
        validate_settings(unsafe)
        assert False, "non-loopback HTTP should be rejected"
    except ValueError:
        pass


def test_post_logout_destination_cannot_leave_application_origin():
    unsafe = local_settings()
    unsafe["APP_HOME"] = "https://attacker.example/"
    try:
        validate_settings(unsafe)
        assert False, "cross-origin post-login redirect should be rejected"
    except ValueError:
        pass


def test_confidential_client_requires_secret():
    unsafe = local_settings()
    unsafe["OIDC_CLIENT_SECRET"] = ""
    try:
        validate_settings(unsafe)
        assert False, "missing confidential client secret should be rejected"
    except ValueError:
        pass


def test_logout_endpoint_must_match_issuer_scheme_and_authority():
    assert is_trusted_logout_endpoint(
        "https://id.example/logout", "https://id.example/realms/shared", True)
    assert not is_trusted_logout_endpoint(
        "http://id.example/logout", "https://id.example/realms/shared", True)
    assert not is_trusted_logout_endpoint(
        "https://other.example/logout", "https://id.example/realms/shared", True)
    assert is_trusted_logout_endpoint(
        "http://localhost:8080/logout", "http://localhost:8080/realms/shared", False)
