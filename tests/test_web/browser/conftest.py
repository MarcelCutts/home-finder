"""Playwright fixtures for the browser tests (``-m browser``).

Their collection rules (sockets, timeout, xdist group) live in tests/conftest.py so they
also apply to browser-marked tests elsewhere, such as integration/test_sort_verification.py.
"""

import re
import warnings
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Prevent signal interference with Playwright's browser process."""
    return {
        **browser_type_launch_args,
        "handle_sigint": False,
        "handle_sigterm": False,
    }


@pytest.fixture
def console_errors(page: "Page") -> Generator[list[str]]:
    """Collect browser console errors per test; warn if unexpected errors appear.

    Usage: include ``console_errors`` in your test signature, then assert at the
    end (or let the teardown warning catch surprises).
    """
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    yield errors
    # Post-test safety net: filter out benign noise, warn if real errors remain.
    # Tests that care should assert on ``console_errors`` explicitly; this
    # teardown catches surprises in tests that don't check.
    benign = re.compile(r"favicon\.ico|Failed to load resource.*favicon")
    unexpected = [e for e in errors if not benign.search(e)]
    if unexpected:
        warnings.warn(
            f"Unexpected console errors (check with console_errors fixture): {unexpected}",
            stacklevel=1,
        )
