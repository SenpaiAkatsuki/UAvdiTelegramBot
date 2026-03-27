import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web

from bot import on_error
from infrastructure.api.weblium_app import unhandled_error_middleware


def test_on_error_answers_callback_query() -> None:
    callback_query = SimpleNamespace(answer=AsyncMock())
    event = SimpleNamespace(
        update=SimpleNamespace(update_id=1, callback_query=callback_query, message=None),
        exception=RuntimeError("boom"),
    )

    handled = asyncio.run(on_error(event))

    assert handled is True
    callback_query.answer.assert_awaited_once()


def test_on_error_answers_message() -> None:
    message = SimpleNamespace(answer=AsyncMock())
    event = SimpleNamespace(
        update=SimpleNamespace(update_id=2, callback_query=None, message=message),
        exception=RuntimeError("boom"),
    )

    handled = asyncio.run(on_error(event))

    assert handled is True
    message.answer.assert_awaited_once()


def test_webhook_error_middleware_returns_json_for_webhook_paths() -> None:
    request = SimpleNamespace(method="POST", path="/webhooks/weblium/application")

    async def broken_handler(_request):
        raise RuntimeError("boom")

    response = asyncio.run(unhandled_error_middleware(request, broken_handler))

    assert isinstance(response, web.Response)
    assert response.status == 500
    assert response.content_type == "application/json"


def test_webhook_error_middleware_returns_plain_500_for_non_webhook_paths() -> None:
    request = SimpleNamespace(method="GET", path="/pay/liqpay/1")

    async def broken_handler(_request):
        raise RuntimeError("boom")

    response = asyncio.run(unhandled_error_middleware(request, broken_handler))

    assert isinstance(response, web.Response)
    assert response.status == 500
    assert response.content_type == "text/plain"
