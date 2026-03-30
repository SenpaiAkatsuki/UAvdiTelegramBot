from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from tgbot.config import ThrottlingConfig

"""
In-memory anti-spam middleware.

Applies per-user rolling-window limits for messages and callbacks.
"""

logger = logging.getLogger(__name__)

HEAVY_CALLBACKS = {
    "membership_pay",
    "membership_check_payment_status",
    "membership_get_group_access",
}


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, throttling: ThrottlingConfig) -> None:
        # Store throttling config and in-memory hit buckets.
        self._cfg = throttling
        self._hits: dict[tuple[int, str], list[float]] = {}
        self._cleanup_every = 500
        self._calls = 0

    def _prune_old(self, now: float) -> None:
        # Periodically drop stale bucket entries to bound memory usage.
        if self._calls % self._cleanup_every != 0:
            return

        hit_ttl_seconds = max(self._cfg.window_seconds * 3, 60.0)
        pruned_hits: dict[tuple[int, str], list[float]] = {}
        for key, timestamps in self._hits.items():
            alive = [ts for ts in timestamps if now - ts <= hit_ttl_seconds]
            if alive:
                pruned_hits[key] = alive
        self._hits = pruned_hits

    def _hit(
        self,
        user_id: int,
        bucket: str,
        max_events: int,
    ) -> tuple[bool, float]:
        # Record event hit and return (throttled, retry_after_seconds).
        now = time.monotonic()
        self._calls += 1
        self._prune_old(now)

        key = (user_id, bucket)
        window_seconds = self._cfg.window_seconds
        since = now - window_seconds
        timestamps = [ts for ts in self._hits.get(key, []) if ts >= since]

        if len(timestamps) >= max_events:
            oldest = timestamps[0]
            retry_after = max(window_seconds - (now - oldest), 0.0)
            self._hits[key] = timestamps
            return True, retry_after

        timestamps.append(now)
        self._hits[key] = timestamps
        return False, 0.0

    def _classify_message(self, message: Message) -> tuple[str, int]:
        # Map message to throttling bucket and limit.
        text = (message.text or message.caption or "").strip()
        if text.startswith("/"):
            return "command", self._cfg.command_max_events
        return "message", self._cfg.message_max_events

    def _classify_callback(self, query: CallbackQuery) -> tuple[str, int]:
        # Map callback data to regular or heavy throttling bucket.
        data = (query.data or "").strip()
        if data in HEAVY_CALLBACKS:
            return "heavy_callback", self._cfg.heavy_callback_max_events
        return "callback", self._cfg.callback_max_events

    @staticmethod
    def _is_admin_user(data: dict[str, Any], user_id: int) -> bool:
        # Skip throttling for configured admins.
        config = data.get("config")
        if config is None:
            return False
        try:
            admin_ids = config.tg_bot.admin_ids
        except Exception:  # noqa: BLE001
            return False
        return user_id in admin_ids

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        # Gate updates by configured limits before passing to handlers.
        if not self._cfg.enabled:
            return await handler(event, data)

        if isinstance(event, Message):
            from_user = event.from_user
            if from_user is None:
                return await handler(event, data)
            if self._is_admin_user(data, from_user.id):
                return await handler(event, data)
            bucket, max_events = self._classify_message(event)
            if max_events > 0:
                throttled, _ = self._hit(
                    user_id=from_user.id,
                    bucket=bucket,
                    max_events=max_events,
                )
                if throttled:
                    logger.debug(
                        "Message throttled user_id=%s bucket=%s limit=%s window=%s",
                        from_user.id,
                        bucket,
                        max_events,
                        self._cfg.window_seconds,
                    )
                    return None
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            from_user = event.from_user
            if from_user is None:
                return await handler(event, data)
            if self._is_admin_user(data, from_user.id):
                return await handler(event, data)
            bucket, max_events = self._classify_callback(event)
            if max_events > 0:
                throttled, retry_after = self._hit(
                    user_id=from_user.id,
                    bucket=bucket,
                    max_events=max_events,
                )
                if throttled:
                    wait_seconds = max(1, int(retry_after) + 1)
                    try:
                        await event.answer(
                            (
                                f"⚠️ Забагато запитів. Спробуйте знову через {wait_seconds}с "
                                f"(ліміт: {max_events}/{int(self._cfg.window_seconds)}с)."
                            ),
                            show_alert=False,
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "Failed to answer throttled callback query",
                            exc_info=True,
                        )
                    return None
            return await handler(event, data)

        return await handler(event, data)
