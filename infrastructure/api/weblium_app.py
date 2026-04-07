from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from ipaddress import ip_address, ip_network
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
)
from aiohttp import web

from tgbot.config import Config, load_config
from tgbot.db.init import init_db, shutdown_db
from tgbot.db.repo import PostgresRepo
from tgbot.keyboards.membership import group_access_keyboard
from tgbot.services.application_voting import (
    build_application_vote_text,
    start_vote,
)
from tgbot.services.notify import notify_user

"""
Webhook HTTP application.

Handles Weblium form ingestion, LiqPay checkout/callback flow, and related DB updates.
"""

logger = logging.getLogger("weblium_webhook_app")

PRIMARY_WEBLIUM_PATH = "/webhooks/weblium/application"
PRIMARY_LIQPAY_CALLBACK_PATH = "/webhooks/liqpay/callback"
PRIMARY_LIQPAY_PAY_PATH = "/pay/liqpay/{payment_id}"
BIND_TOKEN_TTL_DAYS = 7


def _as_string(value: Any) -> str | None:
    # Normalize value to non-empty stripped string.
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value).strip() or None


def _ensure_absolute_url(url: str | None) -> str | None:
    # Normalize protocol-relative URLs to absolute https URL.
    if not url:
        return None
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _get_client_ip(request: web.Request) -> str | None:
    # Resolve client IP, preferring X-Forwarded-For when present.
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote


def _is_content_type_valid(request: web.Request) -> bool:
    # `request.content_type` strips charset, so this supports
    # `application/json; charset=utf-8` automatically.
    return request.content_type.lower() == "application/json"


def _extract_secret_candidate(request: web.Request, payload: Mapping[str, Any]) -> str | None:
    # Extract webhook secret candidate from headers/query/body.
    candidate = request.headers.get("X-Weblium-Secret") or request.headers.get(
        "X-Webhook-Secret"
    )
    if candidate:
        return candidate.strip()

    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None

    query_secret = request.rel_url.query.get("secret")
    if query_secret:
        return query_secret.strip() or None

    body_secret = payload.get("secret")
    if isinstance(body_secret, str):
        return body_secret.strip() or None
    return None


def _is_secret_valid(
    request: web.Request,
    payload: Mapping[str, Any],
    expected_secret: str,
) -> bool:
    candidate = _extract_secret_candidate(request, payload)
    if not candidate:
        return False
    return secrets.compare_digest(candidate, expected_secret)


def _is_ip_allowed(client_ip: str | None, trusted_proxy_ips: list[str]) -> bool:
    if not trusted_proxy_ips:
        return True
    if not client_ip:
        return False
    try:
        client = ip_address(client_ip)
    except ValueError:
        return False

    for raw_network in trusted_proxy_ips:
        if not raw_network:
            continue
        if client in ip_network(raw_network, strict=False):
            return True
    return False


def _extract_tg_token_from_referer(referer: str | None) -> str | None:
    if not referer:
        return None

    parsed = urlparse(referer)
    query_params = parse_qs(parsed.query)
    if query_params.get("tg_token"):
        return _as_string(query_params["tg_token"][0])

    # Supports links shaped like: https://site/#contact-form?tg_token=...
    if parsed.fragment and "?" in parsed.fragment:
        _, fragment_query = parsed.fragment.split("?", 1)
        fragment_params = parse_qs(fragment_query)
        if fragment_params.get("tg_token"):
            return _as_string(fragment_params["tg_token"][0])
    return None


def normalize_weblium_payload(
    payload: Mapping[str, Any],
    request: web.Request,
) -> dict[str, Any]:
    # Convert incoming Weblium payload to normalized internal structure.
    fields_raw = payload.get("fields")
    fields = fields_raw if isinstance(fields_raw, Mapping) else {}

    form_name = _as_string(payload.get("form_name"))
    referer = _as_string(payload.get("referer"))
    submitted_time = _as_string(payload.get("time"))

    applicant_name = _as_string(payload.get("applicant_name"))
    phone = _as_string(payload.get("phone")) or _as_string(payload.get("contact_phone"))
    email = _as_string(payload.get("email")) or _as_string(payload.get("contact_email"))
    specialization = _as_string(payload.get("specialization"))
    document_url = _ensure_absolute_url(_as_string(payload.get("document_url")))
    document_file_name = _as_string(payload.get("document_file_name"))
    tg_token = _as_string(payload.get("tg_token"))

    fallback_text_values: list[str] = []

    for key, item in fields.items():
        key_l = str(key).lower()
        if not isinstance(item, Mapping):
            continue

        title_l = _as_string(item.get("title"))
        title_l = title_l.lower() if title_l else ""
        field_type = _as_string(item.get("type"))
        field_type_l = field_type.lower() if field_type else ""
        field_value = item.get("value")
        field_text = _as_string(field_value)

        if key_l in {"short_text", "full_name"} or "name" in key_l:
            applicant_name = applicant_name or field_text
            continue

        if key_l in {"contactform_phonenumber", "phone", "phone_number"} or field_type_l == "phone":
            phone = phone or field_text
            continue

        if key_l in {"contactform_email", "email"} or field_type_l == "email":
            email = email or field_text
            continue

        if key_l == "tg_token" or "tg_token" in title_l:
            tg_token = tg_token or field_text
            continue

        if field_type_l == "file":
            if isinstance(field_value, Mapping):
                document_url = document_url or _ensure_absolute_url(
                    _as_string(field_value.get("url"))
                )
                document_file_name = document_file_name or _as_string(
                    field_value.get("fileName") or field_value.get("filename")
                )
            else:
                document_url = document_url or _ensure_absolute_url(field_text)
            continue

        if "specialization" in key_l or "specialization" in title_l or "special" in title_l:
            specialization = specialization or field_text
            continue

        if field_type_l in {"text", "textarea", "select", "radio"} and field_text:
            fallback_text_values.append(field_text)

    if not specialization and fallback_text_values:
        specialization = fallback_text_values[0]

    tg_token = (
        tg_token
        or _as_string(request.rel_url.query.get("tg_token"))
        or _extract_tg_token_from_referer(referer)
    )

    return {
        "form_name": form_name,
        "referer": referer,
        "time": submitted_time,
        "applicant_name": applicant_name,
        "phone": phone,
        "email": email,
        "specialization": specialization,
        "document_url": document_url,
        "document_file_name": document_file_name,
        "tg_token": tg_token,
    }


def _build_request_hash(request: web.Request, raw_body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(request.path.encode("utf-8"))
    digest.update(b"\n")
    digest.update(raw_body)
    return digest.hexdigest()


def _amount_to_minor(amount_value: Any) -> int:
    try:
        amount = Decimal(str(amount_value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid amount value: {amount_value}") from exc

    return int(
        (amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
        .to_integral_value(rounding=ROUND_HALF_UP)
    )


def _amount_major_string(amount_minor: int) -> str:
    return format(Decimal(amount_minor) / Decimal(100), ".2f")


def _encode_liqpay_data(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def _decode_liqpay_data(data: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(data.encode("utf-8")).decode("utf-8")
        decoded = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid LiqPay callback data payload") from exc

    if not isinstance(decoded, dict):
        raise ValueError("LiqPay callback payload must be a JSON object")
    return decoded


def _liqpay_signature(private_key: str, data: str) -> str:
    # LiqPay API expects: base64( sha1(private_key + data + private_key) )
    digest = hashlib.sha1(f"{private_key}{data}{private_key}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_liqpay_checkout_payload(
    config: Config,
    payment: Mapping[str, Any],
) -> dict[str, Any]:
    provider_order_id = _as_string(payment.get("provider_order_id"))
    if not provider_order_id:
        raise ValueError("Payment provider_order_id is missing for LiqPay checkout")

    return {
        "version": "3",
        "public_key": config.liqpay.public_key,
        "action": "pay",
        "amount": _amount_major_string(int(payment["amount_minor"])),
        "currency": str(payment["currency"]).upper(),
        "description": f"UAVDI membership payment #{payment['id']}",
        "order_id": provider_order_id,
        "server_url": config.liqpay.callback_url(),
    }


def _build_liqpay_checkout_html(
    data: str,
    signature: str,
    *,
    amount: str,
    currency: str,
    order_id: str,
) -> str:
    data_escaped = html_escape(data)
    signature_escaped = html_escape(signature)
    amount_escaped = html_escape(amount)
    currency_escaped = html_escape(currency)
    order_id_escaped = html_escape(order_id)
    title = "Оплата членства UAVDI"
    description = (
        f"Безпечна оплата через LiqPay. Сума: {amount_escaped} {currency_escaped}. "
        "Натисніть посилання, щоб продовжити."
    )
    title_escaped = html_escape(title)
    description_escaped = html_escape(description)
    return f"""<!doctype html>
<html lang="uk">
  <head>
    <meta charset="utf-8" />
    <title>{title_escaped}</title>
    <meta name="description" content="{description_escaped}" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content="{title_escaped}" />
    <meta property="og:description" content="{description_escaped}" />
    <meta property="og:site_name" content="UAVDI" />
    <meta name="twitter:card" content="summary" />
    <meta name="twitter:title" content="{title_escaped}" />
    <meta name="twitter:description" content="{description_escaped}" />
  </head>
  <body>
    <p>Перенаправлення на сторінку оплати LiqPay...</p>
    <p>Замовлення: {order_id_escaped}</p>
    <p>Сума: {amount_escaped} {currency_escaped}</p>
    <form id="liqpay_checkout" method="POST" action="https://www.liqpay.ua/api/3/checkout">
      <input type="hidden" name="data" value="{data_escaped}" />
      <input type="hidden" name="signature" value="{signature_escaped}" />
      <noscript><button type="submit">Перейти до LiqPay</button></noscript>
    </form>
    <script>document.getElementById('liqpay_checkout').submit();</script>
  </body>
</html>"""


def _is_token_record_valid(token_record: Mapping[str, Any] | None) -> bool:
    if not token_record:
        return False
    if token_record.get("used_at") is not None:
        return False

    expires_at = token_record.get("expires_at")
    if not isinstance(expires_at, datetime):
        return False
    return expires_at > datetime.now(timezone.utc)


def _build_unlinked_vote_text(
    application: Mapping[str, Any],
) -> str:
    return build_application_vote_text(dict(application), branch="unlinked")


async def _remove_token_entry_keyboard(
    bot: Bot,
    token_record: Mapping[str, Any] | None,
) -> None:
    if not token_record:
        return

    metadata_raw = token_record.get("metadata")
    metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
    chat_id = metadata.get("entry_chat_id")
    message_id = metadata.get("entry_message_id")
    if chat_id is None or message_id is None:
        return

    try:
        await bot.edit_message_reply_markup(
            chat_id=int(chat_id),
            message_id=int(message_id),
            reply_markup=None,
        )
    except TelegramBadRequest:
        # Message can already be edited/deleted or markup unchanged.
        return
    except TelegramForbiddenError:
        logger.warning("Cannot edit old entry message for user chat. chat_id=%s", chat_id)
        return
    except TelegramAPIError:
        logger.exception(
            "Failed to remove old application keyboard. chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )


async def _remove_user_entry_keyboards(
    bot: Bot,
    repo: PostgresRepo,
    tg_user_id: int,
    primary_token_record: Mapping[str, Any] | None = None,
) -> None:
    # Remove current and recent token-entry keyboards for this user.
    records: list[Mapping[str, Any]] = []
    if primary_token_record:
        records.append(primary_token_record)

    recent_records = await repo.list_application_token_records_for_user(
        tg_user_id=tg_user_id,
        limit=20,
    )
    records.extend(recent_records)

    seen_targets: set[tuple[int, int]] = set()
    for record in records:
        metadata_raw = record.get("metadata") if isinstance(record, Mapping) else None
        metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        chat_id = metadata.get("entry_chat_id")
        message_id = metadata.get("entry_message_id")
        if chat_id is None or message_id is None:
            continue
        try:
            target = (int(chat_id), int(message_id))
        except (TypeError, ValueError):
            continue
        if target in seen_targets:
            continue
        seen_targets.add(target)
        await _remove_token_entry_keyboard(bot=bot, token_record=record)


async def weblium_application_webhook(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    repo: PostgresRepo = request.app["repo"]
    bot: Bot = request.app["bot"]

    if not _is_content_type_valid(request):
        return web.json_response(
            {"ok": False, "error": "unsupported content type, expected application/json"},
            status=415,
        )

    raw_body = await request.read()
    raw_body_text = raw_body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_body_text) if raw_body_text else {}
    except json.JSONDecodeError:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response(
            {"ok": False, "error": "invalid json object payload"},
            status=400,
        )

    if not _is_secret_valid(request, payload, config.webhook.weblium_secret):
        return web.json_response(
            {"ok": False, "error": "invalid webhook secret"},
            status=401,
        )

    client_ip = _get_client_ip(request)
    if not _is_ip_allowed(client_ip, config.webhook.trusted_proxy_ips):
        return web.json_response(
            {"ok": False, "error": "source ip is not allowed"},
            status=403,
        )

    normalized = normalize_weblium_payload(payload, request)
    request_hash = _build_request_hash(request, raw_body)

    if await repo.webhook_event_exists(event_key=request_hash, provider="weblium"):
        return web.json_response(
            {
                "ok": True,
                "message": "duplicate webhook ignored",
                "request_hash": request_hash,
            },
            status=200,
        )

    webhook_event_id: int | None = None
    try:
        event = await repo.create_webhook_event(
            provider="weblium",
            event_key=request_hash,
            request_path=request.path,
            source_ip=client_ip,
            headers=dict(request.headers),
            query_params=dict(request.rel_url.query),
            payload_raw=raw_body_text,
            payload_json=payload,
        )
        if not bool(event.get("is_new")):
            return web.json_response(
                {
                    "ok": True,
                    "message": "duplicate webhook ignored",
                    "request_hash": request_hash,
                },
                status=200,
            )
        webhook_event_id = int(event["id"])

        token = _as_string(normalized.get("tg_token"))
        token_record = await repo.get_token_record(token) if token else None

        if token:
            if not _is_token_record_valid(token_record):
                return web.json_response(
                    {
                        "ok": False,
                        "message": "invalid/expired/reused tg_token ignored",
                        "branch": "token_invalid",
                        "request_hash": request_hash,
                    },
                    status=200,
                )

            try:
                tg_user_id = int(token_record["tg_user_id"])
                matched_application = await repo.create_matched_application_from_webhook(
                    tg_user_id=tg_user_id,
                    payload_json=payload,
                    application_token=token,
                    webhook_event_id=webhook_event_id,
                )
            except ValueError:
                logger.info(
                    "Token became invalid during processing, ignoring tokenized webhook. token=%s",
                    token,
                )
                return web.json_response(
                    {
                        "ok": False,
                        "message": "invalid/expired/reused tg_token ignored",
                        "branch": "token_invalid",
                        "request_hash": request_hash,
                    },
                    status=200,
                )
            else:
                await notify_user(
                    bot=bot,
                    user_id=tg_user_id,
                    text=(
                        "Your application was received from the website and is now under review."
                    ),
                    context={
                        "event": "weblium_application_pending",
                        "application_id": matched_application.get("id"),
                    },
                )
                await _remove_user_entry_keyboards(
                    bot=bot,
                    repo=repo,
                    tg_user_id=tg_user_id,
                    primary_token_record=token_record,
                )
                await start_vote(
                    application_id=int(matched_application["id"]),
                    application_text=build_application_vote_text(
                        dict(matched_application),
                        branch="matched",
                    ),
                    bot=bot,
                    config=config,
                    repo=repo,
                )

                return web.json_response(
                    {
                        "ok": True,
                        "message": "matched application ingested",
                        "branch": "matched",
                        "application_id": matched_application.get("id"),
                        "request_hash": request_hash,
                    },
                    status=200,
                )

        unlinked_application = await repo.create_unlinked_application_from_webhook(
            payload_json=payload,
            webhook_event_id=webhook_event_id,
        )
        bind_record = await repo.create_bind_token(
            application_id=int(unlinked_application["id"]),
            expires_at=datetime.now(timezone.utc) + timedelta(days=BIND_TOKEN_TTL_DAYS),
            metadata={
                "source": "weblium_unlinked_webhook",
                "request_hash": request_hash,
                "had_tg_token": bool(token),
                "tg_token_valid": False,
            },
        )
        await start_vote(
            application_id=int(unlinked_application["id"]),
            application_text=_build_unlinked_vote_text(
                dict(unlinked_application),
            ),
            bot=bot,
            config=config,
            repo=repo,
        )

        return web.json_response(
            {
                "ok": True,
                "message": "unlinked application ingested",
                "branch": "unlinked",
                "application_id": unlinked_application.get("id"),
                "bind_token": bind_record.get("token"),
                "request_hash": request_hash,
            },
            status=200,
        )

    except Exception as exc:  # noqa: BLE001
        if webhook_event_id is not None:
            await repo.mark_webhook_event_processed(
                event_id=webhook_event_id,
                processing_status="FAILED",
                processing_error=str(exc)[:1000],
            )
        logger.exception("Failed to process Weblium application webhook")
        return web.json_response(
            {"ok": False, "error": "failed to process webhook"},
            status=500,
        )


async def liqpay_pay_page(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    repo: PostgresRepo = request.app["repo"]

    payment_id_raw = _as_string(request.match_info.get("payment_id"))
    if not payment_id_raw or not payment_id_raw.isdigit():
        return web.Response(status=404, text="payment not found")

    payment = await repo.get_payment_by_id(int(payment_id_raw))
    if payment is None or str(payment.get("provider")) != "liqpay":
        return web.Response(status=404, text="payment not found")

    payment_status = str(payment.get("status"))
    if payment_status == "PAID":
        return web.Response(
            text="<html><body><p>Payment is already confirmed.</p></body></html>",
            content_type="text/html",
        )
    if payment_status != "PENDING":
        return web.Response(status=400, text="payment is not active")

    try:
        payload = _build_liqpay_checkout_payload(config, payment)
        data = _encode_liqpay_data(payload)
        signature = _liqpay_signature(config.liqpay.private_key, data)
    except ValueError as exc:
        return web.Response(status=400, text=str(exc))

    return web.Response(
        text=_build_liqpay_checkout_html(
            data=data,
            signature=signature,
            amount=str(payload.get("amount") or ""),
            currency=str(payload.get("currency") or ""),
            order_id=str(payload.get("order_id") or ""),
        ),
        content_type="text/html",
    )


async def liqpay_callback_webhook(request: web.Request) -> web.Response:
    repo: PostgresRepo = request.app["repo"]
    bot: Bot = request.app["bot"]
    config: Config = request.app["config"]

    form = await request.post()
    data = _as_string(form.get("data"))
    signature = _as_string(form.get("signature"))

    if not data or not signature:
        return web.Response(status=400, text="missing data/signature")

    try:
        decoded = _decode_liqpay_data(data)
    except ValueError:
        return web.Response(status=400, text="invalid callback data")

    provider_order_id = _as_string(decoded.get("order_id"))
    provider_status = (_as_string(decoded.get("status")) or "").lower()
    currency = (_as_string(decoded.get("currency")) or "").upper()
    amount_raw = decoded.get("amount")
    if not provider_order_id:
        return web.Response(status=400, text="missing order_id")

    expected_signature = _liqpay_signature(config.liqpay.private_key, data)
    signature_valid = secrets.compare_digest(signature, expected_signature)
    if not signature_valid:
        await repo.update_liqpay_callback_audit(
            provider_order_id=provider_order_id,
            provider_status=provider_status,
            callback_data=data,
            callback_signature=signature,
            signature_valid=False,
        )
        return web.Response(status=400, text="invalid signature")

    payment = await repo.get_payment_by_provider_order_id(provider_order_id)
    if payment is None:
        return web.Response(status=404, text="payment not found")

    try:
        amount_minor = _amount_to_minor(amount_raw)
    except ValueError:
        await repo.update_liqpay_callback_audit(
            provider_order_id=provider_order_id,
            provider_status=provider_status,
            callback_data=data,
            callback_signature=signature,
            signature_valid=True,
        )
        return web.Response(status=400, text="invalid amount")

    if provider_status != "success":
        updated = await repo.update_liqpay_callback_audit(
            provider_order_id=provider_order_id,
            provider_status=provider_status,
            callback_data=data,
            callback_signature=signature,
            signature_valid=True,
        )
        if updated is None:
            return web.Response(status=404, text="payment not found")

        if int(updated["amount_minor"]) != int(amount_minor):
            return web.Response(status=400, text="callback amount mismatch")
        if str(updated["currency"]).upper() != currency:
            return web.Response(status=400, text="callback currency mismatch")
        return web.Response(text="ok")

    try:
        result = await repo.process_liqpay_success_callback(
            provider_order_id=provider_order_id,
            amount_minor=amount_minor,
            currency=currency,
            provider_status=provider_status,
            callback_data=data,
            callback_signature=signature,
            signature_valid=True,
        )
    except ValueError as exc:
        return web.Response(status=400, text=str(exc))

    if result.get("paid_now"):
        application = result.get("application") or {}
        tg_user_id = application.get("tg_user_id")
        if tg_user_id is not None:
            app_status = str(application.get("status") or "")
            show_group_access = app_status == "PAID_AWAITING_JOIN"
            await notify_user(
                bot=bot,
                user_id=int(tg_user_id),
                text=(
                    "Payment confirmed. Your 365-day subscription is active.\n"
                    + (
                        "You can now request group access."
                        if show_group_access
                        else "Your access is already active."
                    )
                ),
                reply_markup=group_access_keyboard() if show_group_access else None,
                context={
                    "event": "liqpay_payment_confirmed",
                    "provider_order_id": provider_order_id,
                    "application_id": application.get("id"),
                },
            )

    return web.Response(text="ok")


async def _on_startup(app: web.Application) -> None:
    config: Config = app["config"]
    pool = await init_db(config)
    app["db_pool"] = pool
    app["repo"] = PostgresRepo(pool)
    app["bot"] = Bot(
        token=config.tg_bot.token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    logger.info(
        "Webhook app started on %s:%s weblium_path=%s liqpay_callback=%s liqpay_pay=%s",
        config.webhook.host,
        config.webhook.port,
        config.webhook.weblium_path or PRIMARY_WEBLIUM_PATH,
        config.liqpay.callback_path or PRIMARY_LIQPAY_CALLBACK_PATH,
        config.liqpay.pay_path or PRIMARY_LIQPAY_PAY_PATH,
    )
    logger.info(
        "LiqPay runtime config: public_key=%s private_key_fingerprint=%s",
        config.liqpay.public_key,
        hashlib.sha256(config.liqpay.private_key.encode("utf-8")).hexdigest()[:10],
    )


async def _on_cleanup(app: web.Application) -> None:
    bot: Bot | None = app.get("bot")
    if bot is not None:
        await bot.session.close()

    await shutdown_db(app.get("db_pool"))
    logger.info("Weblium webhook app stopped")


def create_app(config: Config | None = None) -> web.Application:
    resolved_config = config or load_config(".env")
    app = web.Application()
    app["config"] = resolved_config

    app.router.add_post(PRIMARY_WEBLIUM_PATH, weblium_application_webhook)
    configured_path = resolved_config.webhook.weblium_path
    if configured_path and configured_path != PRIMARY_WEBLIUM_PATH:
        app.router.add_post(configured_path, weblium_application_webhook)

    app.router.add_get(PRIMARY_LIQPAY_PAY_PATH, liqpay_pay_page)
    configured_pay_path = resolved_config.liqpay.pay_path
    if configured_pay_path and configured_pay_path != PRIMARY_LIQPAY_PAY_PATH:
        app.router.add_get(configured_pay_path, liqpay_pay_page)

    app.router.add_post(PRIMARY_LIQPAY_CALLBACK_PATH, liqpay_callback_webhook)
    configured_callback_path = resolved_config.liqpay.callback_path
    if configured_callback_path and configured_callback_path != PRIMARY_LIQPAY_CALLBACK_PATH:
        app.router.add_post(configured_callback_path, liqpay_callback_webhook)

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    runtime_config = load_config(".env")
    if not runtime_config.webhook.webhook_enabled:
        raise RuntimeError(
            "WEBHOOK_ENABLED must be true to run production Weblium webhook server."
        )
    web.run_app(
        create_app(runtime_config),
        host=runtime_config.webhook.host,
        port=runtime_config.webhook.port,
    )

