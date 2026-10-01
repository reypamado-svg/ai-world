"""Tests that call a real model provider run only when asked for with `-m live`."""

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if "live" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="calls a real model provider; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
