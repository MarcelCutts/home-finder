"""The supported OnTheMarket Next.js envelopes, shared by search and detail readers."""

from typing import Any


def onthemarket_redux_state(payload: object) -> dict[str, Any]:
    """Select current or legacy state without hiding a malformed current envelope."""
    if not isinstance(payload, dict):
        raise ValueError("invalid_next_data")
    props = payload.get("props")
    if not isinstance(props, dict):
        raise ValueError("invalid_props")
    # Presence, not truthiness, selects the current envelope. Its empty/malformed
    # contents must never fall back to stale legacy data or suggested properties.
    envelope = props.get("pageProps", props)
    if not isinstance(envelope, dict):
        raise ValueError("invalid_page_props")
    state = envelope.get("initialReduxState")
    if not isinstance(state, dict):
        raise ValueError("invalid_redux_state")
    return state
