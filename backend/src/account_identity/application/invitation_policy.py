"""Pure referral eligibility rules; caller must supply verified IdP facts."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Invitation:
    client_id: str
    inviter_sub: str
    created_at: int


def qualifies_for_referral(invitation, account_sub, account_created_at,
                           email_verified, registered_client_id):
    return bool(
        invitation.client_id == registered_client_id
        and invitation.inviter_sub != account_sub
        and email_verified
        and account_created_at >= invitation.created_at
    )
