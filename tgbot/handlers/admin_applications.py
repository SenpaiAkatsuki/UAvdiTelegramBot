from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from tgbot.filters.admin import AdminFilter

"""
Admin application callback guards.

Legacy inline moderation callbacks are disabled in favor of poll-based flow.
"""

admin_applications_router = Router()
admin_applications_router.callback_query.filter(AdminFilter())


@admin_applications_router.callback_query(F.data.startswith("admin_application_"))
@admin_applications_router.callback_query(F.data.startswith("admin_unlinked_"))
async def admin_application_callbacks_disabled(query: CallbackQuery) -> None:
    # Inform admin that old inline moderation buttons are disabled.
    await query.answer(
        "Inline admin moderation is disabled. Use group voting poll results.",
        show_alert=True,
    )
    if query.message is not None:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            return
