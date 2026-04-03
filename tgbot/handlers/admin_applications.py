from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from tgbot.callbacks.voting import (
    ApplicationVoteCallbackData,
    VOTE_DECISION_APPROVE,
)
from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.services.application_voting import (
    VOTE_CAST_CLOSED,
    VOTE_CAST_EXPIRED,
    VOTE_CAST_NOT_FOUND,
    VOTE_CAST_OK,
    cast_admin_vote,
    close_due_votes,
    finalize_vote_if_target_reached,
    refresh_vote_message_markup,
)

"""
Admin application voting callbacks.

Handles inline approve/reject vote buttons in one-message group vote flow.
"""

admin_applications_router = Router()


def _is_admin(config: Config, tg_user_id: int) -> bool:
    # Check whether user id is in configured admin list.
    return tg_user_id in config.tg_bot.admin_ids


@admin_applications_router.callback_query(ApplicationVoteCallbackData.filter())
async def handle_application_vote_callback(
    query: CallbackQuery,
    callback_data: ApplicationVoteCallbackData,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Accept admin vote, persist it, and refresh inline counters.
    if query.from_user is None:
        await query.answer()
        return

    if not _is_admin(config, query.from_user.id):
        await query.answer(
            "Only admins can vote.",
            show_alert=True,
        )
        return

    approve = callback_data.decision == VOTE_DECISION_APPROVE
    cast_status, application_row = await cast_admin_vote(
        repo,
        application_id=callback_data.application_id,
        tg_user_id=query.from_user.id,
        approve=approve,
    )

    if cast_status == VOTE_CAST_NOT_FOUND:
        await query.answer("Application not found.", show_alert=True)
        if query.message is not None:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
        return

    if cast_status in {VOTE_CAST_CLOSED, VOTE_CAST_EXPIRED}:
        await query.answer("Voting is already closed.", show_alert=True)
        if cast_status == VOTE_CAST_EXPIRED:
            await close_due_votes(
                bot=query.bot,
                config=config,
                repo=repo,
            )
        return

    if cast_status == VOTE_CAST_OK and application_row is not None:
        finalized = await finalize_vote_if_target_reached(
            bot=query.bot,
            config=config,
            repo=repo,
            application_row=application_row,
        )
        if finalized:
            await query.answer("Ваш голос зараховано. Рішення прийнято.")
            return

        await refresh_vote_message_markup(
            bot=query.bot,
            application_row=application_row,
        )
        await query.answer("Your vote has been counted.")
        return

    await query.answer("Failed to process vote.", show_alert=True)


@admin_applications_router.callback_query(F.data.startswith("admin_application_"))
@admin_applications_router.callback_query(F.data.startswith("admin_unlinked_"))
async def admin_legacy_application_callbacks(query: CallbackQuery) -> None:
    # Keep old disabled callback namespace explicit for backward compatibility.
    await query.answer(
        "Legacy button format. Use active voting buttons in application message.",
        show_alert=True,
    )
