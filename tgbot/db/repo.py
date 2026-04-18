from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4

import asyncpg
from asyncpg import Connection, Pool, Record

"""
PostgreSQL repository.

Central async data-access layer for users, applications, payments, voting, and subscriptions.
"""


def _record_to_dict(row: Record | None) -> dict[str, Any] | None:
    # Convert asyncpg record to plain dict.
    if row is None:
        return None
    return dict(row)


def _utcnow() -> datetime:
    # Current UTC timestamp helper.
    return datetime.now(timezone.utc)


SUBSCRIPTION_PRICE_MINOR_SETTING_KEY = "subscription_price_minor"
EXPIRING_WINDOW_DAYS_SETTING_KEY = "expiring_window_days"
VOTE_MIN_TOTAL_SETTING_KEY = "vote_min_total"
VOTE_DURATION_SECONDS_SETTING_KEY = "vote_duration_seconds"


def _to_jsonb(value: Mapping[str, Any] | None = None) -> str:
    # Serialize mapping to JSON string for jsonb columns.
    return json.dumps(dict(value or {}), ensure_ascii=False, default=str)


@dataclass
class PostgresRepo:
    pool: Pool

    async def _acquire_conn(self, conn: Connection | None = None) -> Connection:
        # Reuse external connection or acquire one from pool.
        if conn is not None:
            return conn
        return await self.pool.acquire()

    async def _release_conn(self, conn: Connection, external: Connection | None) -> None:
        # Release only internally acquired connections.
        if external is None:
            await self.pool.release(conn)

    @staticmethod
    def _clean_string(value: Any) -> str | None:
        # Normalize incoming values to stripped string or None.
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return str(value).strip() or None

    @staticmethod
    def compute_days_left(subscription_expires_at: datetime | None) -> int | None:
        # Compute whole-day difference from today (UTC).
        if not isinstance(subscription_expires_at, datetime):
            return None

        expires_at = subscription_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)

        today = _utcnow().date()
        return (expires_at.date() - today).days

    async def get_setting(
        self,
        key: str,
        conn: Connection | None = None,
    ) -> str | None:
        acquired = await self._acquire_conn(conn)
        try:
            value = await acquired.fetchval(
                "SELECT value_text FROM app_settings WHERE key = $1;",
                key,
            )
            if value is None:
                return None
            return str(value)
        finally:
            await self._release_conn(acquired, conn)

    async def set_setting(
        self,
        key: str,
        value_text: str,
        updated_by_tg_user_id: int | None = None,
        conn: Connection | None = None,
    ) -> None:
        acquired = await self._acquire_conn(conn)
        try:
            await acquired.execute(
                """
                INSERT INTO app_settings (key, value_text, updated_by_tg_user_id, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (key) DO UPDATE
                SET
                    value_text = EXCLUDED.value_text,
                    updated_by_tg_user_id = EXCLUDED.updated_by_tg_user_id,
                    updated_at = NOW();
                """,
                key,
                value_text,
                updated_by_tg_user_id,
            )
        finally:
            await self._release_conn(acquired, conn)

    async def get_subscription_price_minor(self, default_minor: int) -> int:
        value_text = await self.get_setting(SUBSCRIPTION_PRICE_MINOR_SETTING_KEY)
        if value_text is None:
            return max(int(default_minor), 1)
        try:
            parsed = int(value_text)
        except ValueError:
            return max(int(default_minor), 1)
        return max(parsed, 1)

    async def set_subscription_price_minor(
        self,
        amount_minor: int,
        updated_by_tg_user_id: int | None = None,
    ) -> int:
        normalized = max(int(amount_minor), 1)
        await self.set_setting(
            key=SUBSCRIPTION_PRICE_MINOR_SETTING_KEY,
            value_text=str(normalized),
            updated_by_tg_user_id=updated_by_tg_user_id,
        )
        return normalized

    async def get_expiring_window_days(self, default_days: int = 30) -> int:
        value_text = await self.get_setting(EXPIRING_WINDOW_DAYS_SETTING_KEY)
        fallback = max(int(default_days), 1)
        if value_text is None:
            return fallback
        try:
            parsed = int(value_text)
        except ValueError:
            return fallback
        return max(parsed, 1)

    async def set_expiring_window_days(
        self,
        days: int,
        updated_by_tg_user_id: int | None = None,
    ) -> int:
        normalized = max(int(days), 1)
        await self.set_setting(
            key=EXPIRING_WINDOW_DAYS_SETTING_KEY,
            value_text=str(normalized),
            updated_by_tg_user_id=updated_by_tg_user_id,
        )
        return normalized

    async def get_vote_min_total(self, default_target: int = 1) -> int:
        value_text = await self.get_setting(VOTE_MIN_TOTAL_SETTING_KEY)
        fallback = max(int(default_target), 1)
        if value_text is None:
            return fallback
        try:
            parsed = int(value_text)
        except ValueError:
            return fallback
        return max(parsed, 1)

    async def set_vote_min_total(
        self,
        target: int,
        updated_by_tg_user_id: int | None = None,
    ) -> int:
        normalized = max(int(target), 1)
        await self.set_setting(
            key=VOTE_MIN_TOTAL_SETTING_KEY,
            value_text=str(normalized),
            updated_by_tg_user_id=updated_by_tg_user_id,
        )
        return normalized

    async def get_vote_duration_seconds(self, default_seconds: int) -> int:
        value_text = await self.get_setting(VOTE_DURATION_SECONDS_SETTING_KEY)
        fallback = max(int(default_seconds), 0)
        if value_text is None:
            return fallback
        try:
            parsed = int(value_text)
        except ValueError:
            return fallback
        return max(parsed, 0)

    async def set_vote_duration_seconds(
        self,
        seconds: int,
        updated_by_tg_user_id: int | None = None,
    ) -> int:
        normalized = max(int(seconds), 0)
        await self.set_setting(
            key=VOTE_DURATION_SECONDS_SETTING_KEY,
            value_text=str(normalized),
            updated_by_tg_user_id=updated_by_tg_user_id,
        )
        return normalized

    @staticmethod
    def _extract_weblium_fields(payload_json: Mapping[str, Any]) -> dict[str, Any]:
        fields_raw = payload_json.get("fields")
        fields = fields_raw if isinstance(fields_raw, Mapping) else {}

        applicant_name = (
            PostgresRepo._clean_string(payload_json.get("applicant_name"))
            or PostgresRepo._clean_string(payload_json.get("name"))
        )
        contact_phone = (
            PostgresRepo._clean_string(payload_json.get("contact_phone"))
            or PostgresRepo._clean_string(payload_json.get("phone"))
        )
        contact_email = (
            PostgresRepo._clean_string(payload_json.get("contact_email"))
            or PostgresRepo._clean_string(payload_json.get("email"))
        )
        specialization = PostgresRepo._clean_string(payload_json.get("specialization"))
        document_url = PostgresRepo._clean_string(payload_json.get("document_url"))
        document_file_name = PostgresRepo._clean_string(
            payload_json.get("document_file_name")
        )

        fallback_text_values: list[str] = []

        for key, item in fields.items():
            key_l = str(key).lower()
            if not isinstance(item, Mapping):
                continue

            field_type = PostgresRepo._clean_string(item.get("type"))
            field_type_l = field_type.lower() if field_type else ""
            title = PostgresRepo._clean_string(item.get("title"))
            title_l = title.lower() if title else ""
            field_value = item.get("value")
            field_text = PostgresRepo._clean_string(field_value)

            if key_l in {"short_text", "full_name"} or "name" in key_l:
                applicant_name = applicant_name or field_text
            elif key_l in {"contactform_phonenumber", "phone", "phone_number"} or field_type_l == "phone":
                contact_phone = contact_phone or field_text
            elif key_l in {"contactform_email", "email"} or field_type_l == "email":
                contact_email = contact_email or field_text
            elif (
                "specialization" in key_l
                or "specialization" in title_l
                or "special" in title_l
            ):
                specialization = specialization or field_text
            elif field_type_l == "file":
                if isinstance(field_value, Mapping):
                    document_url = document_url or PostgresRepo._clean_string(
                        field_value.get("url")
                    )
                    document_file_name = document_file_name or PostgresRepo._clean_string(
                        field_value.get("fileName") or field_value.get("filename")
                    )
                else:
                    document_url = document_url or field_text
            elif field_type_l in {"text", "textarea", "select", "radio"} and field_text:
                fallback_text_values.append(field_text)

        if not specialization and fallback_text_values:
            specialization = fallback_text_values[0]

        if document_url and document_url.startswith("//"):
            document_url = f"https:{document_url}"

        return {
            "applicant_name": applicant_name,
            "contact_phone": contact_phone,
            "contact_email": contact_email,
            "specialization": specialization,
            "document_url": document_url,
            "document_file_name": document_file_name,
            "weblium_referer": payload_json.get("referer"),
        }

    async def create_or_update_user(
        self,
        tg_user_id: int,
        full_name: str,
        username: str | None = None,
        language_code: str | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                INSERT INTO users (tg_user_id, username, full_name, language_code, is_active)
                VALUES ($1, $2, $3, $4, TRUE)
                ON CONFLICT (tg_user_id) DO UPDATE
                SET
                    username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    language_code = EXCLUDED.language_code,
                    is_active = TRUE,
                    updated_at = NOW()
                RETURNING *;
                """,
                tg_user_id,
                username,
                full_name,
                language_code,
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def set_user_subscription_until(
        self,
        *,
        tg_user_id: int,
        subscription_expires_at: datetime,
        full_name: str | None = None,
        username: str | None = None,
        language_code: str | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        # Upsert user and set ACTIVE subscription until provided timestamp.
        acquired = await self._acquire_conn(conn)
        try:
            expires_at = subscription_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at = expires_at.astimezone(timezone.utc)
            normalized_name = self._clean_string(full_name) or f"User {int(tg_user_id)}"
            normalized_username = self._clean_string(username)
            normalized_language_code = self._clean_string(language_code)
            row = await acquired.fetchrow(
                """
                INSERT INTO users (
                    tg_user_id,
                    username,
                    full_name,
                    language_code,
                    is_active,
                    member_since,
                    subscription_expires_at,
                    subscription_status
                )
                VALUES ($1, $2, $3, $4, TRUE, NOW(), $5, 'ACTIVE')
                ON CONFLICT (tg_user_id) DO UPDATE
                SET
                    username = COALESCE(EXCLUDED.username, users.username),
                    full_name = COALESCE(EXCLUDED.full_name, users.full_name),
                    language_code = COALESCE(EXCLUDED.language_code, users.language_code),
                    is_active = TRUE,
                    member_since = COALESCE(users.member_since, NOW()),
                    subscription_expires_at = $5,
                    subscription_status = 'ACTIVE',
                    updated_at = NOW()
                RETURNING *;
                """,
                int(tg_user_id),
                normalized_username,
                normalized_name,
                normalized_language_code,
                expires_at,
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def is_bot_admin(
        self,
        tg_user_id: int,
        conn: Connection | None = None,
    ) -> bool:
        # Check runtime admin flag from voting_members (fallback to users legacy flag).
        acquired = await self._acquire_conn(conn)
        try:
            value = await acquired.fetchval(
                """
                SELECT
                    COALESCE(
                        (SELECT vm.is_bot_admin FROM voting_members vm WHERE vm.tg_user_id = $1),
                        FALSE
                    )
                    OR
                    COALESCE(
                        (SELECT u.is_bot_admin FROM users u WHERE u.tg_user_id = $1),
                        FALSE
                    );
                """,
                int(tg_user_id),
            )
            return bool(value)
        finally:
            await self._release_conn(acquired, conn)

    async def list_bot_admin_ids(
        self,
        conn: Connection | None = None,
    ) -> list[int]:
        # Return all DB-stored admin user ids (voting_members + legacy users flag).
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT DISTINCT tg_user_id
                FROM (
                    SELECT vm.tg_user_id
                    FROM voting_members vm
                    WHERE vm.is_bot_admin = TRUE
                    UNION
                    SELECT u.tg_user_id
                    FROM users u
                    WHERE COALESCE(u.is_bot_admin, FALSE) = TRUE
                ) t
                ORDER BY tg_user_id;
                """
            )
            return [int(row["tg_user_id"]) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def list_broadcast_user_ids(
        self,
        *,
        include_inactive: bool = False,
        conn: Connection | None = None,
    ) -> list[int]:
        # Return unique Telegram user ids for admin broadcast.
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT u.tg_user_id
                FROM users u
                WHERE u.tg_user_id IS NOT NULL
                  AND ($1::bool OR u.is_active = TRUE)
                ORDER BY u.tg_user_id ASC;
                """,
                bool(include_inactive),
            )
            return [int(row["tg_user_id"]) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def grant_bot_admin(
        self,
        *,
        tg_user_id: int,
        full_name: str | None = None,
        username: str | None = None,
        language_code: str | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        # Upsert user and grant bot-admin in voting_members.
        acquired = await self._acquire_conn(conn)
        try:
            normalized_full_name = self._clean_string(full_name) or f"User {int(tg_user_id)}"
            normalized_username = self._clean_string(username)
            normalized_language_code = self._clean_string(language_code)
            user_row = await acquired.fetchrow(
                """
                INSERT INTO users (
                    tg_user_id,
                    username,
                    full_name,
                    language_code,
                    is_active,
                    is_bot_admin
                )
                VALUES ($1, $2, $3, $4, TRUE, TRUE)
                ON CONFLICT (tg_user_id) DO UPDATE
                SET
                    username = COALESCE(EXCLUDED.username, users.username),
                    full_name = COALESCE(EXCLUDED.full_name, users.full_name),
                    language_code = COALESCE(EXCLUDED.language_code, users.language_code),
                    is_active = TRUE,
                    is_bot_admin = TRUE,
                    updated_at = NOW()
                RETURNING *;
                """,
                int(tg_user_id),
                normalized_username,
                normalized_full_name,
                normalized_language_code,
            )
            await acquired.execute(
                """
                INSERT INTO voting_members (
                    tg_user_id,
                    username,
                    full_name,
                    language_code,
                    is_bot_admin,
                    member_status,
                    first_seen_at,
                    last_seen_at,
                    last_verified_at
                )
                VALUES ($1, $2, $3, $4, TRUE, 'ACTIVE', NOW(), NOW(), NOW())
                ON CONFLICT (tg_user_id) DO UPDATE
                SET
                    username = COALESCE(EXCLUDED.username, voting_members.username),
                    full_name = COALESCE(EXCLUDED.full_name, voting_members.full_name),
                    language_code = COALESCE(EXCLUDED.language_code, voting_members.language_code),
                    is_bot_admin = TRUE,
                    member_status = 'ACTIVE',
                    last_seen_at = NOW(),
                    last_verified_at = NOW(),
                    updated_at = NOW();
                """,
                int(tg_user_id),
                normalized_username,
                normalized_full_name,
                normalized_language_code,
            )
            return _record_to_dict(user_row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def get_voting_member(
        self,
        tg_user_id: int,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Return voting-member row by Telegram user id.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT *
                FROM voting_members
                WHERE tg_user_id = $1;
                """,
                int(tg_user_id),
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def list_known_tg_user_ids_for_voting_sync(
        self,
        *,
        limit: int = 5000,
        conn: Connection | None = None,
    ) -> list[int]:
        # Return deduplicated Telegram user ids from voting-related sources.
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT tg_user_id
                FROM (
                    SELECT vm.tg_user_id
                    FROM voting_members vm
                    UNION
                    SELECT av.tg_user_id
                    FROM application_votes av
                ) ids
                WHERE tg_user_id IS NOT NULL
                ORDER BY tg_user_id ASC
                LIMIT $1;
                """,
                max(int(limit), 1),
            )
            return [int(row["tg_user_id"]) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def is_active_voting_member(
        self,
        tg_user_id: int,
        conn: Connection | None = None,
    ) -> bool:
        # Check ACTIVE membership flag in voting_members table.
        acquired = await self._acquire_conn(conn)
        try:
            value = await acquired.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM voting_members
                    WHERE tg_user_id = $1
                      AND member_status = 'ACTIVE'
                );
                """,
                int(tg_user_id),
            )
            return bool(value)
        finally:
            await self._release_conn(acquired, conn)

    async def upsert_voting_member(
        self,
        *,
        tg_user_id: int,
        username: str | None = None,
        full_name: str | None = None,
        language_code: str | None = None,
        member_status: str = "ACTIVE",
        is_bot_admin: bool | None = None,
        verified_at: datetime | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        # Upsert voting-member profile/status with optional admin flag.
        acquired = await self._acquire_conn(conn)
        try:
            normalized_status = str(member_status or "ACTIVE").strip().upper()
            if normalized_status not in {"ACTIVE", "LEFT"}:
                normalized_status = "ACTIVE"
            normalized_username = self._clean_string(username)
            normalized_full_name = self._clean_string(full_name) or f"User {int(tg_user_id)}"
            normalized_language_code = self._clean_string(language_code)
            row = await acquired.fetchrow(
                """
                INSERT INTO voting_members (
                    tg_user_id,
                    username,
                    full_name,
                    language_code,
                    is_bot_admin,
                    member_status,
                    first_seen_at,
                    last_seen_at,
                    last_verified_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    COALESCE($5, FALSE),
                    $6,
                    NOW(),
                    NOW(),
                    $7
                )
                ON CONFLICT (tg_user_id) DO UPDATE
                SET
                    username = COALESCE(EXCLUDED.username, voting_members.username),
                    full_name = COALESCE(EXCLUDED.full_name, voting_members.full_name),
                    language_code = COALESCE(EXCLUDED.language_code, voting_members.language_code),
                    is_bot_admin = COALESCE($5, voting_members.is_bot_admin),
                    member_status = $6,
                    last_seen_at = NOW(),
                    last_verified_at = COALESCE($7, voting_members.last_verified_at),
                    updated_at = NOW()
                RETURNING *;
                """,
                int(tg_user_id),
                normalized_username,
                normalized_full_name,
                normalized_language_code,
                is_bot_admin,
                normalized_status,
                verified_at,
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def set_voting_member_status(
        self,
        *,
        tg_user_id: int,
        member_status: str,
        clear_admin: bool = False,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Update voting member status; optionally revoke admin flag on leave.
        acquired = await self._acquire_conn(conn)
        try:
            normalized_status = str(member_status or "LEFT").strip().upper()
            if normalized_status not in {"ACTIVE", "LEFT"}:
                normalized_status = "LEFT"
            row = await acquired.fetchrow(
                """
                UPDATE voting_members
                SET
                    member_status = $2,
                    is_bot_admin = CASE WHEN $3 THEN FALSE ELSE is_bot_admin END,
                    last_seen_at = NOW(),
                    last_verified_at = NOW(),
                    updated_at = NOW()
                WHERE tg_user_id = $1
                RETURNING *;
                """,
                int(tg_user_id),
                normalized_status,
                bool(clear_admin),
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def get_user_membership_invite(
        self,
        tg_user_id: int,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Return last stored membership invite link metadata for user.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT
                    last_membership_invite_link,
                    last_membership_invite_expires_at
                FROM users
                WHERE tg_user_id = $1;
                """,
                int(tg_user_id),
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def set_user_membership_invite(
        self,
        *,
        tg_user_id: int,
        invite_link: str,
        invite_expires_at: datetime | None,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Persist latest generated invite link for dedupe/revoke logic.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE users
                SET
                    last_membership_invite_link = $2,
                    last_membership_invite_expires_at = $3,
                    updated_at = NOW()
                WHERE tg_user_id = $1
                RETURNING *;
                """,
                int(tg_user_id),
                self._clean_string(invite_link),
                invite_expires_at,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def clear_user_membership_invite(
        self,
        tg_user_id: int,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Drop cached invite link metadata after successful join/sync.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE users
                SET
                    last_membership_invite_link = NULL,
                    last_membership_invite_expires_at = NULL,
                    updated_at = NOW()
                WHERE tg_user_id = $1
                RETURNING *;
                """,
                int(tg_user_id),
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def activate_membership_from_group_entry(
        self,
        *,
        tg_user_id: int,
        full_name: str | None = None,
        username: str | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        # Sync DB state after manual/approved membership-group entry.
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                name_value = self._clean_string(full_name) or f"User {int(tg_user_id)}"
                username_value = self._clean_string(username)
                language_value = self._clean_string(language_code)

                user_before = await conn.fetchrow(
                    """
                    SELECT *
                    FROM users
                    WHERE tg_user_id = $1
                    FOR UPDATE;
                    """,
                    int(tg_user_id),
                )
                if user_before is None:
                    user_before = await conn.fetchrow(
                        """
                        INSERT INTO users (tg_user_id, username, full_name, language_code, is_active)
                        VALUES ($1, $2, $3, $4, TRUE)
                        RETURNING *;
                        """,
                        int(tg_user_id),
                        username_value,
                        name_value,
                        language_value,
                    )
                else:
                    user_before = await conn.fetchrow(
                        """
                        UPDATE users
                        SET
                            username = COALESCE($2, username),
                            full_name = COALESCE($3, full_name),
                            language_code = COALESCE($4, language_code),
                            is_active = TRUE,
                            updated_at = NOW()
                        WHERE tg_user_id = $1
                        RETURNING *;
                        """,
                        int(tg_user_id),
                        username_value,
                        name_value,
                        language_value,
                    )

                if user_before is None:
                    raise ValueError("failed to create/update user for membership sync")

                now_utc = _utcnow()
                current_expires_at = user_before["subscription_expires_at"]
                if (
                    isinstance(current_expires_at, datetime)
                    and current_expires_at > now_utc
                ):
                    next_expires_at = current_expires_at
                    started_from_group_entry = False
                else:
                    next_expires_at = now_utc + timedelta(days=365)
                    started_from_group_entry = True

                user_after = await conn.fetchrow(
                    """
                    UPDATE users
                    SET
                        member_since = COALESCE(member_since, NOW()),
                        subscription_expires_at = $2,
                        subscription_status = 'ACTIVE',
                        last_membership_invite_link = NULL,
                        last_membership_invite_expires_at = NULL,
                        updated_at = NOW()
                    WHERE tg_user_id = $1
                    RETURNING *;
                    """,
                    int(tg_user_id),
                    next_expires_at,
                )
                if user_after is None:
                    raise ValueError("failed to update user subscription during membership sync")

                latest_app = await conn.fetchrow(
                    """
                    SELECT *
                    FROM applications
                    WHERE tg_user_id = $1
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE;
                    """,
                    int(tg_user_id),
                )

                created_application = False
                if latest_app is None:
                    latest_app = await conn.fetchrow(
                        """
                        INSERT INTO applications (
                            source,
                            status,
                            tg_user_id,
                            applicant_name,
                            approved_at
                        )
                        VALUES (
                            'manual',
                            'ACTIVE_MEMBER',
                            $1,
                            $2,
                            NOW()
                        )
                        RETURNING *;
                        """,
                        int(tg_user_id),
                        name_value,
                    )
                    created_application = True
                else:
                    latest_app = await conn.fetchrow(
                        """
                        UPDATE applications
                        SET
                            status = 'ACTIVE_MEMBER',
                            vote_status = CASE
                                WHEN status IN ('APPLICATION_PENDING', 'UNLINKED_APPLICATION_PENDING')
                                    THEN 'PROCESSED'
                                ELSE vote_status
                            END,
                            approved_at = COALESCE(approved_at, NOW()),
                            rejected_at = NULL,
                            updated_at = NOW()
                        WHERE id = $1
                        RETURNING *;
                        """,
                        int(latest_app["id"]),
                    )

                if latest_app is None:
                    raise ValueError("failed to sync application status during membership sync")

                return {
                    "user": _record_to_dict(user_after),
                    "application": _record_to_dict(latest_app),
                    "created_application": created_application,
                    "started_from_group_entry": started_from_group_entry,
                }

    async def block_user_access_from_membership_removal(
        self,
        *,
        tg_user_id: int,
        full_name: str | None = None,
        username: str | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        # Restrict bot access when user is removed from membership group.
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                name_value = self._clean_string(full_name) or f"User {int(tg_user_id)}"
                username_value = self._clean_string(username)
                language_value = self._clean_string(language_code)

                user_before = await conn.fetchrow(
                    """
                    SELECT *
                    FROM users
                    WHERE tg_user_id = $1
                    FOR UPDATE;
                    """,
                    int(tg_user_id),
                )
                if user_before is None:
                    user_before = await conn.fetchrow(
                        """
                        INSERT INTO users (tg_user_id, username, full_name, language_code, is_active)
                        VALUES ($1, $2, $3, $4, TRUE)
                        RETURNING *;
                        """,
                        int(tg_user_id),
                        username_value,
                        name_value,
                        language_value,
                    )

                user_after = await conn.fetchrow(
                    """
                    UPDATE users
                    SET
                        username = COALESCE($2, username),
                        full_name = COALESCE($3, full_name),
                        language_code = COALESCE($4, language_code),
                        subscription_status = 'BLOCKED',
                        last_membership_invite_link = NULL,
                        last_membership_invite_expires_at = NULL,
                        updated_at = NOW()
                    WHERE tg_user_id = $1
                    RETURNING *;
                    """,
                    int(tg_user_id),
                    username_value,
                    name_value,
                    language_value,
                )
                if user_after is None:
                    raise ValueError("failed to block user access after membership removal")

                return {"user": _record_to_dict(user_after)}

    async def get_user_by_tg_user_id(
        self, tg_user_id: int, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                "SELECT * FROM users WHERE tg_user_id = $1;",
                tg_user_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def get_user_panel_data(
        self,
        tg_user_id: int,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT
                    u.*,
                    app.id AS application_id,
                    app.status AS application_status
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT id, status
                    FROM applications
                    WHERE tg_user_id = u.tg_user_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) app ON TRUE
                WHERE u.tg_user_id = $1
                LIMIT 1;
                """,
                tg_user_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def list_active_members(
        self,
        limit: int,
        offset: int,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT
                    u.tg_user_id,
                    u.username,
                    u.full_name,
                    u.member_since,
                    u.subscription_expires_at,
                    u.subscription_status,
                    app.id AS application_id,
                    app.status AS application_status
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT id, status
                    FROM applications
                    WHERE tg_user_id = u.tg_user_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) app ON TRUE
                WHERE u.subscription_status = 'ACTIVE'
                  AND u.subscription_expires_at IS NOT NULL
                  AND u.subscription_expires_at > NOW()
                ORDER BY u.subscription_expires_at ASC, u.tg_user_id ASC
                LIMIT $1 OFFSET $2;
                """,
                limit,
                offset,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def list_pending_approval_members(
        self,
        limit: int,
        offset: int,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT
                    u.tg_user_id,
                    u.username,
                    u.full_name,
                    u.member_since,
                    u.subscription_expires_at,
                    u.subscription_status,
                    app.id AS application_id,
                    app.status AS application_status
                FROM users u
                JOIN LATERAL (
                    SELECT id, status, created_at
                    FROM applications
                    WHERE tg_user_id = u.tg_user_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) app ON TRUE
                WHERE app.status = 'APPLICATION_PENDING'
                ORDER BY app.created_at ASC, u.tg_user_id ASC
                LIMIT $1 OFFSET $2;
                """,
                limit,
                offset,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def list_expiring_members(
        self,
        max_days: int,
        limit: int,
        offset: int,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT
                    u.tg_user_id,
                    u.username,
                    u.full_name,
                    u.member_since,
                    u.subscription_expires_at,
                    u.subscription_status,
                    app.id AS application_id,
                    app.status AS application_status
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT id, status
                    FROM applications
                    WHERE tg_user_id = u.tg_user_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) app ON TRUE
                WHERE u.subscription_status = 'ACTIVE'
                  AND u.subscription_expires_at IS NOT NULL
                  AND u.subscription_expires_at > NOW()
                  AND u.subscription_expires_at <= (
                        NOW() + ($1::int * INTERVAL '1 day')
                  )
                ORDER BY u.subscription_expires_at ASC, u.tg_user_id ASC
                LIMIT $2 OFFSET $3;
                """,
                max_days,
                limit,
                offset,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def list_expired_members(
        self,
        limit: int,
        offset: int,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT
                    u.tg_user_id,
                    u.username,
                    u.full_name,
                    u.member_since,
                    u.subscription_expires_at,
                    u.subscription_status,
                    app.id AS application_id,
                    app.status AS application_status
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT id, status
                    FROM applications
                    WHERE tg_user_id = u.tg_user_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) app ON TRUE
                WHERE u.subscription_status = 'ACTIVE'
                  AND u.subscription_expires_at IS NOT NULL
                  AND u.subscription_expires_at <= NOW()
                ORDER BY u.subscription_expires_at DESC, u.tg_user_id ASC
                LIMIT $1 OFFSET $2;
                """,
                limit,
                offset,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def get_member_detail(
        self,
        tg_user_id: int,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT
                    u.tg_user_id,
                    u.username,
                    u.full_name,
                    u.member_since,
                    u.subscription_expires_at,
                    u.subscription_status,
                    app.id AS application_id,
                    app.status AS application_status
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT id, status
                    FROM applications
                    WHERE tg_user_id = u.tg_user_id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) app ON TRUE
                WHERE u.tg_user_id = $1
                LIMIT 1;
                """,
                tg_user_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def count_library_topics(
        self,
        *,
        include_inactive: bool = False,
        conn: Connection | None = None,
    ) -> int:
        # Count library topics for pagination.
        acquired = await self._acquire_conn(conn)
        try:
            value = await acquired.fetchval(
                """
                SELECT COUNT(*)
                FROM library_topics
                WHERE ($1::bool OR is_active = TRUE);
                """,
                bool(include_inactive),
            )
            return int(value or 0)
        finally:
            await self._release_conn(acquired, conn)

    async def list_library_topics(
        self,
        *,
        limit: int,
        offset: int,
        include_inactive: bool = False,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        # Paginated library topics ordered by sort_order.
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT id, title, sort_order, is_active, created_at, updated_at
                FROM library_topics
                WHERE ($1::bool OR is_active = TRUE)
                ORDER BY sort_order ASC, id ASC
                LIMIT $2 OFFSET $3;
                """,
                bool(include_inactive),
                int(limit),
                int(offset),
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def get_library_topic(
        self,
        *,
        topic_id: int,
        include_inactive: bool = False,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Get single topic by id.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT id, title, sort_order, is_active, created_at, updated_at
                FROM library_topics
                WHERE id = $1
                  AND ($2::bool OR is_active = TRUE)
                LIMIT 1;
                """,
                int(topic_id),
                bool(include_inactive),
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def create_library_topic(
        self,
        *,
        title: str,
        sort_order: int | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        # Create active library topic.
        normalized_title = self._clean_string(title)
        if not normalized_title:
            raise ValueError("topic title is required")

        acquired = await self._acquire_conn(conn)
        try:
            next_sort_order = sort_order
            if next_sort_order is None:
                value = await acquired.fetchval(
                    "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM library_topics;"
                )
                next_sort_order = int(value or 10)

            row = await acquired.fetchrow(
                """
                INSERT INTO library_topics (title, sort_order, is_active, updated_at)
                VALUES ($1, $2, TRUE, NOW())
                ON CONFLICT (title) DO UPDATE
                SET
                    is_active = TRUE,
                    updated_at = NOW()
                RETURNING id, title, sort_order, is_active, created_at, updated_at;
                """,
                normalized_title,
                int(next_sort_order),
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def update_library_topic(
        self,
        *,
        topic_id: int,
        title: str,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Rename topic.
        normalized_title = self._clean_string(title)
        if not normalized_title:
            raise ValueError("topic title is required")
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE library_topics
                SET
                    title = $2,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING id, title, sort_order, is_active, created_at, updated_at;
                """,
                int(topic_id),
                normalized_title,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def delete_library_topic(
        self,
        *,
        topic_id: int,
        conn: Connection | None = None,
    ) -> bool:
        # Delete topic with all related articles.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                "DELETE FROM library_topics WHERE id = $1 RETURNING id;",
                int(topic_id),
            )
            return row is not None
        finally:
            await self._release_conn(acquired, conn)

    async def count_library_articles(
        self,
        *,
        topic_id: int,
        include_inactive: bool = False,
        conn: Connection | None = None,
    ) -> int:
        # Count articles in topic for pagination.
        acquired = await self._acquire_conn(conn)
        try:
            value = await acquired.fetchval(
                """
                SELECT COUNT(*)
                FROM library_articles
                WHERE topic_id = $1
                  AND ($2::bool OR is_active = TRUE);
                """,
                int(topic_id),
                bool(include_inactive),
            )
            return int(value or 0)
        finally:
            await self._release_conn(acquired, conn)

    async def list_library_articles(
        self,
        *,
        topic_id: int,
        limit: int,
        offset: int,
        include_inactive: bool = False,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        # Paginated articles for selected topic.
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT
                    a.id,
                    a.topic_id,
                    a.title,
                    a.content,
                    a.sort_order,
                    a.is_active,
                    a.created_at,
                    a.updated_at
                FROM library_articles a
                WHERE a.topic_id = $1
                  AND ($2::bool OR a.is_active = TRUE)
                ORDER BY a.sort_order ASC, a.id ASC
                LIMIT $3 OFFSET $4;
                """,
                int(topic_id),
                bool(include_inactive),
                int(limit),
                int(offset),
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def get_library_article(
        self,
        *,
        article_id: int,
        include_inactive: bool = False,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Get single article with topic title.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT
                    a.id,
                    a.topic_id,
                    a.title,
                    a.content,
                    a.sort_order,
                    a.is_active,
                    a.created_at,
                    a.updated_at,
                    t.title AS topic_title
                FROM library_articles a
                JOIN library_topics t ON t.id = a.topic_id
                WHERE a.id = $1
                  AND ($2::bool OR a.is_active = TRUE)
                LIMIT 1;
                """,
                int(article_id),
                bool(include_inactive),
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def create_library_article(
        self,
        *,
        topic_id: int,
        title: str,
        content: str,
        sort_order: int | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        # Create article in topic.
        normalized_title = self._clean_string(title)
        normalized_content = self._clean_string(content)
        if not normalized_title:
            raise ValueError("article title is required")
        if not normalized_content:
            raise ValueError("article content is required")

        acquired = await self._acquire_conn(conn)
        try:
            next_sort_order = sort_order
            if next_sort_order is None:
                value = await acquired.fetchval(
                    """
                    SELECT COALESCE(MAX(sort_order), 0) + 10
                    FROM library_articles
                    WHERE topic_id = $1;
                    """,
                    int(topic_id),
                )
                next_sort_order = int(value or 10)

            row = await acquired.fetchrow(
                """
                INSERT INTO library_articles (
                    topic_id, title, content, sort_order, is_active, updated_at
                )
                VALUES ($1, $2, $3, $4, TRUE, NOW())
                RETURNING id, topic_id, title, content, sort_order, is_active, created_at, updated_at;
                """,
                int(topic_id),
                normalized_title,
                normalized_content,
                int(next_sort_order),
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def update_library_article(
        self,
        *,
        article_id: int,
        title: str,
        content: str,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        # Update article title/content.
        normalized_title = self._clean_string(title)
        normalized_content = self._clean_string(content)
        if not normalized_title:
            raise ValueError("article title is required")
        if not normalized_content:
            raise ValueError("article content is required")

        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE library_articles
                SET
                    title = $2,
                    content = $3,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING id, topic_id, title, content, sort_order, is_active, created_at, updated_at;
                """,
                int(article_id),
                normalized_title,
                normalized_content,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def delete_library_article(
        self,
        *,
        article_id: int,
        conn: Connection | None = None,
    ) -> bool:
        # Delete article by id.
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                "DELETE FROM library_articles WHERE id = $1 RETURNING id;",
                int(article_id),
            )
            return row is not None
        finally:
            await self._release_conn(acquired, conn)

    async def get_users_with_subscription_expiring(
        self,
        days_left: int,
        limit: int = 1000,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT u.*
                FROM users u
                WHERE u.subscription_status = 'ACTIVE'
                  AND u.subscription_expires_at IS NOT NULL
                  AND DATE(u.subscription_expires_at AT TIME ZONE 'UTC') =
                      DATE((NOW() AT TIME ZONE 'UTC') + ($1::int * INTERVAL '1 day'))
                  AND NOT EXISTS (
                    SELECT 1
                    FROM renewal_notifications rn
                    WHERE rn.tg_user_id = u.tg_user_id
                      AND rn.subscription_expires_at = u.subscription_expires_at
                      AND rn.days_left = $1
                  )
                ORDER BY u.subscription_expires_at ASC
                LIMIT $2;
                """,
                days_left,
                limit,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def mark_renewal_notified(
        self,
        tg_user_id: int,
        subscription_expires_at: datetime,
        days_left: int,
        conn: Connection | None = None,
    ) -> bool:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                INSERT INTO renewal_notifications (
                    tg_user_id, subscription_expires_at, days_left
                )
                VALUES ($1, $2, $3)
                ON CONFLICT (tg_user_id, subscription_expires_at, days_left) DO NOTHING
                RETURNING id;
                """,
                tg_user_id,
                subscription_expires_at,
                days_left,
            )
            return row is not None
        finally:
            await self._release_conn(acquired, conn)

    async def get_users_with_expired_subscription(
        self,
        limit: int = 1000,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT *
                FROM users
                WHERE subscription_status = 'ACTIVE'
                  AND subscription_expires_at IS NOT NULL
                  AND subscription_expires_at <= NOW()
                ORDER BY subscription_expires_at ASC
                LIMIT $1;
                """,
                limit,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def create_manual_application(
        self,
        tg_user_id: int,
        applicant_name: str | None = None,
        status: str = "APPROVED_AWAITING_PAYMENT",
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                INSERT INTO applications (
                    source,
                    status,
                    tg_user_id,
                    applicant_name
                )
                VALUES (
                    'manual',
                    $1,
                    $2,
                    $3
                )
                RETURNING *;
                """,
                status,
                tg_user_id,
                self._clean_string(applicant_name),
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def get_application_by_id(
        self, application_id: int, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                "SELECT * FROM applications WHERE id = $1;",
                application_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def find_linked_tg_user_id_by_contacts(
        self,
        contact_phone: str | None,
        contact_email: str | None,
        conn: Connection | None = None,
    ) -> int | None:
        # Find latest Telegram-linked user by matching phone/email in applications.
        phone = self._clean_string(contact_phone)
        email = self._clean_string(contact_email)
        phone_digits = "".join(ch for ch in phone if ch.isdigit()) if phone else ""
        phone_tail10 = phone_digits[-10:] if len(phone_digits) >= 10 else None
        if phone_tail10 is None and email is None:
            return None

        acquired = await self._acquire_conn(conn)
        try:
            tg_user_id = await acquired.fetchval(
                """
                SELECT a.tg_user_id
                FROM applications a
                WHERE a.tg_user_id IS NOT NULL
                  AND (
                    (
                        $1::text IS NOT NULL
                        AND right(regexp_replace(COALESCE(a.contact_phone, ''), '\\D', '', 'g'), 10) = $1
                    )
                    OR (
                        $2::text IS NOT NULL
                        AND a.contact_email IS NOT NULL
                        AND lower(a.contact_email) = lower($2)
                    )
                  )
                ORDER BY a.created_at DESC
                LIMIT 1;
                """,
                phone_tail10,
                email,
            )
            if tg_user_id is None:
                return None
            return int(tg_user_id)
        finally:
            await self._release_conn(acquired, conn)

    async def update_application_status(
        self,
        application_id: int,
        status: str,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE applications
                SET status = $2, updated_at = NOW()
                WHERE id = $1
                RETURNING *;
                """,
                application_id,
                status,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def approve_pending_application_for_user(
        self,
        tg_user_id: int,
    ) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                latest = await conn.fetchrow(
                    """
                    SELECT *
                    FROM applications
                    WHERE tg_user_id = $1
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE;
                    """,
                    tg_user_id,
                )
                if latest is None:
                    return None

                current_status = str(latest["status"] or "")
                if current_status == "APPLICATION_PENDING":
                    target_status = "APPROVED_AWAITING_PAYMENT"
                elif current_status == "UNLINKED_APPLICATION_PENDING":
                    target_status = "UNLINKED_APPLICATION_APPROVED"
                else:
                    return dict(latest)

                updated = await conn.fetchrow(
                    """
                    UPDATE applications
                    SET
                        status = $2,
                        vote_status = 'PROCESSED',
                        approved_at = COALESCE(approved_at, NOW()),
                        rejected_at = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *;
                    """,
                    int(latest["id"]),
                    target_status,
                )
                return _record_to_dict(updated)

    async def create_payment(
        self,
        application_id: int,
        amount_minor: int,
        currency: str,
        provider: str = "telegram",
        provider_payment_id: str | None = None,
        provider_order_id: str | None = None,
        provider_status: str | None = None,
        callback_data: str | None = None,
        callback_signature: str | None = None,
        signature_valid: bool | None = None,
        status: str = "PENDING",
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                INSERT INTO payments (
                    application_id,
                    provider,
                    provider_payment_id,
                    provider_order_id,
                    provider_status,
                    callback_data,
                    callback_signature,
                    signature_valid,
                    amount_minor,
                    currency,
                    status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING *;
                """,
                application_id,
                provider,
                provider_payment_id,
                provider_order_id,
                provider_status,
                callback_data,
                callback_signature,
                signature_valid,
                amount_minor,
                currency,
                status,
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def get_payment_by_id(
        self, payment_id: int, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                "SELECT * FROM payments WHERE id = $1;",
                payment_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def get_payment_by_provider_order_id(
        self,
        provider_order_id: str,
        provider: str = "liqpay",
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT *
                FROM payments
                WHERE provider = $1
                  AND provider_order_id = $2
                ORDER BY id DESC
                LIMIT 1;
                """,
                provider,
                provider_order_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def update_payment_status(
        self,
        payment_id: int,
        new_status: str,
        provider_payment_id: str | None = None,
    ) -> dict[str, Any] | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                payment = await conn.fetchrow(
                    "SELECT * FROM payments WHERE id = $1 FOR UPDATE;",
                    payment_id,
                )
                if payment is None:
                    return None

                paid_at = _utcnow() if new_status == "PAID" else None
                updated_payment = await conn.fetchrow(
                    """
                    UPDATE payments
                    SET
                        status = $2,
                        provider_payment_id = COALESCE($3, provider_payment_id),
                        paid_at = CASE WHEN $2 = 'PAID' THEN $4 ELSE paid_at END,
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *;
                    """,
                    payment_id,
                    new_status,
                    provider_payment_id,
                    paid_at,
                )

                if new_status == "PAID":
                    await conn.execute(
                        """
                        UPDATE applications
                        SET
                            status = 'PAID_AWAITING_JOIN',
                            vote_status = CASE
                                WHEN status = 'APPLICATION_PENDING' THEN 'PROCESSED'
                                ELSE vote_status
                            END,
                            approved_at = CASE
                                WHEN status IN ('APPROVED_AWAITING_PAYMENT', 'UNLINKED_APPLICATION_APPROVED', 'APPLICATION_PENDING')
                                    THEN COALESCE(approved_at, NOW())
                                ELSE approved_at
                            END,
                            rejected_at = NULL,
                            updated_at = NOW()
                        WHERE id = $1
                          AND status IN ('APPROVED_AWAITING_PAYMENT', 'UNLINKED_APPLICATION_APPROVED', 'APPLICATION_PENDING');
                        """,
                        payment["application_id"],
                    )

                return _record_to_dict(updated_payment)

    async def update_liqpay_callback_audit(
        self,
        provider_order_id: str,
        provider_status: str | None,
        callback_data: str,
        callback_signature: str,
        signature_valid: bool,
        provider: str = "liqpay",
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE payments
                SET
                    provider_status = $3,
                    callback_data = $4,
                    callback_signature = $5,
                    signature_valid = $6,
                    updated_at = NOW()
                WHERE provider = $1
                  AND provider_order_id = $2
                RETURNING *;
                """,
                provider,
                provider_order_id,
                provider_status,
                callback_data,
                callback_signature,
                signature_valid,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def process_liqpay_success_callback(
        self,
        provider_order_id: str,
        amount_minor: int,
        currency: str,
        provider_status: str,
        callback_data: str,
        callback_signature: str,
        signature_valid: bool,
    ) -> dict[str, Any]:
        if provider_status != "success":
            raise ValueError("provider_status must be 'success' for success processing")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                payment_before = await conn.fetchrow(
                    """
                    SELECT *
                    FROM payments
                    WHERE provider = 'liqpay'
                      AND provider_order_id = $1
                    FOR UPDATE;
                    """,
                    provider_order_id,
                )
                if payment_before is None:
                    raise ValueError("payment with provider_order_id not found")

                payment_with_audit = await conn.fetchrow(
                    """
                    UPDATE payments
                    SET
                        provider_status = $2,
                        callback_data = $3,
                        callback_signature = $4,
                        signature_valid = $5,
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *;
                    """,
                    int(payment_before["id"]),
                    provider_status,
                    callback_data,
                    callback_signature,
                    signature_valid,
                )
                if payment_with_audit is None:
                    raise ValueError("payment not found during callback audit update")

                if int(payment_with_audit["amount_minor"]) != int(amount_minor):
                    raise ValueError("callback amount mismatch")
                if str(payment_with_audit["currency"]).upper() != str(currency).upper():
                    raise ValueError("callback currency mismatch")

                app_before = await conn.fetchrow(
                    """
                    SELECT *
                    FROM applications
                    WHERE id = $1
                    FOR UPDATE;
                    """,
                    int(payment_with_audit["application_id"]),
                )
                if app_before is None:
                    raise ValueError("application not found for payment")

                if str(payment_with_audit["status"]) == "PAID":
                    user_existing = None
                    tg_user_id = app_before["tg_user_id"]
                    if tg_user_id is not None:
                        user_existing = await conn.fetchrow(
                            "SELECT * FROM users WHERE tg_user_id = $1;",
                            int(tg_user_id),
                        )

                    return {
                        "payment": _record_to_dict(payment_with_audit),
                        "application": _record_to_dict(app_before),
                        "user": _record_to_dict(user_existing),
                        "paid_now": False,
                    }

                payment_paid = await conn.fetchrow(
                    """
                    UPDATE payments
                    SET
                        status = 'PAID',
                        paid_at = COALESCE(paid_at, NOW()),
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *;
                    """,
                    int(payment_with_audit["id"]),
                )
                if payment_paid is None:
                    raise ValueError("payment not found while marking as PAID")

                await conn.execute(
                    """
                    UPDATE applications
                    SET
                        status = 'PAID_AWAITING_JOIN',
                        vote_status = CASE
                            WHEN status = 'APPLICATION_PENDING' THEN 'PROCESSED'
                            ELSE vote_status
                        END,
                        approved_at = CASE
                            WHEN status IN ('APPROVED_AWAITING_PAYMENT', 'UNLINKED_APPLICATION_APPROVED', 'APPLICATION_PENDING')
                                THEN COALESCE(approved_at, NOW())
                            ELSE approved_at
                        END,
                        rejected_at = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                      AND status IN ('APPROVED_AWAITING_PAYMENT', 'UNLINKED_APPLICATION_APPROVED', 'APPLICATION_PENDING');
                    """,
                    int(app_before["id"]),
                )
                app_after = await conn.fetchrow(
                    "SELECT * FROM applications WHERE id = $1;",
                    int(app_before["id"]),
                )

                user_after = None
                tg_user_id = app_before["tg_user_id"]
                if tg_user_id is not None:
                    user_before = await conn.fetchrow(
                        """
                        SELECT *
                        FROM users
                        WHERE tg_user_id = $1
                        FOR UPDATE;
                        """,
                        int(tg_user_id),
                    )

                    now_utc = _utcnow()
                    expires_at = (
                        user_before["subscription_expires_at"] if user_before else None
                    )
                    base_ts = (
                        expires_at
                        if isinstance(expires_at, datetime) and expires_at > now_utc
                        else now_utc
                    )
                    new_expires_at = base_ts + timedelta(days=365)

                    user_after = await conn.fetchrow(
                        """
                        UPDATE users
                        SET
                            member_since = COALESCE(member_since, NOW()),
                            subscription_expires_at = $2,
                            subscription_status = 'ACTIVE',
                            updated_at = NOW()
                        WHERE tg_user_id = $1
                        RETURNING *;
                        """,
                        int(tg_user_id),
                        new_expires_at,
                    )

                return {
                    "payment": _record_to_dict(payment_paid),
                    "application": _record_to_dict(app_after),
                    "user": _record_to_dict(user_after),
                    "paid_now": True,
                }

    async def create_application_token(
        self,
        tg_user_id: int,
        expires_at: datetime,
        token: str | None = None,
        application_id: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        resolved_token = token or uuid4().hex
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                INSERT INTO application_tokens (
                    token, tg_user_id, application_id, expires_at, metadata
                )
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING *;
                """,
                resolved_token,
                tg_user_id,
                application_id,
                expires_at,
                _to_jsonb(metadata),
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def get_active_application_token(
        self, tg_user_id: int, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                SELECT *
                FROM application_tokens
                WHERE tg_user_id = $1
                  AND used_at IS NULL
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                tg_user_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def get_token_record(
        self, token: str, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                "SELECT * FROM application_tokens WHERE token = $1;",
                token,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def list_application_token_records_for_user(
        self,
        tg_user_id: int,
        limit: int = 20,
        conn: Connection | None = None,
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT *
                FROM application_tokens
                WHERE tg_user_id = $1
                ORDER BY created_at DESC
                LIMIT $2;
                """,
                tg_user_id,
                limit,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def mark_application_token_used(
        self,
        token: str,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE application_tokens
                SET used_at = NOW()
                WHERE token = $1
                  AND used_at IS NULL
                RETURNING *;
                """,
                token,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def merge_application_token_metadata(
        self,
        token: str,
        metadata: Mapping[str, Any],
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE application_tokens
                SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb
                WHERE token = $1
                RETURNING *;
                """,
                token,
                _to_jsonb(metadata),
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def create_bind_token(
        self,
        application_id: int,
        expires_at: datetime,
        token: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any]:
        resolved_token = token or uuid4().hex
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                INSERT INTO bind_tokens (
                    token, application_id, expires_at, metadata
                )
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING *;
                """,
                resolved_token,
                application_id,
                expires_at,
                _to_jsonb(metadata),
            )
            return _record_to_dict(row) or {}
        finally:
            await self._release_conn(acquired, conn)

    async def get_bind_token(
        self, token: str, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                "SELECT * FROM bind_tokens WHERE token = $1;",
                token,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def mark_bind_token_used(
        self,
        token: str,
        tg_user_id: int | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE bind_tokens
                SET
                    used_at = NOW(),
                    claimed_tg_user_id = COALESCE($2, claimed_tg_user_id)
                WHERE token = $1
                  AND used_at IS NULL
                RETURNING *;
                """,
                token,
                tg_user_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def webhook_event_exists(
        self,
        event_key: str,
        provider: str = "weblium",
        conn: Connection | None = None,
    ) -> bool:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchval(
                """
                SELECT 1
                FROM webhook_events
                WHERE provider = $1 AND event_key = $2
                LIMIT 1;
                """,
                provider,
                event_key,
            )
            return row is not None
        finally:
            await self._release_conn(acquired, conn)

    async def create_webhook_event(
        self,
        event_key: str,
        request_path: str,
        source_ip: str | None = None,
        headers: Mapping[str, Any] | None = None,
        query_params: Mapping[str, Any] | None = None,
        payload_raw: str | None = None,
        payload_json: Mapping[str, Any] | None = None,
        provider: str = "weblium",
    ) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO webhook_events (
                    provider, event_key, request_path, source_ip, headers, query_params, payload_raw, payload_json
                )
                VALUES (
                    $1, $2, $3, $4::inet, $5::jsonb, $6::jsonb, $7, $8::jsonb
                )
                ON CONFLICT (provider, event_key) DO NOTHING
                RETURNING *, TRUE AS is_new;
                """,
                provider,
                event_key,
                request_path,
                source_ip,
                _to_jsonb(headers),
                _to_jsonb(query_params),
                payload_raw,
                _to_jsonb(payload_json),
            )

            if row is not None:
                return _record_to_dict(row) or {}

            existing = await conn.fetchrow(
                """
                SELECT *, FALSE AS is_new
                FROM webhook_events
                WHERE provider = $1 AND event_key = $2;
                """,
                provider,
                event_key,
            )
            return _record_to_dict(existing) or {}

    async def mark_webhook_event_processed(
        self,
        event_id: int,
        processing_status: str = "PROCESSED",
        processing_error: str | None = None,
        application_id: int | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        acquired = await self._acquire_conn(conn)
        try:
            row = await acquired.fetchrow(
                """
                UPDATE webhook_events
                SET
                    processing_status = $2,
                    processing_error = $3,
                    application_id = COALESCE($4, application_id),
                    processed_at = NOW()
                WHERE id = $1
                RETURNING *;
                """,
                event_id,
                processing_status,
                processing_error,
                application_id,
            )
            return _record_to_dict(row)
        finally:
            await self._release_conn(acquired, conn)

    async def create_matched_application_from_webhook(
        self,
        tg_user_id: int,
        payload_json: Mapping[str, Any],
        application_token: str | None = None,
        webhook_event_id: int | None = None,
    ) -> dict[str, Any]:
        extracted = self._extract_weblium_fields(payload_json)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if application_token:
                    token_row = await conn.fetchrow(
                        """
                        SELECT *
                        FROM application_tokens
                        WHERE token = $1
                          AND used_at IS NULL
                          AND expires_at > NOW()
                        FOR UPDATE;
                        """,
                        application_token,
                    )
                    if token_row is None:
                        raise ValueError("application token is missing/expired/already used")
                    if token_row["tg_user_id"] != tg_user_id:
                        raise ValueError("application token does not belong to this tg_user_id")

                    await conn.execute(
                        """
                        UPDATE application_tokens
                        SET used_at = NOW()
                        WHERE id = $1;
                        """,
                        token_row["id"],
                    )

                app_row = await conn.fetchrow(
                    """
                    INSERT INTO applications (
                        source,
                        status,
                        tg_user_id,
                        contact_phone,
                        contact_email,
                        applicant_name,
                        specialization,
                        document_url,
                        document_file_name,
                        weblium_referer
                    )
                    VALUES (
                        'bot_link',
                        'APPLICATION_PENDING',
                        $1, $2, $3, $4, $5, $6, $7, $8
                    )
                    RETURNING *;
                    """,
                    tg_user_id,
                    extracted["contact_phone"],
                    extracted["contact_email"],
                    extracted["applicant_name"],
                    extracted["specialization"],
                    extracted["document_url"],
                    extracted["document_file_name"],
                    extracted["weblium_referer"],
                )

                if webhook_event_id is not None:
                    await conn.execute(
                        """
                        UPDATE webhook_events
                        SET
                            application_id = $2,
                            processing_status = 'PROCESSED',
                            processed_at = NOW()
                        WHERE id = $1;
                        """,
                        webhook_event_id,
                        app_row["id"],
                    )

                return _record_to_dict(app_row) or {}

    async def create_unlinked_application_from_webhook(
        self,
        payload_json: Mapping[str, Any],
        webhook_event_id: int | None = None,
    ) -> dict[str, Any]:
        extracted = self._extract_weblium_fields(payload_json)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                app_row = await conn.fetchrow(
                    """
                    INSERT INTO applications (
                        source,
                        status,
                        tg_user_id,
                        contact_phone,
                        contact_email,
                        applicant_name,
                        specialization,
                        document_url,
                        document_file_name,
                        weblium_referer
                    )
                    VALUES (
                        'site_direct',
                        'UNLINKED_APPLICATION_PENDING',
                        NULL, $1, $2, $3, $4, $5, $6, $7
                    )
                    RETURNING *;
                    """,
                    extracted["contact_phone"],
                    extracted["contact_email"],
                    extracted["applicant_name"],
                    extracted["specialization"],
                    extracted["document_url"],
                    extracted["document_file_name"],
                    extracted["weblium_referer"],
                )

                if webhook_event_id is not None:
                    await conn.execute(
                        """
                        UPDATE webhook_events
                        SET
                            application_id = $2,
                            processing_status = 'PROCESSED',
                            processed_at = NOW()
                        WHERE id = $1;
                        """,
                        webhook_event_id,
                        app_row["id"],
                    )

                return _record_to_dict(app_row) or {}

    async def get_unlinked_application_candidates_by_phone(
        self, phone: str, limit: int = 20, conn: Connection | None = None
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT *
                FROM applications
                WHERE tg_user_id IS NULL
                  AND contact_phone = $1
                  AND status IN ('UNLINKED_APPLICATION_PENDING', 'UNLINKED_APPLICATION_APPROVED')
                ORDER BY created_at DESC
                LIMIT $2;
                """,
                phone,
                limit,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def get_unlinked_application_candidates_by_email(
        self, email: str, limit: int = 20, conn: Connection | None = None
    ) -> list[dict[str, Any]]:
        acquired = await self._acquire_conn(conn)
        try:
            rows = await acquired.fetch(
                """
                SELECT *
                FROM applications
                WHERE tg_user_id IS NULL
                  AND lower(contact_email) = lower($1)
                  AND status IN ('UNLINKED_APPLICATION_PENDING', 'UNLINKED_APPLICATION_APPROVED')
                ORDER BY created_at DESC
                LIMIT $2;
                """,
                email,
                limit,
            )
            return [dict(row) for row in rows]
        finally:
            await self._release_conn(acquired, conn)

    async def bind_application_to_tg_user(
        self,
        application_id: int,
        tg_user_id: int,
        bind_token: str | None = None,
        new_status: str = "APPLICATION_PENDING",
    ) -> dict[str, Any]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                app_before = await conn.fetchrow(
                    """
                    SELECT *
                    FROM applications
                    WHERE id = $1
                    FOR UPDATE;
                    """,
                    application_id,
                )
                if app_before is None:
                    raise ValueError("application not found")

                current_tg_user_id = app_before["tg_user_id"]
                if current_tg_user_id is not None:
                    if (
                        current_tg_user_id == tg_user_id
                        and app_before["status"] == new_status
                    ):
                        return _record_to_dict(app_before) or {}
                    raise ValueError("application already linked to another tg_user_id")

                if app_before["status"] not in {
                    "UNLINKED_APPLICATION_PENDING",
                    "UNLINKED_APPLICATION_APPROVED",
                }:
                    raise ValueError(
                        f"application is not bindable from status {app_before['status']}"
                    )

                if bind_token:
                    bind_row = await conn.fetchrow(
                        """
                        SELECT *
                        FROM bind_tokens
                        WHERE token = $1
                          AND used_at IS NULL
                          AND expires_at > NOW()
                        FOR UPDATE;
                        """,
                        bind_token,
                    )
                    if bind_row is None:
                        raise ValueError("bind token is missing/expired/already used")
                    if bind_row["application_id"] != application_id:
                        raise ValueError("bind token does not match application_id")

                    await conn.execute(
                        """
                        UPDATE bind_tokens
                        SET used_at = NOW(), claimed_tg_user_id = $2
                        WHERE id = $1;
                        """,
                        bind_row["id"],
                        tg_user_id,
                    )

                app_row = await conn.fetchrow(
                    """
                    UPDATE applications
                    SET
                        tg_user_id = $2,
                        status = $3,
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *;
                    """,
                    application_id,
                    tg_user_id,
                    new_status,
                )
                if app_row is None:
                    raise ValueError("application not found")

                return _record_to_dict(app_row) or {}





