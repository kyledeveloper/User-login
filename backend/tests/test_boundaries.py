from account_identity.application.invitation_policy import Invitation, qualifies_for_referral
from account_identity.bootstrap import build_app
from account_identity.modules.membership.sqlite_provider import SQLiteMembershipProvider


def test_keycloak_demo_client_is_confidential_pkce_and_exact_redirect():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    realm = json.loads((root / "config/realm-export.json").read_text())
    client = realm["clients"][0]
    assert client["standardFlowEnabled"]
    assert not client["publicClient"]
    assert not client["directAccessGrantsEnabled"]
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["redirectUris"] == ["http://localhost:8000/auth/callback"]
    assert client["attributes"]["backchannel.logout.session.required"] == "true"
    assert client["attributes"]["backchannel.logout.url"] == "http://localhost:8000/auth/backchannel-logout"
    assert realm["revokeRefreshToken"] is True
    assert realm["verifyEmail"] is True
    assert realm["resetPasswordAllowed"] is True
    assert "email" in client["defaultClientScopes"]
    assert "*" not in client["redirectUris"][0]


def test_invitation_is_bound_to_registered_client_and_new_verified_account():
    invite = Invitation(client_id="alpha", inviter_sub="user-1", created_at=100)
    assert qualifies_for_referral(invite, account_sub="user-2", account_created_at=101,
                                  email_verified=True, registered_client_id="alpha")
    assert not qualifies_for_referral(invite, account_sub="user-2", account_created_at=99,
                                     email_verified=True, registered_client_id="alpha")
    assert not qualifies_for_referral(invite, account_sub="user-2", account_created_at=101,
                                     email_verified=False, registered_client_id="alpha")
    assert not qualifies_for_referral(invite, account_sub="user-2", account_created_at=101,
                                     email_verified=True, registered_client_id="beta")
    assert not qualifies_for_referral(invite, account_sub="user-1", account_created_at=101,
                                     email_verified=True, registered_client_id="alpha")


def test_core_boots_without_optional_membership_module():
    app = build_app(membership_provider=None)
    assert app.membership_provider is None
    assert app.core_ready


def test_core_api_imports_when_optional_membership_package_is_absent(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "account_identity"
    target = tmp_path / "account_identity"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("membership"))
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    subprocess.run([sys.executable, "-c", "import account_identity.api.main"],
                   env=env, check=True, capture_output=True, text=True)


def test_member_provider_failure_does_not_break_identity_or_grant_claims():
    class BrokenProvider:
        def claims_for(self, client_id, subject):
            raise RuntimeError("unavailable")

    app = build_app(membership_provider=BrokenProvider())
    assert app.core_ready
    assert app.safe_membership_claims("alpha", "user-2") == {}


def test_referral_awards_are_idempotent_scoped_and_full_seven_days(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.set_tier("alpha", "inviter", "pro", 1_000_000)
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert provider.award_verified_invitation("i1", "alpha", "new-user", 101, True, 200)
    assert not provider.award_verified_invitation("i1", "alpha", "new-user", 101, True, 200)
    assert provider.claims_for("alpha", "inviter", now=200)["membership_expires_at"] == 1_604_800
    assert provider.claims_for("beta", "inviter") == {}


def test_referral_rejects_unverified_existing_and_cross_client_accounts(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert not provider.award_verified_invitation("i1", "alpha", "u1", 101, False, 200)
    assert not provider.award_verified_invitation("i1", "beta", "u1", 101, True, 200)
    assert not provider.award_verified_invitation("i1", "alpha", "u1", 99, True, 200)
    assert not provider.award_verified_invitation("i1", "alpha", "inviter", 101, True, 200)


def test_referral_requires_literal_boolean_for_verified_email(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    for index, value in enumerate(("false", 1, [], None)):
        invite_id = "invite-{}".format(index)
        provider.create_invitation(invite_id, "alpha", "inviter", 100)
        assert not provider.award_verified_invitation(
            invite_id, "alpha", "new-{}".format(index), 101, value, 200)


def test_referral_rejects_malformed_trusted_identity_facts(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert not provider.award_verified_invitation("i1", "alpha", "new", "101", True, 200)
    assert not provider.award_verified_invitation("i1", "alpha", None, 101, True, 200)
    assert not provider.award_verified_invitation("i1", "alpha", "new", True, True, 200)
    assert not provider.award_verified_invitation("i1", "alpha", "new", 10 ** 100, True, 200)


def test_referral_monthly_cap_is_full_award_only_and_resets_in_utc_month(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    for number in range(5):
        invite_id = "invite-{}".format(number)
        provider.create_invitation(invite_id, "alpha", "inviter", 100)
        awarded = provider.award_verified_invitation(invite_id, "alpha", "new-{}".format(number),
                                                     101, True, 1_735_689_600 + number)
        assert awarded is (number < 4)
    provider.create_invitation("next-month", "alpha", "inviter", 100)
    assert provider.award_verified_invitation("next-month", "alpha", "new-next", 101, True,
                                              1_738_368_000)


def test_elite_referral_credits_are_banked_until_elite_expiry(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.set_tier("alpha", "inviter", "elite", 1000)
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert provider.award_verified_invitation("i1", "alpha", "new-user", 101, True, 200)
    assert provider.claims_for("alpha", "inviter", now=200)["membership_banked_pro_days"] == 7


def test_operator_cancellation_does_not_erase_earned_referral_time(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert provider.award_verified_invitation("i1", "alpha", "new-user", 101, True, 200)
    provider.set_tier("alpha", "inviter", "free", now=300)
    claims = provider.claims_for("alpha", "inviter", now=201)
    assert claims["membership_tier"] == "pro"
    assert claims["membership_expires_at"] == 605_000


def test_elite_banked_reward_starts_after_expiry_or_operator_cancellation(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.set_tier("alpha", "inviter", "elite", 1000)
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert provider.award_verified_invitation("i1", "alpha", "new-user", 101, True, 200)
    assert provider.claims_for("alpha", "inviter", now=1001)["membership_expires_at"] == 605801


def test_permanent_manual_pro_banks_referrals_and_cancellation_activates_them(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.set_tier("alpha", "inviter", "pro")
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert provider.award_verified_invitation("i1", "alpha", "new-user", 101, True, 200)
    assert provider.claims_for("alpha", "inviter", now=201)["membership_banked_pro_days"] == 7
    provider.set_tier("alpha", "inviter", "free")
    claims = provider.claims_for("alpha", "inviter", now=300)
    assert claims["membership_tier"] == "pro"
    assert claims["membership_expires_at"] == 300 + 7 * 86400


def test_cancelling_manual_pro_activates_unstarted_referral_time(tmp_path):
    provider = SQLiteMembershipProvider(str(tmp_path / "members.sqlite3"))
    provider.set_tier("alpha", "inviter", "pro", 10_000)
    provider.create_invitation("i1", "alpha", "inviter", 100)
    assert provider.award_verified_invitation("i1", "alpha", "new-user", 101, True, 200)
    provider.set_tier("alpha", "inviter", "free", now=3000)
    claims = provider.claims_for("alpha", "inviter", now=3000)
    assert claims["membership_expires_at"] == 3000 + 7 * 86400
