from types import SimpleNamespace

from yarl import URL

from infrastructure.api.weblium_app import (
    _amount_to_minor,
    _decode_liqpay_data,
    _encode_liqpay_data,
    _extract_tg_token_from_referer,
    _is_webhook_signature_valid,
    _liqpay_signature,
    normalize_weblium_payload,
)


def test_liqpay_data_roundtrip() -> None:
    payload = {"order_id": "liqpay_123", "status": "success", "amount": "100.00"}
    encoded = _encode_liqpay_data(payload)
    decoded = _decode_liqpay_data(encoded)
    assert decoded == payload


def test_liqpay_signature_matches_expected_sample() -> None:
    signature = _liqpay_signature(
        private_key="private_key",
        data="eyJvcmRlcl9pZCI6InRlc3QifQ==",
    )
    assert signature == "Dc3wKpTXZjuMiXCY0qiPQJExBLs="


def test_amount_to_minor() -> None:
    assert _amount_to_minor("100") == 10000
    assert _amount_to_minor("100.01") == 10001


def test_extract_tg_token_from_referer_query_and_fragment() -> None:
    assert (
        _extract_tg_token_from_referer("https://example.com/form?tg_token=query-token")
        == "query-token"
    )
    assert (
        _extract_tg_token_from_referer("https://example.com/#contact-form?tg_token=fragment-token")
        == "fragment-token"
    )


def test_normalize_weblium_payload_extracts_contact_fields() -> None:
    payload = {
        "form_name": "Contact",
        "fields": {
            "short_text": {"title": "Name", "value": "Jane Test", "type": "text"},
            "contactForm_phoneNumber": {
                "title": "Phone",
                "value": "+380501112233",
                "type": "phone",
            },
            "contactForm_email": {
                "title": "Email",
                "value": "jane@example.com",
                "type": "email",
            },
        },
        "referer": "https://example.com/#contact-form?tg_token=token-from-fragment",
    }
    request = SimpleNamespace(rel_url=URL("/webhooks/weblium/application"))

    normalized = normalize_weblium_payload(payload, request)

    assert normalized["applicant_name"] == "Jane Test"
    assert normalized["phone"] == "+380501112233"
    assert normalized["email"] == "jane@example.com"
    assert normalized["tg_token"] == "token-from-fragment"


def test_webhook_signature_validator_accepts_hex_prefixed_and_base64_formats() -> None:
    raw_body = b'{"event":"application"}'
    secret = "webhook_sig_secret"
    expected_hex = "82b276f950a29c80ef2954a8b2ea983cec5dc3e481e6753863093ade1f905303"
    expected_b64 = "grJ2+VCinIDvKVSosuqYPOxdw+SB5nU4Ywk63h+QUwM="
    request_plain = SimpleNamespace(headers={"X-Webhook-Signature": expected_hex})
    request_prefixed = SimpleNamespace(headers={"X-Webhook-Signature": f"sha256={expected_hex}"})
    request_base64 = SimpleNamespace(headers={"X-Webhook-Signature": expected_b64})

    assert _is_webhook_signature_valid(
        request=request_plain,
        raw_body=raw_body,
        signature_secret=secret,
        signature_header="X-Webhook-Signature",
    )
    assert _is_webhook_signature_valid(
        request=request_prefixed,
        raw_body=raw_body,
        signature_secret=secret,
        signature_header="X-Webhook-Signature",
    )
    assert _is_webhook_signature_valid(
        request=request_base64,
        raw_body=raw_body,
        signature_secret=secret,
        signature_header="X-Webhook-Signature",
    )


def test_webhook_signature_validator_rejects_missing_or_invalid_signature() -> None:
    raw_body = b'{"event":"application"}'
    request_missing = SimpleNamespace(headers={})
    request_invalid = SimpleNamespace(headers={"X-Webhook-Signature": "wrong-signature"})

    assert not _is_webhook_signature_valid(
        request=request_missing,
        raw_body=raw_body,
        signature_secret="webhook_sig_secret",
        signature_header="X-Webhook-Signature",
    )
    assert not _is_webhook_signature_valid(
        request=request_invalid,
        raw_body=raw_body,
        signature_secret="webhook_sig_secret",
        signature_header="X-Webhook-Signature",
    )
