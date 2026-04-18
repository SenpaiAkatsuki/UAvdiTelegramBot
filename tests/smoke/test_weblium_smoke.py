import os

import pytest

from infrastructure.api.weblium_app import (
    PRIMARY_LIQPAY_CALLBACK_PATH,
    PRIMARY_LIQPAY_PAY_PATH,
    PRIMARY_WEBLIUM_PATH,
    create_app,
)
from tests.helpers import make_test_config


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_WEBLIUM_SMOKE_TESTS", "0") != "1",
    reason="Smoke tests are disabled. Set RUN_WEBLIUM_SMOKE_TESTS=1 to run them.",
)


def _collect_route_paths(app) -> set[str]:
    paths: set[str] = set()
    for route in app.router.routes():
        info = route.get_info()
        if "path" in info:
            paths.add(info["path"])
        elif "formatter" in info:
            paths.add(info["formatter"])
    return paths


def test_create_app_registers_main_webhook_routes() -> None:
    app = create_app(make_test_config())
    route_paths = _collect_route_paths(app)

    assert PRIMARY_WEBLIUM_PATH in route_paths
    assert PRIMARY_LIQPAY_CALLBACK_PATH in route_paths
    assert PRIMARY_LIQPAY_PAY_PATH in route_paths
