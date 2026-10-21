"""Opaque, server-side RP sessions with random identifiers and expiry."""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time


class SQLiteSessionStore:
    _SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")

    def __init__(self, database_path, ttl_seconds=28800, max_lifetime_seconds=2592000):
        self.database_path = database_path
        self.ttl_seconds = ttl_seconds
        self.max_lifetime_seconds = max_lifetime_seconds
        parent = os.path.dirname(os.path.abspath(database_path))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions ("
                       "session_hash TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                       "created_at INTEGER NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if "created_at" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
            if "subject" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN subject TEXT")
            if "provider_sid" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN provider_sid TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS logout_jtis ("
                       "jti TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)")
        os.chmod(database_path, 0o600)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5, isolation_level="IMMEDIATE")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @staticmethod
    def _hash(session_id):
        if not SQLiteSessionStore._SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("invalid session id")
        return hashlib.sha256(session_id.encode("ascii")).hexdigest()

    def load(self, session_id, now=None):
        if not isinstance(session_id, str) or not SQLiteSessionStore._SESSION_ID_PATTERN.fullmatch(session_id):
            return {}
        with self._connect() as db:
            row = db.execute("SELECT payload, created_at, expires_at FROM sessions WHERE session_hash=?",
                             (self._hash(session_id),)).fetchone()
        current = int(time.time() if now is None else now)
        if row is None or row[2] <= current or row[1] + self.max_lifetime_seconds <= current:
            return {}
        try:
            payload = json.loads(row[0])
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    def save(self, session_id, payload, now=None):
        if not isinstance(session_id, str) or not SQLiteSessionStore._SESSION_ID_PATTERN.fullmatch(session_id) or not isinstance(payload, dict):
            raise ValueError("invalid session")
        current = int(time.time() if now is None else now)
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (current,))
            session_hash = self._hash(session_id)
            existing = db.execute("SELECT created_at FROM sessions WHERE session_hash=?",
                                  (session_hash,)).fetchone()
            created_at = existing[0] if existing else current
            if created_at + self.max_lifetime_seconds <= current:
                db.execute("DELETE FROM sessions WHERE session_hash=?", (session_hash,))
                return False
            expiry = min(current + self.ttl_seconds, created_at + self.max_lifetime_seconds)
            db.execute("INSERT INTO sessions(session_hash, payload, created_at, expires_at, subject, provider_sid) "
                       "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_hash) DO UPDATE SET "
                       "payload=excluded.payload, expires_at=excluded.expires_at, "
                       "subject=excluded.subject, provider_sid=excluded.provider_sid",
                       (session_hash, encoded, created_at, expiry, payload.get("sub"), payload.get("sid")))
        return True

    def delete(self, session_id):
        if not isinstance(session_id, str) or not SQLiteSessionStore._SESSION_ID_PATTERN.fullmatch(session_id):
            return
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE session_hash=?", (self._hash(session_id),))

    def apply_backchannel_logout(self, jti, subject, provider_sid, token_expiry, now=None):
        if (not isinstance(jti, str) or not jti or len(jti) > 255
                or (not subject and not provider_sid)):
            raise ValueError("invalid logout event")
        current = int(time.time() if now is None else now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM logout_jtis WHERE expires_at<=?", (current,))
            try:
                db.execute("INSERT INTO logout_jtis(jti, expires_at) VALUES (?, ?)",
                           (jti, token_expiry))
            except sqlite3.IntegrityError:
                return 0
            if provider_sid:
                cursor = db.execute("DELETE FROM sessions WHERE provider_sid=? "
                                    "AND (? IS NULL OR subject=?)",
                                    (provider_sid, subject, subject))
            else:
                cursor = db.execute("DELETE FROM sessions WHERE subject=?", (subject,))
            return cursor.rowcount

    @staticmethod
    def new_id():
        return secrets.token_urlsafe(32)
