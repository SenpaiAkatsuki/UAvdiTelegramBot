from .admin import admin_router
from .admin_applications import admin_applications_router
from .binding import binding_router
from .group_access import group_access_router
from .menu import menu_router
from .membership import membership_router
from .payments import payments_router

"""
Handlers package router registry.

Exports routers_list in required include order.
"""

routers_list = [
    admin_router,
    membership_router,
    menu_router,
    binding_router,
    admin_applications_router,
    payments_router,
    group_access_router,
]

__all__ = [
    "routers_list",
]
