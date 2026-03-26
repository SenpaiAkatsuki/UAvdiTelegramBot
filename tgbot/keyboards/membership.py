from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

"""
Membership flow keyboards.

Buttons for application entry, payment actions, binding flow, and group access.
"""


def application_entry_keyboard(
    application_url: str,
    self_bind_enabled: bool = True,
) -> InlineKeyboardMarkup:
    # Main entry keyboard with website form link and optional self-bind action.
    kb = InlineKeyboardBuilder()
    kb.button(text="Fill application on website", url=application_url)
    if self_bind_enabled:
        kb.button(
            text="I already applied on the website",
            callback_data="membership_site_applied",
        )
    kb.adjust(1)
    return kb.as_markup()


def payment_keyboard(pay_button_text: str = "Pay membership") -> InlineKeyboardMarkup:
    # Payment action keyboard with pay and status-check buttons.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=pay_button_text,
                    callback_data="membership_pay",
                )
            ],
            [
                InlineKeyboardButton(
                    text="I paid, check status",
                    callback_data="membership_check_payment_status",
                )
            ],
        ]
    )


def group_access_keyboard() -> InlineKeyboardMarkup:
    # One-button keyboard to request group invite link.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Get group access",
                    callback_data="membership_get_group_access",
                )
            ]
        ]
    )


def bind_confirmation_keyboard() -> InlineKeyboardMarkup:
    # Ask admins for manual confirmation of binding.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Request bind confirmation",
                    callback_data="membership_bind_confirmation_request",
                )
            ]
        ]
    )


def bind_back_keyboard() -> InlineKeyboardMarkup:
    # Return from bind phone step to previous action.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Back",
                    callback_data="membership_bind_back",
                )
            ],
        ]
    )
