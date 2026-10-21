"""Optional client-scoped plan configuration store; product usage stays local."""
import sqlite3
import os
import time
from datetime import datetime, timezone


class SQLiteMembershipProvider:
    def __init__(self, database_path):
        self.database_path = database_path
        os.makedirs(os.path.dirname(os.path.abspath(database_path)), mode=0o700, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS memberships ("
                        "client_id TEXT NOT NULL, subject TEXT NOT NULL, tier TEXT NOT NULL, "
                       "expires_at INTEGER, banked_pro_days INTEGER NOT NULL DEFAULT 0, "
                       "referral_expires_at INTEGER, "
                       "PRIMARY KEY (client_id, subject))")
            columns = {row[1] for row in db.execute("PRAGMA table_info(memberships)")}
            if "referral_expires_at" not in columns:
                db.execute("ALTER TABLE memberships ADD COLUMN referral_expires_at INTEGER")
            db.execute("CREATE TABLE IF NOT EXISTS invitations ("
                       "invite_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, inviter_sub TEXT NOT NULL, "
                       "created_at INTEGER NOT NULL, redeemed_sub TEXT UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS referral_awards ("
                       "invite_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, inviter_sub TEXT NOT NULL, "
                       "redeemed_sub TEXT NOT NULL UNIQUE, awarded_at INTEGER NOT NULL, days INTEGER NOT NULL)")
        os.chmod(database_path, 0o600)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5, isolation_level="IMMEDIATE")
        db.row_factory = sqlite3.Row
        return db

    def set_tier(self, client_id, subject, tier, expires_at=None, now=None):
        if tier not in {"free", "pro", "elite"}:
            raise ValueError("unsupported membership tier")
        current = int(time.time() if now is None else now)
        with self._connect() as db:
            previous = db.execute("SELECT tier, expires_at, referral_expires_at "
                                  "FROM memberships WHERE client_id=? AND subject=?",
                                  (client_id, subject)).fetchone()
            referral_expiry = previous["referral_expires_at"] if previous else None
            if (tier == "free" and previous and previous["tier"] == "pro"
                    and previous["expires_at"] is not None and previous["expires_at"] > current
                    and referral_expiry is not None and referral_expiry > previous["expires_at"]):
                referral_expiry = current + referral_expiry - previous["expires_at"]
            db.execute("INSERT INTO memberships(client_id, subject, tier, expires_at) "
                       "VALUES (?, ?, ?, ?) ON CONFLICT(client_id, subject) DO UPDATE SET "
                       "tier=excluded.tier, expires_at=excluded.expires_at, "
                       "referral_expires_at=COALESCE(?, memberships.referral_expires_at)",
                       (client_id, subject, tier, expires_at, referral_expiry))

    def claims_for(self, client_id, subject, now=None):
        current = int(time.time() if now is None else now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT tier, expires_at, banked_pro_days, referral_expires_at "
                             "FROM memberships "
                             "WHERE client_id=? AND subject=?", (client_id, subject)).fetchone()
            if row is None:
                return {}
            if row["tier"] == "elite" and (row["expires_at"] is None or row["expires_at"] > current):
                return {"membership_tier": "elite", "membership_expires_at": row["expires_at"],
                        "membership_banked_pro_days": row["banked_pro_days"]}
            manual_active = row["tier"] == "pro" and (
                row["expires_at"] is None or row["expires_at"] > current)
            if manual_active:
                active_expiries = [value for value in (row["expires_at"], row["referral_expires_at"])
                                   if value is not None and value > current]
                return {"membership_tier": "pro",
                        "membership_expires_at": max(active_expiries) if active_expiries else None,
                        "membership_banked_pro_days": row["banked_pro_days"]}
            referral_expiry = row["referral_expires_at"]
            if row["banked_pro_days"]:
                referral_expiry = max(current, referral_expiry or current) + row["banked_pro_days"] * 86400
                db.execute("UPDATE memberships SET referral_expires_at=?, banked_pro_days=0 "
                           "WHERE client_id=? AND subject=?",
                           (referral_expiry, client_id, subject))
            referral_active = referral_expiry is not None and referral_expiry > current
            if referral_active:
                expiries = [value for value in (row["expires_at"] if manual_active else None,
                                                referral_expiry if referral_active else None)
                            if value is not None]
                return {"membership_tier": "pro",
                        "membership_expires_at": max(expiries) if expiries else None,
                        "membership_banked_pro_days": row["banked_pro_days"]}
            return {"membership_tier": "free", "membership_expires_at": None,
                    "membership_banked_pro_days": 0}

    def create_invitation(self, invite_id, client_id, inviter_sub, created_at):
        with self._connect() as db:
            db.execute("INSERT INTO invitations(invite_id, client_id, inviter_sub, created_at) "
                       "VALUES (?, ?, ?, ?)", (invite_id, client_id, inviter_sub, created_at))

    def award_verified_invitation(self, invite_id, client_id, account_sub,
                                  account_created_at, email_verified, now):
        """Qualify and award once in one transaction using trusted IdP account facts."""
        identifiers = (invite_id, client_id, account_sub)
        valid_identifiers = all(isinstance(value, str) and 0 < len(value) <= 255
                                for value in identifiers)
        valid_timestamps = all(isinstance(value, int) and not isinstance(value, bool)
                               and 0 <= value <= 253402300799
                               for value in (account_created_at, now))
        if (not valid_identifiers or not valid_timestamps or email_verified is not True
                or account_created_at > now + 60):
            return False
        current_month = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            invitation = db.execute("SELECT * FROM invitations WHERE invite_id=?", (invite_id,)).fetchone()
            if (invitation is None or invitation["client_id"] != client_id
                    or invitation["inviter_sub"] == account_sub
                    or account_created_at < invitation["created_at"]
                    or invitation["redeemed_sub"] is not None):
                return False
            prior_award = db.execute("SELECT 1 FROM referral_awards WHERE redeemed_sub=?",
                                     (account_sub,)).fetchone()
            if prior_award:
                return False
            earned = db.execute("SELECT COALESCE(SUM(days), 0) FROM referral_awards "
                                "WHERE client_id=? AND inviter_sub=? "
                                "AND strftime('%Y-%m', awarded_at, 'unixepoch')=?",
                                (client_id, invitation["inviter_sub"], current_month)).fetchone()[0]
            if earned + 7 > 30:
                return False
            db.execute("INSERT INTO referral_awards VALUES (?, ?, ?, ?, ?, 7)",
                       (invite_id, client_id, invitation["inviter_sub"], account_sub, now))
            db.execute("UPDATE invitations SET redeemed_sub=? WHERE invite_id=?",
                       (account_sub, invite_id))
            member = db.execute("SELECT tier, expires_at, banked_pro_days, referral_expires_at "
                                "FROM memberships WHERE client_id=? AND subject=?",
                                (client_id, invitation["inviter_sub"])).fetchone()
            bank_reward = member and (
                member["tier"] == "elite" and (member["expires_at"] is None or member["expires_at"] > now)
                or member["tier"] == "pro" and member["expires_at"] is None)
            if bank_reward:
                db.execute("UPDATE memberships SET banked_pro_days=banked_pro_days+7 "
                           "WHERE client_id=? AND subject=?",
                           (client_id, invitation["inviter_sub"]))
            else:
                base = max(now, member["referral_expires_at"] or now) if member else now
                if member and member["tier"] == "pro" and member["expires_at"] and member["expires_at"] > now:
                    base = max(base, member["expires_at"])
                if member and member["banked_pro_days"]:
                    base += member["banked_pro_days"] * 86400
                db.execute("INSERT INTO memberships(client_id, subject, tier, referral_expires_at) "
                           "VALUES (?, ?, 'free', ?) ON CONFLICT(client_id, subject) DO UPDATE SET "
                           "referral_expires_at=excluded.referral_expires_at, banked_pro_days=0",
                           (client_id, invitation["inviter_sub"], base + 7 * 86400))
            return True
