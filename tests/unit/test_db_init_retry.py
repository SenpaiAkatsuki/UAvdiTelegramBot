import asyncio
from types import SimpleNamespace

import pytest

from tgbot.db import init as db_init


def test_init_db_retries_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"apply_calls": 0, "sleep_calls": 0}

    async def fake_apply_schema(_db) -> None:
        state["apply_calls"] += 1
        if state["apply_calls"] == 1:
            raise OSError("connection refused")

    async def fake_create_pool(_db):
        return "POOL"

    async def fake_sleep(_delay: float) -> None:
        state["sleep_calls"] += 1

    monkeypatch.setattr(db_init, "apply_schema", fake_apply_schema)
    monkeypatch.setattr(db_init, "create_db_pool", fake_create_pool)
    monkeypatch.setattr(db_init.asyncio, "sleep", fake_sleep)
    monkeypatch.setenv("DB_WAIT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("DB_WAIT_DELAY_SECONDS", "0.01")

    result = asyncio.run(db_init.init_db(SimpleNamespace(db=object())))

    assert result == "POOL"
    assert state["apply_calls"] == 2
    assert state["sleep_calls"] == 1


def test_init_db_fails_immediately_on_non_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_apply_schema(_db) -> None:
        raise ValueError("schema failed")

    async def fake_create_pool(_db):
        return "POOL"

    monkeypatch.setattr(db_init, "apply_schema", fake_apply_schema)
    monkeypatch.setattr(db_init, "create_db_pool", fake_create_pool)
    monkeypatch.setenv("DB_WAIT_MAX_ATTEMPTS", "5")

    with pytest.raises(ValueError, match="schema failed"):
        asyncio.run(db_init.init_db(SimpleNamespace(db=object())))
