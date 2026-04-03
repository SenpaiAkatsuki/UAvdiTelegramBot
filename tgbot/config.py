from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from ipaddress import ip_network
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from environs import Env

"""
Application config models and env parsing helpers.

Loads runtime configuration for bot, DB, payments, webhook, voting, and throttling.
"""


def _env_non_empty_or_default(env: Env, key: str, default: str) -> str:
    # Read string env value and fallback to default when empty/placeholder.
    raw = env.str(key, "")
    value = raw.strip()

    # Support inline-comment style placeholders like:
    # KEY= # optional override
    # or
    # KEY=/path # optional override
    if "#" in value:
        hash_index = value.find("#")
        if hash_index == 0:
            value = ""
        else:
            value = value[:hash_index].rstrip()

    return value or default


def _env_non_negative_float(env: Env, key: str, default: float) -> float:
    # Read non-negative float from env with validation.
    raw = env.str(key, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if value < 0:
        raise ValueError(f"{key} must be >= 0")
    return value


def _env_positive_int(env: Env, key: str, default: int) -> int:
    # Read positive integer from env with validation.
    raw = env.str(key, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{key} must be > 0")
    return value


@dataclass
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    min_pool_size: int = 1
    max_pool_size: int = 10

    def dsn(self) -> str:
        # Build asyncpg DSN string.
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{self.database}"

    @staticmethod
    def from_env(env: Env) -> "DbConfig":
        # Parse DB settings from env.
        return DbConfig(
            host=env.str("DB_HOST", "127.0.0.1"),
            port=env.int("DB_PORT", 5432),
            user=env.str("DB_USER", "postgres"),
            password=env.str("DB_PASSWORD", "postgres"),
            database=env.str("DB_NAME", "bot"),
            min_pool_size=env.int("DB_MIN_POOL_SIZE", 1),
            max_pool_size=env.int("DB_MAX_POOL_SIZE", 10),
        )


@dataclass
class TgBot:
    token: str
    admin_ids: list[int]
    use_redis: bool

    @staticmethod
    def from_env(env: Env) -> "TgBot":
        # Parse Telegram bot settings from env.
        return TgBot(
            token=env.str("BOT_TOKEN"),
            admin_ids=env.list("ADMINS", subcast=int),
            use_redis=env.bool("USE_REDIS", False),
        )


@dataclass
class RedisConfig:
    redis_pass: Optional[str]
    redis_port: int
    redis_host: str

    def dsn(self) -> str:
        # Build Redis DSN string.
        if self.redis_pass:
            return f"redis://:{self.redis_pass}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @staticmethod
    def from_env(env: Env) -> "RedisConfig":
        # Parse Redis settings from env.
        return RedisConfig(
            redis_pass=env.str("REDIS_PASSWORD", ""),
            redis_port=env.int("REDIS_PORT"),
            redis_host=env.str("REDIS_HOST"),
        )


@dataclass
class ChatConfig:
    membership_chat_id: int
    applications_chat_id: int

    @staticmethod
    def from_env(env: Env) -> "ChatConfig":
        # Parse chat ids from env.
        return ChatConfig(
            membership_chat_id=env.int("CHAT_MEMBERSHIP_CHAT_ID", 0),
            applications_chat_id=env.int("CHAT_APPLICATIONS_CHAT_ID", 0),
        )


@dataclass
class PaymentsConfig:
    enabled: bool

    @staticmethod
    def from_env(env: Env) -> "PaymentsConfig":
        # Parse payment enable flag.
        return PaymentsConfig(
            enabled=env.bool("PAYMENTS_ENABLED", False),
        )


@dataclass
class ThrottlingConfig:
    enabled: bool
    window_seconds: float
    message_max_events: int
    command_max_events: int
    callback_max_events: int
    heavy_callback_max_events: int
    warning_cooldown_seconds: float

    @staticmethod
    def from_env(env: Env) -> "ThrottlingConfig":
        # Parse throttling limits and validation rules.
        window_seconds = _env_non_negative_float(
            env,
            "THROTTLE_WINDOW_SECONDS",
            10.0,
        )
        if window_seconds <= 0:
            raise ValueError("THROTTLE_WINDOW_SECONDS must be > 0")

        message_max_events = _env_positive_int(
            env,
            "THROTTLE_MESSAGE_MAX_EVENTS",
            5,
        )
        command_max_events = _env_positive_int(
            env,
            "THROTTLE_COMMAND_MAX_EVENTS",
            5,
        )
        callback_max_events = _env_positive_int(
            env,
            "THROTTLE_CALLBACK_MAX_EVENTS",
            8,
        )
        heavy_callback_max_events = _env_positive_int(
            env,
            "THROTTLE_HEAVY_CALLBACK_MAX_EVENTS",
            5,
        )
        warning_cooldown = _env_non_negative_float(
            env,
            "THROTTLE_WARNING_COOLDOWN_SECONDS",
            5.0,
        )

        return ThrottlingConfig(
            enabled=env.bool("THROTTLING_ENABLED", True),
            window_seconds=window_seconds,
            message_max_events=message_max_events,
            command_max_events=command_max_events,
            callback_max_events=callback_max_events,
            heavy_callback_max_events=min(
                heavy_callback_max_events,
                callback_max_events,
            ),
            warning_cooldown_seconds=warning_cooldown,
        )


@dataclass
class LiqPayConfig:
    public_key: str
    private_key: str
    currency: str
    amount: Decimal
    public_base_url: str
    callback_path: str
    pay_path: str

    @staticmethod
    def from_env(env: Env) -> "LiqPayConfig":
        # Parse LiqPay credentials, amount, and URL paths.
        amount_raw = env.str("LIQPAY_AMOUNT", "0").strip()
        try:
            amount = Decimal(amount_raw)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid LIQPAY_AMOUNT value: {amount_raw}") from exc

        public_base_url = env.str("PUBLIC_WEBHOOK_BASE_URL", "").strip().rstrip("/")
        if not public_base_url:
            # Backward compatibility for previous env key naming.
            public_base_url = env.str("PUBLIC_BASE_URL", "").strip().rstrip("/")

        return LiqPayConfig(
            public_key=env.str("LIQPAY_PUBLIC_KEY", "").strip(),
            private_key=env.str("LIQPAY_PRIVATE_KEY", "").strip(),
            currency=env.str("LIQPAY_CURRENCY", "UAH").strip().upper(),
            amount=amount,
            public_base_url=public_base_url,
            callback_path=_env_non_empty_or_default(
                env,
                "LIQPAY_CALLBACK_PATH",
                "/webhooks/liqpay/callback",
            ),
            pay_path=_env_non_empty_or_default(
                env,
                "LIQPAY_PAY_PATH",
                "/pay/liqpay/{payment_id}",
            ),
        )

    @property
    def amount_minor(self) -> int:
        # Convert decimal amount to minor units.
        return int(
            (self.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
            .to_integral_value(rounding=ROUND_HALF_UP)
        )

    def build_pay_url(self, payment_id: int) -> str:
        # Build user-facing payment link for checkout page.
        resolved_path = self.pay_path.format(payment_id=payment_id)
        return f"{self.public_base_url}{resolved_path}"

    def callback_url(self) -> str:
        # Build full callback URL for LiqPay server_url.
        return f"{self.public_base_url}{self.callback_path}"

    def validate(self, payments_enabled: bool) -> None:
        # Validate required LiqPay settings and path formats.
        if not self.callback_path.startswith("/"):
            raise ValueError("LIQPAY_CALLBACK_PATH must start with '/'")
        if not self.pay_path.startswith("/"):
            raise ValueError("LIQPAY_PAY_PATH must start with '/'")
        if "{payment_id}" not in self.pay_path:
            raise ValueError("LIQPAY_PAY_PATH must include '{payment_id}' placeholder")

        if not payments_enabled:
            return

        errors: list[str] = []
        if self.amount <= 0:
            errors.append("LIQPAY_AMOUNT must be > 0 when PAYMENTS_ENABLED=true")
        if not self.public_key:
            errors.append("LIQPAY_PUBLIC_KEY must not be empty when PAYMENTS_ENABLED=true")
        if not self.private_key:
            errors.append("LIQPAY_PRIVATE_KEY must not be empty when PAYMENTS_ENABLED=true")
        if not self.public_base_url:
            errors.append(
                "PUBLIC_WEBHOOK_BASE_URL (or legacy PUBLIC_BASE_URL) must not be empty when PAYMENTS_ENABLED=true"
            )
        elif not (
            self.public_base_url.startswith("https://")
            or self.public_base_url.startswith("http://")
        ):
            errors.append(
                "PUBLIC_WEBHOOK_BASE_URL (or legacy PUBLIC_BASE_URL) must start with http:// or https://"
            )
        if not self.currency:
            errors.append("LIQPAY_CURRENCY must not be empty when PAYMENTS_ENABLED=true")

        if errors:
            raise ValueError("Invalid LiqPay config:\n- " + "\n- ".join(errors))


@dataclass
class SubscriptionConfig:
    enforce_expired_removal: bool
    enforce_expired_removal_dry_run: bool
    enforce_expired_removal_max_per_run: int

    @staticmethod
    def from_env(env: Env) -> "SubscriptionConfig":
        # Parse expired-membership enforcement settings.
        max_per_run = env.int("ENFORCE_EXPIRED_REMOVAL_MAX_PER_RUN", 25)
        if max_per_run <= 0:
            raise ValueError("ENFORCE_EXPIRED_REMOVAL_MAX_PER_RUN must be > 0")
        return SubscriptionConfig(
            enforce_expired_removal=env.bool("ENFORCE_EXPIRED_REMOVAL", False),
            enforce_expired_removal_dry_run=env.bool(
                "ENFORCE_EXPIRED_REMOVAL_DRY_RUN",
                True,
            ),
            enforce_expired_removal_max_per_run=max_per_run,
        )


@dataclass
class MembershipConfig:
    application_url: str
    application_link_base_url: str
    fallback_manual_submit_enabled: bool
    bot_username: Optional[str] = None

    @staticmethod
    def from_env(env: Env) -> "MembershipConfig":
        # Parse membership form URLs and fallback flags.
        return MembershipConfig(
            application_url=env.str("MEMBERSHIP_APPLICATION_URL"),
            application_link_base_url=env.str("MEMBERSHIP_APPLICATION_LINK_BASE_URL"),
            fallback_manual_submit_enabled=env.bool(
                "MEMBERSHIP_FALLBACK_MANUAL_SUBMIT_ENABLED", True
            ),
            bot_username=None,
        )


@dataclass
class WebhookConfig:
    host: str
    port: int
    weblium_path: str
    weblium_secret: str
    trusted_proxy_ips: list[str]
    webhook_enabled: bool

    @staticmethod
    def from_env(env: Env) -> "WebhookConfig":
        # Parse webhook app host/port/security settings.
        return WebhookConfig(
            host=env.str("WEBHOOK_HOST", "0.0.0.0"),
            port=env.int("WEBHOOK_PORT", 8080),
            weblium_path=_env_non_empty_or_default(
                env,
                "WEBHOOK_WEBLIUM_PATH",
                "/webhooks/weblium/application",
            ),
            weblium_secret=env.str("WEBHOOK_WEBLIUM_SECRET", ""),
            trusted_proxy_ips=env.list(
                "WEBHOOK_TRUSTED_PROXY_IPS",
                subcast=str,
                default=[],
            ),
            webhook_enabled=env.bool("WEBHOOK_ENABLED", False),
        )

    def validate(self) -> None:
        # Validate webhook settings when webhook mode is enabled.
        if not self.webhook_enabled:
            return

        errors: list[str] = []

        if not self.host.strip():
            errors.append("WEBHOOK_HOST must not be empty when WEBHOOK_ENABLED=true")

        if not (1 <= self.port <= 65535):
            errors.append("WEBHOOK_PORT must be in range 1..65535")

        if not self.weblium_path.strip():
            errors.append(
                "WEBHOOK_WEBLIUM_PATH must not be empty when WEBHOOK_ENABLED=true"
            )
        elif not self.weblium_path.startswith("/"):
            errors.append("WEBHOOK_WEBLIUM_PATH must start with '/'")

        if not self.weblium_secret.strip():
            errors.append(
                "WEBHOOK_WEBLIUM_SECRET must not be empty when WEBHOOK_ENABLED=true"
            )

        for idx, value in enumerate(self.trusted_proxy_ips):
            if not value:
                continue
            try:
                ip_network(value, strict=False)
            except ValueError:
                errors.append(
                    f"WEBHOOK_TRUSTED_PROXY_IPS[{idx}] is invalid IP/CIDR: {value}"
                )

        if errors:
            raise ValueError("Invalid webhook config:\n- " + "\n- ".join(errors))


@dataclass
class VotingConfig:
    chat_id: int
    thread_id: int | None
    duration_seconds: int
    min_total: int | None
    require_yes_gt_no: bool
    allow_shared_chat: bool

    @staticmethod
    def from_env(env: Env) -> "VotingConfig":
        # Parse voting chat/topic thresholds and duration.
        thread_raw = env.str("VOTING_TOPIC_ID", "").strip()
        if not thread_raw:
            # Backward compatibility with previous key name.
            thread_raw = env.str("VOTING_THREAD_ID", "").strip()
        thread_id: int | None = int(thread_raw) if thread_raw else None

        min_total_raw = env.str("VOTE_MIN_TOTAL", "").strip()
        min_total: int | None = None
        if min_total_raw:
            # Target votes for one option (yes/no).
            parsed = int(min_total_raw)
            min_total = parsed if parsed > 0 else None

        return VotingConfig(
            chat_id=env.int("VOTING_CHAT_ID", 0),
            thread_id=thread_id,
            duration_seconds=env.int("VOTE_DURATION_SECONDS", 86400),
            min_total=min_total,
            require_yes_gt_no=env.bool("VOTE_REQUIRE_YES_GT_NO", True),
            allow_shared_chat=env.bool("VOTING_ALLOW_SHARED_CHAT", False),
        )

    def validate(self, membership_chat_id: int) -> None:
        # Validate voting config constraints.
        if self.chat_id == 0:
            raise ValueError("VOTING_CHAT_ID must be configured")
        if (
            not self.allow_shared_chat
            and membership_chat_id != 0
            and self.chat_id == membership_chat_id
        ):
            raise ValueError(
                "VOTING_CHAT_ID must be different from CHAT_MEMBERSHIP_CHAT_ID "
                "(or set VOTING_ALLOW_SHARED_CHAT=true)"
            )
        if self.duration_seconds < 0:
            raise ValueError("VOTE_DURATION_SECONDS must be >= 0")
        if self.thread_id is not None and self.thread_id <= 0:
            raise ValueError("VOTING_TOPIC_ID (or legacy VOTING_THREAD_ID) must be > 0 when set")


@dataclass
class Config:
    tg_bot: TgBot
    db: DbConfig
    chat: ChatConfig
    payments: PaymentsConfig
    throttling: ThrottlingConfig
    liqpay: LiqPayConfig
    subscription: SubscriptionConfig
    membership: MembershipConfig
    webhook: WebhookConfig
    voting: VotingConfig
    redis: Optional[RedisConfig] = None


def load_config(path: str = None) -> Config:
    # Load full app config from .env file and process-level environment.
    env = Env()
    resolved_path = path
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            cwd_candidate = Path.cwd() / candidate
            if cwd_candidate.exists():
                resolved_path = str(cwd_candidate)
            else:
                project_root_candidate = Path(__file__).resolve().parents[1] / candidate
                resolved_path = str(project_root_candidate)
        else:
            resolved_path = str(candidate)

    # Prioritize project `.env` over inherited OS/session env vars.
    # This avoids stale placeholder values (e.g. LIQPAY_* = change_me_*) shadowing local config.
    env.read_env(resolved_path, override=True)

    tg_bot = TgBot.from_env(env)
    payments = PaymentsConfig.from_env(env)
    throttling = ThrottlingConfig.from_env(env)
    liqpay = LiqPayConfig.from_env(env)
    liqpay.validate(payments_enabled=payments.enabled)
    subscription = SubscriptionConfig.from_env(env)
    chat = ChatConfig.from_env(env)

    webhook = WebhookConfig.from_env(env)
    webhook.validate()
    voting = VotingConfig.from_env(env)
    voting.validate(membership_chat_id=chat.membership_chat_id)

    redis = RedisConfig.from_env(env) if tg_bot.use_redis else None

    return Config(
        tg_bot=tg_bot,
        db=DbConfig.from_env(env),
        chat=chat,
        payments=payments,
        throttling=throttling,
        liqpay=liqpay,
        subscription=subscription,
        membership=MembershipConfig.from_env(env),
        webhook=webhook,
        voting=voting,
        redis=redis,
    )
