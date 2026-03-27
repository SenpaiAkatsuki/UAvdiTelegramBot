from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgbot.callbacks.voting import (
    ApplicationVoteCallbackData,
    VOTE_DECISION_APPROVE,
    VOTE_DECISION_REJECT,
)

"""
Application voting keyboards.

Provides inline approve/reject buttons with live vote counters.
"""


def application_vote_keyboard(
    application_id: int,
    yes_count: int,
    no_count: int,
) -> InlineKeyboardMarkup:
    # Build inline voting keyboard with current counts.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Підтвердити ({yes_count})",
                    callback_data=ApplicationVoteCallbackData(
                        application_id=application_id,
                        decision=VOTE_DECISION_APPROVE,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"❌ Відхилити ({no_count})",
                    callback_data=ApplicationVoteCallbackData(
                        application_id=application_id,
                        decision=VOTE_DECISION_REJECT,
                    ).pack(),
                ),
            ]
        ]
    )
