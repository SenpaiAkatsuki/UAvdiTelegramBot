from aiogram.filters.callback_data import CallbackData

"""
Menu callback schema.

Defines callback payload structure and screen ids for inline menu navigation.
"""

SCOPE_USER = "u"
SCOPE_ADMIN = "a"

VIEW_USER_ROOT = "root"
VIEW_PROFILE = "profile"
VIEW_LIBRARY_TOPICS = "lib_t"
VIEW_LIBRARY_ARTICLES = "lib_a"
VIEW_LIBRARY_ARTICLE = "lib_v"

VIEW_ADMIN_ROOT = "admin_root"
VIEW_ADMIN_MANAGEMENT = "admin_management"
VIEW_ADMIN_PENDING = "pending"
VIEW_ADMIN_ACTIVE = "active"
VIEW_ADMIN_EXPIRING = "expiring"
VIEW_ADMIN_EXPIRED = "expired"
VIEW_ADMIN_USER_DETAIL = "user_detail"
VIEW_ADMIN_SUBSCRIPTION_PRICE = "subscription_price"
VIEW_ADMIN_APPROVE_PENDING = "approve_pending"
VIEW_ADMIN_EXPIRING_SETTINGS = "expiring_settings"
VIEW_ADMIN_VOTING_SETTINGS = "voting_settings"
VIEW_ADMIN_ADD_ADMIN = "add_admin"
VIEW_ADMIN_BROADCAST = "broadcast"
VIEW_ADMIN_LIBRARY_TOPICS = "al_t"
VIEW_ADMIN_LIBRARY_ARTICLES = "al_a"
VIEW_ADMIN_LIBRARY_ARTICLE = "al_v"
VIEW_ADMIN_LIBRARY_ADD_TOPIC = "al_at"
VIEW_ADMIN_LIBRARY_EDIT_TOPIC = "al_et"
VIEW_ADMIN_LIBRARY_DELETE_TOPIC = "al_dt"
VIEW_ADMIN_LIBRARY_ADD_ARTICLE = "al_aa"
VIEW_ADMIN_LIBRARY_EDIT_ARTICLE = "al_ea"
VIEW_ADMIN_LIBRARY_DELETE_ARTICLE = "al_da"


class MenuCallbackData(CallbackData, prefix="menu"):
    # Unified callback payload used by all menu screens.
    scope: str
    view: str
    page: int = 0
    target_user_id: int = 0
    back_view: str = VIEW_USER_ROOT
