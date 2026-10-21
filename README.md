# User-login: shared identity starter

[简体中文](README.zh-CN.md)

This repository is a reusable React account portal and Python relying-party backend-for-frontend (BFF) for projects that share one account system. A self-hosted Keycloak realm is the sole owner of credentials, registration, email verification, password recovery, OIDC authorization codes, signing keys, and SSO sessions. Each relying project registers a separate confidential client and exact callback URLs in Keycloak. Projects use issuer-scoped `sub` values; email is a profile claim, never an account key.

The checked-in Docker Compose setup is for local development only. It starts Keycloak 26.8.1, PostgreSQL and Mailpit on loopback. Do not expose it to the internet or reuse its local administrator credentials in production. The imported realm is an initial seed; Keycloak does not overwrite an existing realm when the import file changes.

## Local development

Requirements: Docker Compose, Python 3.11+, Node.js 22.12+.

1. Start the local identity provider and mailbox: `docker compose up -d`.
2. In the Keycloak admin console at `http://localhost:8080`, sign in with the local-only bootstrap account shown in `docker-compose.yml`. Open realm `shared`, client `account-portal`, and copy its generated client secret into `backend/.env`.
3. Copy `backend/.env.example` to `backend/.env`. Keep the local `COOKIE_SECURE=false` only for loopback HTTP. Never use that setting outside local development.
4. Install and run the BFF from `backend/`:

   ```sh
   python -m venv ../.venv
   ../.venv/bin/pip install -r requirements.lock
   ../.venv/bin/uvicorn account_identity.api.main:app --host 127.0.0.1 --port 8000 --env-file .env
   ```

5. In another terminal run `npm ci && npm run dev` from `frontend/`; open `http://localhost:5173`. The Vite proxy forwards `/auth` and `/api` to the BFF. Complete signup and email verification through Keycloak; Mailpit is at `http://localhost:8025`.

The BFF uses Authorization Code with PKCE S256, validated OIDC ID tokens, server-side SQLite sessions, opaque `HttpOnly` cookies, session rotation after login, same-origin plus CSRF checks for logout, and the provider's RP-initiated logout endpoint. Tokens are not persisted in the browser or BFF session database. Production must use HTTPS, `COOKIE_SECURE=true`, a durable private session database, a fixed issuer URL, secure secret storage, TLS termination, backups, rate limits, and an allowlisted registered callback. Do not enable broad CORS.

## Optional membership profile

Core startup does not import the membership package. For the optional client-scoped membership profile, set `ENABLE_MEMBERSHIP=true` and run `account_identity.modules.membership.asgi:app`. The composition entry injects the provider; its SQLite file is private and each membership/invitation is keyed by the server-configured client and immutable `sub`. Products retain ownership of actual usage and enforcement. The `/api/membership` endpoint reads claims for the BFF's configured client; callers cannot choose another client in request data. Operator authorization and assignment UI must be composed by each project using that project's verified Operator role; this starter deliberately does not create a global admin credential or shared Operator role.

The provider implements full seven-day referral awards, a 30-day monthly cap that rejects the next full award instead of issuing a partial reward, Pro extension, Elite banking, account-level exactly-once awards, client isolation, and preservation of earned referral time when an Operator changes the manual tier. **Automatic referral qualification is not wired to Keycloak yet.** Before enabling awards in production, add a trusted server-side registration reconciliation adapter that obtains the immutable account creation timestamp and verified-email state from Keycloak Admin API or a reviewed event listener, authenticates its service account narrowly, retries idempotently, and never accepts those facts from browser input. Until that adapter exists, the invitation award method must only be called by trusted server code.

## Layout and ownership

- `config/realm-export.json` and Keycloak own identity credentials, email verification, recovery, issuer, client registry and protocol lifecycles.
- `backend/src/account_identity/api` is the Python FastAPI BFF boundary. `application/session_store.py` owns RP session persistence. It stores only random session-ID hashes in the cookie and keeps session data in SQLite.
- `backend/src/account_identity/modules/membership` is optional and owns client-scoped plan and referral state. `bootstrap.py` accepts a narrow provider capability and fails closed for membership claims without blocking identity flows.
- `frontend/src` is React-only product-facing UI; synchronized language packs live in `frontend/locales/{en-US,zh-CN}/common.json`.
- `backend/tests` and `frontend/src/*.test.tsx` cover security boundaries. Tests use temporary SQLite files and do not contact Keycloak.

There is no import from the optional membership profile into core startup. Keycloak remains the only client registry and identity store; this BFF does not create a second credential store. No source from the trading application, market data, user database, local cache, or Git history is included.

## Validation

```sh
cd backend && PYTHONPATH=src ../.venv/bin/python -m pytest -q tests
cd ../frontend && npm test && npm run build
```

The unit suite runs offline. It does not replace a live Keycloak acceptance run, production recovery/backup exercise, independent infrastructure review, or implementation of the trusted referral event adapter.
