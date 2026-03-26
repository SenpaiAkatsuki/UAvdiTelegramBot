from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

"""
Demo inline keyboards.

Contains simple demo menu keyboard and order callback-data based list keyboard.
"""


def very_simple_keyboard() -> InlineKeyboardMarkup:
    # Legacy static keyboard version for demo.
    buttons = [
        [
            InlineKeyboardButton(text="Create order", callback_data="create_order"),
            InlineKeyboardButton(text="My orders", callback_data="my_orders"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def simple_menu_keyboard() -> InlineKeyboardMarkup:
    # Builder-based keyboard for demo menu actions.
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Create order", callback_data="create_order")
    keyboard.button(text="My orders", callback_data="my_orders")
    return keyboard.as_markup()


class OrderCallbackData(CallbackData, prefix="order"):
    # Callback payload for selecting specific demo order row.
    order_id: int


def my_orders_keyboard(orders: list[dict]) -> InlineKeyboardMarkup:
    # Build keyboard from provided demo orders list.
    keyboard = InlineKeyboardBuilder()
    for order in orders:
        keyboard.button(
            text=f"Order: {order['title']}",
            callback_data=OrderCallbackData(order_id=order["id"]),
        )
    return keyboard.as_markup()
