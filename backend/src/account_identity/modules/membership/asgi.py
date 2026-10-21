"""Optional ASGI profile; core-only deployments use account_identity.api.main:app."""
from account_identity.api.main import create_api
from account_identity.modules.membership.sqlite_provider import SQLiteMembershipProvider


app = create_api(membership_factory=SQLiteMembershipProvider)
