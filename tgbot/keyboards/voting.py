from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tgbot.callbacks.voting import (
    ApplicationVoteContactCallbackData,
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
    include_vote_buttons: bool = True,
    contact_url: str | None = None,
) -> InlineKeyboardMarkup:
    # Build inline voting keyboard with counters and manual-contact helper.
    rows: list[list[InlineKeyboardButton]] = []
    if include_vote_buttons:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ За ({yes_count})",
                    callback_data=ApplicationVoteCallbackData(
                        application_id=application_id,
                        decision=VOTE_DECISION_APPROVE,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"❌ Проти ({no_count})",
                    callback_data=ApplicationVoteCallbackData(
                        application_id=application_id,
                        decision=VOTE_DECISION_REJECT,
                    ).pack(),
                ),
            ]
        )

    rows.append(
        [
            (
                InlineKeyboardButton(
                    text="📞 Зв’язатися вручну",
                    url=contact_url,
                )
                if contact_url
                else InlineKeyboardButton(
                    text="📞 Зв’язатися вручну",
                    callback_data=ApplicationVoteContactCallbackData(
                        application_id=application_id,
                    ).pack(),
                )
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)
