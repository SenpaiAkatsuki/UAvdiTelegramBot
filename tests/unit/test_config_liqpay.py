from decimal import Decimal

import pytest

from tgbot.config import LiqPayConfig


def make_liqpay_config(**overrides) -> LiqPayConfig:
    payload = {
        "public_key": "sandbox_public",
        "private_key": "sandbox_private",
        "currency": "UAH",
        "amount": Decimal("100.00"),
        "public_base_url": "https://example.com",
        "callback_path": "/webhooks/liqpay/callback",
        "pay_path": "/pay/liqpay/{payment_id}",
    }
    payload.update(overrides)
    return LiqPayConfig(**payload)


def test_liqpay_amount_minor_rounding() -> None:
    config = make_liqpay_config(amount=Decimal("100.015"))
    assert config.amount_minor == 10002


def test_liqpay_build_urls() -> None:
    config = make_liqpay_config()
    assert config.build_pay_url(42) == "https://example.com/pay/liqpay/42"
    assert config.callback_url() == "https://example.com/webhooks/liqpay/callback"


def test_liqpay_validate_requires_pay_path_placeholder() -> None:
    config = make_liqpay_config(pay_path="/pay/liqpay")
    with pytest.raises(ValueError, match="must include '\\{payment_id\\}'"):
        config.validate(payments_enabled=False)


def test_liqpay_validate_allows_empty_secrets_when_payments_disabled() -> None:
    config = make_liqpay_config(
        public_key="",
        private_key="",
        public_base_url="",
        amount=Decimal("0"),
    )
    config.validate(payments_enabled=False)


def test_liqpay_validate_fails_when_payments_enabled() -> None:
    config = make_liqpay_config(
        public_key="",
        private_key="",
        public_base_url="",
        amount=Decimal("0"),
    )
    with pytest.raises(ValueError, match="Invalid LiqPay config"):
        config.validate(payments_enabled=True)
