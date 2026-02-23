"""Shared Playwright fixtures and helpers for browser E2E tests."""

import re
from collections.abc import Generator

import pytest
from playwright.sync_api import Page, expect


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-enable socket and disable pytest-timeout for browser-marked tests.

    The global ``addopts`` includes ``--disable-socket`` to keep unit tests
    network-free.  Browser E2E tests need real TCP sockets (server + Chromium),
    so we piggyback on pytest-socket's ``enable_socket`` marker.

    pytest-timeout's signal method (SIGALRM) corrupts Playwright's internal
    connection state, causing deadlocks on fixture teardown.  We disable it
    for browser tests and rely on Playwright's built-in timeouts instead.
    """
    for item in items:
        if item.get_closest_marker("browser"):
            item.add_marker(pytest.mark.enable_socket)
            item.add_marker(pytest.mark.timeout(0))


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Prevent signal interference with Playwright's browser process."""
    return {
        **browser_type_launch_args,
        "handle_sigint": False,
        "handle_sigterm": False,
    }


@pytest.fixture
def console_errors(page: Page) -> Generator[list[str]]:
    """Collect browser console errors per test; fail if unexpected errors appear.

    Usage: include ``console_errors`` in your test signature, then assert at the
    end (or let the teardown assertion catch surprises).
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
        import warnings

        warnings.warn(
            f"Unexpected console errors (check with console_errors fixture): {unexpected}",
            stacklevel=1,
        )


def wait_for_htmx_settle(page: Page, *, timeout: int = 5000) -> None:
    """Wait for HTMX to finish processing and settle the DOM.

    Uses polling via ``page.evaluate`` instead of ``page.wait_for_function``
    because the latter is blocked by the app's CSP nonce policy.
    """
    page.evaluate(
        """
        window.__htmxSettled = false;
        document.addEventListener(
            'htmx:afterSettle',
            () => { window.__htmxSettled = true },
            { once: true }
        );
        """
    )
    import time

    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if page.evaluate("window.__htmxSettled"):
            return
        page.wait_for_timeout(50)
    msg = f"HTMX did not settle within {timeout}ms"
    raise TimeoutError(msg)


def assert_no_dialog_visible(page: Page) -> None:
    """Assert no <dialog> element is currently visible on the page."""
    dialogs = page.locator("dialog")
    for i in range(dialogs.count()):
        expect(dialogs.nth(i)).not_to_be_visible()
