from __future__ import annotations

from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from tgbot.config import Config
from tgbot.db.repo import PostgresRepo
from tgbot.filters.admin import AdminFilter
from tgbot.keyboards.membership import group_access_keyboard, payment_keyboard
from tgbot.services.menu_state import (
    MENU_STATE_ACTION,
    clear_tracked_keyboard,
    remember_tracked_message,
)

"""
Payment handlers (LiqPay).

Creates payment links, checks payment status, and supports admin test payment command.
"""

payments_router = Router()

async def remove_callback_keyboard(query: CallbackQuery) -> None:
    # Remove source inline keyboard after callback handling.
    if query.message is None:
        return
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        return


async def remember_action_message_for_user(tg_user_id: int, sent: Message) -> None:
    # Save action message id for future cleanup.
    remember_tracked_message(
        state=MENU_STATE_ACTION,
        tg_user_id=tg_user_id,
        chat_id=sent.chat.id,
        message_id=sent.message_id,
    )


async def send_tracked_action_message_from_query(
    query: CallbackQuery,
    *,
    text: str,
    reply_markup=None,
) -> None:
    # Send action message from callback context and track it.
    if query.from_user is None:
        return
    await clear_tracked_keyboard(
        bot=query.bot,
        state=MENU_STATE_ACTION,
        tg_user_id=query.from_user.id,
    )
    if query.message is None:
        sent = await query.bot.send_message(
            chat_id=query.from_user.id,
            text=text,
            reply_markup=reply_markup,
        )
    else:
        sent = await query.message.answer(text, reply_markup=reply_markup)
    if reply_markup is not None:
        await remember_action_message_for_user(query.from_user.id, sent)


async def get_latest_application_for_user(
    repo: PostgresRepo,
    tg_user_id: int,
) -> dict | None:
    # Load latest application record for user.
    async with repo.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM applications
            WHERE tg_user_id = $1
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            tg_user_id,
        )
    return dict(row) if row else None


async def get_latest_open_payment_for_application(
    repo: PostgresRepo,
    application_id: int,
) -> dict | None:
    # Load latest pending LiqPay payment for application.
    async with repo.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM payments
            WHERE application_id = $1
              AND provider = 'liqpay'
              AND status = 'PENDING'
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            application_id,
        )
    return dict(row) if row else None


async def get_latest_payment_for_application(
    repo: PostgresRepo,
    application_id: int,
) -> dict | None:
    # Load latest LiqPay payment regardless of status.
    async with repo.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM payments
            WHERE application_id = $1
              AND provider = 'liqpay'
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            application_id,
        )
    return dict(row) if row else None


def is_application_payment_eligible(application: dict) -> bool:
    # Check if application status allows creating payment.
    status = application.get("status")
    if status == "APPROVED_AWAITING_PAYMENT":
        return application.get("vote_status") == "PROCESSED"
    return status in {"PAID_AWAITING_JOIN", "ACTIVE_MEMBER"}


async def create_or_reuse_liqpay_payment(
    repo: PostgresRepo,
    config: Config,
    application_id: int,
    force_new: bool = False,
) -> dict:
    # Reuse valid pending payment or create a new one with runtime amount.
    runtime_amount_minor = await repo.get_subscription_price_minor(
        default_minor=int(config.liqpay.amount_minor)
    )
    existing_payment = await get_latest_open_payment_for_application(
        repo=repo,
        application_id=application_id,
    )
    if (
        existing_payment
        and not force_new
        and existing_payment["status"] == "PENDING"
        and int(existing_payment["amount_minor"]) == int(runtime_amount_minor)
        and str(existing_payment["currency"]).upper() == str(config.liqpay.currency).upper()
        and str(existing_payment.get("provider_order_id") or "").strip()
    ):
        return existing_payment

    if existing_payment and existing_payment["status"] == "PENDING":
        await repo.update_payment_status(
            payment_id=int(existing_payment["id"]),
            new_status="CANCELED",
        )

    return await repo.create_payment(
        application_id=application_id,
        amount_minor=int(runtime_amount_minor),
        currency=config.liqpay.currency,
        provider="liqpay",
        provider_order_id=f"liqpay_{uuid4().hex}",
        status="PENDING",
    )


def parse_int_argument(message: Message) -> int | None:
    # Parse command numeric argument: /pay <application_id>.
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    value = parts[1].strip()
    if not value.isdigit():
        return -1
    return int(value)


@payments_router.message(AdminFilter(), Command("pay"))
async def admin_test_pay_link(
    message: Message,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Admin-only helper to create a direct LiqPay test link.
    if message.from_user is None:
        await message.answer("Unable to identify user.", parse_mode=None)
        return

    if not config.payments.enabled:
        await message.answer(
            "Payments are temporarily unavailable (PAYMENTS_ENABLED=false).",
            parse_mode=None,
        )
        return

    target_application_id: int
    status_hint: str

    arg_application_id = parse_int_argument(message)
    if arg_application_id == -1:
        await message.answer("Usage: /pay [application_id_number]", parse_mode=None)
        return

    if arg_application_id is None:
        application = await get_latest_application_for_user(repo, message.from_user.id)
        if application is None:
            await repo.create_or_update_user(
                tg_user_id=message.from_user.id,
                full_name=message.from_user.full_name,
                username=message.from_user.username,
                language_code=message.from_user.language_code,
            )
            application = await repo.create_manual_application(
                tg_user_id=message.from_user.id,
                applicant_name=message.from_user.full_name,
                status="APPROVED_AWAITING_PAYMENT",
            )
            status_hint = f"{application.get('status', '-')} (auto-created test application)"
        else:
            status_hint = str(application.get("status") or "-")
        target_application_id = int(application["id"])
    else:
        application = await repo.get_application_by_id(arg_application_id)
        if application is None:
            await message.answer(
                f"Application not found: {arg_application_id}",
                parse_mode=None,
            )
            return
        target_application_id = int(application["id"])
        status_hint = str(application.get("status") or "-")

    payment = await create_or_reuse_liqpay_payment(
        repo=repo,
        config=config,
        application_id=target_application_id,
        force_new=True,
    )
    amount_uah = f"{int(payment['amount_minor']) / 100:.2f}"
    pay_url = config.liqpay.build_pay_url(int(payment["id"]))
    await message.answer(
        "Admin test payment link created.\n"
        f"application_id={target_application_id}, status={status_hint}\n"
        f"payment_id={int(payment['id'])}, payment_status={payment['status']}, amount={amount_uah} UAH\n\n"
        f"{pay_url}\n\n"
        "Note: this command bypasses normal membership payment eligibility checks.",
        parse_mode=None,
    )


@payments_router.callback_query(F.data == "membership_pay")
async def start_membership_payment(
    query: CallbackQuery,
    repo: PostgresRepo,
    config: Config,
) -> None:
    # Create/reuse payment and send checkout link to user.
    await query.answer()

    if query.from_user is None:
        if query.message is not None:
            await query.message.answer("Unable to identify user.")
            await remove_callback_keyboard(query)
        return

    if not config.payments.enabled:
        await send_tracked_action_message_from_query(
            query,
            text="Payments are temporarily unavailable.",
        )
        await remove_callback_keyboard(query)
        return

    application = await get_latest_application_for_user(repo, query.from_user.id)
    if application is None:
        await send_tracked_action_message_from_query(
            query,
            text="Application not found. Please contact admin.",
        )
        await remove_callback_keyboard(query)
        return

    if not is_application_payment_eligible(application):
        await send_tracked_action_message_from_query(
            query,
            text=f"Payment is not available for your current status: {application['status']}.",
        )
        await remove_callback_keyboard(query)
        return

    payment = await create_or_reuse_liqpay_payment(
        repo=repo,
        config=config,
        application_id=int(application["id"]),
    )

    pay_url = config.liqpay.build_pay_url(int(payment["id"]))
    await send_tracked_action_message_from_query(
        query,
        text=(
            "Open payment page:\n"
            f"{pay_url}\n\n"
            "After successful payment, tap \"I paid, check status\"."
        ),
        reply_markup=payment_keyboard(),
    )
    await remove_callback_keyboard(query)


@payments_router.callback_query(F.data == "membership_check_payment_status")
async def check_membership_payment_status(
    query: CallbackQuery,
    repo: PostgresRepo,
) -> None:
    # Re-check latest payment state and return next action to user.
    await query.answer()

    if query.from_user is None:
        if query.message is not None:
            await query.message.answer("Unable to identify user.")
            await remove_callback_keyboard(query)
        return

    application = await get_latest_application_for_user(repo, query.from_user.id)
    if application is None:
        await send_tracked_action_message_from_query(
            query,
            text="Application not found. Please contact admin.",
        )
        await remove_callback_keyboard(query)
        return

    latest_payment = await get_latest_payment_for_application(
        repo=repo,
        application_id=int(application["id"]),
    )
    if latest_payment is None:
        await send_tracked_action_message_from_query(
            query,
            text="No payment record found yet. Tap Pay membership first.",
            reply_markup=payment_keyboard(),
        )
        await remove_callback_keyboard(query)
        return

    payment_status = latest_payment["status"]
    if payment_status == "PENDING":
        await send_tracked_action_message_from_query(
            query,
            text="Payment is still pending LiqPay callback. Please wait and check again.",
            reply_markup=payment_keyboard(),
        )
        await remove_callback_keyboard(query)
        return

    status = application["status"]
    if status == "PAID_AWAITING_JOIN":
        await send_tracked_action_message_from_query(
            query,
            text="Payment is confirmed. If button is missing, send /start.",
            reply_markup=group_access_keyboard(),
        )
        await remove_callback_keyboard(query)
        return
    if payment_status == "PAID" or status == "ACTIVE_MEMBER":
        if query.from_user is not None:
            await clear_tracked_keyboard(
                bot=query.bot,
                state=MENU_STATE_ACTION,
                tg_user_id=query.from_user.id,
            )
        if query.message is not None:
            await query.message.answer("Payment is confirmed. Membership is active.")
        await remove_callback_keyboard(query)
        return

    await send_tracked_action_message_from_query(
        query,
        text=f"Latest payment status: {payment_status}. Please try payment again.",
        reply_markup=payment_keyboard(),
    )
    await remove_callback_keyboard(query)
