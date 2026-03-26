from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.formatting import as_key_value, as_marked_list, as_section

from tgbot.keyboards.inline import (
    OrderCallbackData,
    my_orders_keyboard,
    simple_menu_keyboard,
)

"""
Simple demo menu handlers.

Provides lightweight inline navigation example for creating and viewing demo orders.
"""

menu_router = Router()


ORDERS = [
    {"id": 1, "title": "Order 1", "status": "In progress"},
    {"id": 2, "title": "Order 2", "status": "Done"},
    {"id": 3, "title": "Order 3", "status": "Done"},
]


@menu_router.message(Command("menu"))
async def show_menu(message: Message):
    # Open simple demo menu.
    await message.answer("Choose menu action:", reply_markup=simple_menu_keyboard())


@menu_router.callback_query(F.data == "create_order")
async def create_order(query: CallbackQuery):
    # Handle "create order" demo action.
    await query.answer()
    if query.message is not None:
        await query.message.answer("You selected create order.")


@menu_router.callback_query(F.data == "my_orders")
async def my_orders(query: CallbackQuery):
    # Show demo orders list keyboard.
    await query.answer()
    if query.message is not None:
        await query.message.edit_text(
            "You selected order list.",
            reply_markup=my_orders_keyboard(ORDERS),
        )


@menu_router.callback_query(OrderCallbackData.filter())
async def show_order(query: CallbackQuery, callback_data: OrderCallbackData):
    # Render selected order detail.
    await query.answer()
    if query.message is None:
        return

    order_id = callback_data.order_id
    order_info = next((order for order in ORDERS if order["id"] == order_id), None)
    if not order_info:
        await query.message.edit_text("Order not found.")
        return

    text = as_section(
        as_key_value("Order #", order_info["id"]),
        as_marked_list(
            as_key_value("Title", order_info["title"]),
            as_key_value("Status", order_info["status"]),
        ),
    )
    await query.message.edit_text(text.as_html(), parse_mode=ParseMode.HTML)
