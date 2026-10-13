"""Bounded, sanitized descriptions for the owner's read-only delivery view."""

from typing import Any

DELIVERY_STATES = ("queued", "ready", "waiting", "blocked", "uncertain", "sending", "review")

_REASONS = {
    "no_images": "Waiting for listing images before making a plan.",
    "rate_limited": "Telegram requested a delay for this recipient.",
    "recipient_changed": "The configured bot or chat differs from this saved plan.",
    "interrupted_dispatch": "The process stopped before a receipt was saved.",
    "legacy_untracked": "Historical delivery has no reliable receipt record.",
    "TelegramBadRequest": "Telegram rejected the request.",
    "TelegramForbiddenError": "Telegram denied access to the chat.",
    "TelegramNotFound": "Telegram could not find the requested resource.",
    "TelegramUnauthorizedError": "Telegram rejected the bot credentials.",
    "TelegramNetworkError": "The network response did not establish delivery.",
    "TelegramServerError": "Telegram's server response did not establish delivery.",
    "TimeoutError": "The request timed out before a reliable receipt was saved.",
    "CancelledError": "The request was interrupted before a reliable receipt was saved.",
    "FileNotFoundError": "A planned media file is missing.",
    "DeliveryStateError": "The saved plan, media or receipt could not be verified.",
    "ValidationError": "The saved delivery data could not be validated.",
}

_ACTIONS = {
    "queued": "The next pipeline drain can prepare this alert once its inputs are available.",
    "ready": (
        "The next pipeline drain can send the next part when its discovery and recipient are ready."
    ),
    "waiting": "Wait for the saved delay; restarting does not shorten it.",
    "blocked": (
        "Correct the rejection or restore the original planned media, then reconcile locally."
    ),
    "uncertain": (
        "Check the configured Telegram chat for this part before choosing a recovery action."
    ),
    "sending": (
        "A request may be in flight. Do not retry it; let the runner finish or recover on restart."
    ),
    "review": "Check historical delivery in the configured chat before choosing a recovery action.",
}


def delivery_view(report: dict[str, Any]) -> dict[str, Any]:
    """Do not render raw exception text, payloads, recipient IDs or evidence references."""
    records = []
    for row in report.get("unfinished", []):
        record = {
            name: row[name]
            for name in (
                "event_key",
                "property_id",
                "kind",
                "state",
                "next_part",
                "total_parts",
                "message_ids",
                "retry_at",
                "created_at",
                "updated_at",
            )
        }
        record["reason_label"] = (
            _REASONS.get(row["reason"], "Recorded failure; inspect the local delivery report.")
            if row["reason"]
            else "No failure recorded."
        )
        record["next_action"] = _ACTIONS[row["state"]]
        records.append(record)
    return {
        "counts": report.get("counts", {}),
        "records": records,
        "limit": report.get("limit", 100),
    }
