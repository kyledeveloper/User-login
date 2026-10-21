"""Validate fixed OIDC and browser destinations at application composition."""
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    post_logout_uri: str
    app_origin: str
    app_home: str
    cookie_secure: bool


def _parse_absolute_url(name, value):
    parsed = urlsplit(value)
    if (not parsed.scheme or not parsed.netloc or parsed.username or parsed.password
            or parsed.fragment):
        raise ValueError("{} must be an absolute URL without credentials or fragment".format(name))
    return parsed


def is_trusted_logout_endpoint(endpoint, issuer, require_https):
    """Accept only logout endpoints on the configured issuer origin."""
    try:
        endpoint_url = _parse_absolute_url("logout endpoint", endpoint)
        issuer_url = _parse_absolute_url("issuer", issuer)
    except (TypeError, ValueError):
        return False
    return (endpoint_url.scheme == issuer_url.scheme
            and endpoint_url.netloc == issuer_url.netloc
            and (not require_https or endpoint_url.scheme == "https"))


def validate_settings(env):
    names = ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
             "OIDC_REDIRECT_URI", "OIDC_POST_LOGOUT_URI", "APP_ORIGIN", "APP_HOME")
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise ValueError("missing required settings: {}".format(", ".join(missing)))
    secure_value = env.get("COOKIE_SECURE", "true").lower()
    if secure_value not in {"true", "false"}:
        raise ValueError("COOKIE_SECURE must be exactly true or false")
    secure = secure_value == "true"
    issuer = env["OIDC_ISSUER"].rstrip("/")
    urls = {
        "OIDC_ISSUER": _parse_absolute_url("OIDC_ISSUER", issuer),
        "OIDC_REDIRECT_URI": _parse_absolute_url("OIDC_REDIRECT_URI", env["OIDC_REDIRECT_URI"]),
        "OIDC_POST_LOGOUT_URI": _parse_absolute_url(
            "OIDC_POST_LOGOUT_URI", env["OIDC_POST_LOGOUT_URI"]),
        "APP_ORIGIN": _parse_absolute_url("APP_ORIGIN", env["APP_ORIGIN"]),
        "APP_HOME": _parse_absolute_url("APP_HOME", env["APP_HOME"]),
    }
    origin = urls["APP_ORIGIN"]
    if origin.path not in ("", "/") or origin.query:
        raise ValueError("APP_ORIGIN must contain only scheme and host")
    if (urls["APP_HOME"].scheme, urls["APP_HOME"].netloc) != (origin.scheme, origin.netloc):
        raise ValueError("APP_HOME must remain on APP_ORIGIN")
    if (urls["OIDC_POST_LOGOUT_URI"].scheme, urls["OIDC_POST_LOGOUT_URI"].netloc) != (
            origin.scheme, origin.netloc):
        raise ValueError("OIDC_POST_LOGOUT_URI must remain on APP_ORIGIN")
    parsed_urls = list(urls.values())
    if secure:
        if any(url.scheme != "https" for url in parsed_urls):
            raise ValueError("HTTPS is required when COOKIE_SECURE=true")
    else:
        loopback_hosts = {"localhost", "127.0.0.1", "::1"}
        if any(url.hostname not in loopback_hosts for url in parsed_urls):
            raise ValueError("insecure cookies are allowed only for loopback development")
    return OIDCSettings(
        issuer=issuer,
        client_id=env["OIDC_CLIENT_ID"],
        client_secret=env["OIDC_CLIENT_SECRET"],
        redirect_uri=env["OIDC_REDIRECT_URI"],
        post_logout_uri=env["OIDC_POST_LOGOUT_URI"],
        app_origin="{}://{}".format(origin.scheme, origin.netloc),
        app_home=env["APP_HOME"],
        cookie_secure=secure,
    )
