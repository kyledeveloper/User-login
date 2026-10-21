"""Core composition; optional member capabilities are injected at this edge."""


class CoreApplication:
    def __init__(self, membership_provider):
        self.membership_provider = membership_provider
        self.core_ready = True

    def safe_membership_claims(self, client_id, subject):
        provider = self.membership_provider
        if provider is None:
            return {}
        try:
            claims = provider.claims_for(client_id, subject)
            return claims if isinstance(claims, dict) else {}
        except Exception:
            # Privileged claims fail closed while identity functions remain available.
            return {}


def build_app(membership_provider=None):
    return CoreApplication(membership_provider)
