"""The five Campus Customs agents."""

from . import accounting, boss, customer_service, facilities, inventory

HELPERS = {
    "inventory": inventory,
    "accounting": accounting,
    "facilities": facilities,
    "customer_service": customer_service,
}

__all__ = ["boss", "inventory", "accounting", "facilities", "customer_service", "HELPERS"]
