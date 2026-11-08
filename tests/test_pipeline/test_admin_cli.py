"""Administrative argument contracts; durable reconciliation is tested separately."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from home_finder.pipeline import delivery_admin, notification_report, recovery_admin


@pytest.mark.parametrize(
    "flags,expected",
    [
        ([], None),
        (["--retry-confirmed-undelivered"], ("retry", None)),
        (["--suppress"], ("suppress", None)),
        (["--message-ids", "17,18"], ("acknowledge", [17, 18])),
    ],
    ids=["report", "retry", "suppress", "acknowledge"],
)
def test_delivery_cli_selects_exact_action_and_prints_result(monkeypatch, capsys, flags, expected):
    report = {"available": True, "counts": {"review": 1}}
    read = AsyncMock(return_value=report)
    administer = AsyncMock(return_value=report)
    monkeypatch.setattr(delivery_admin, "read_report", read)
    monkeypatch.setattr(delivery_admin, "administer", administer)
    args = ["delivery-admin", "--database", "fixture.db", *flags]
    if expected:
        args += ["--delivery", "event-17", "--evidence-ref", "operator-note"]
    monkeypatch.setattr(sys, "argv", args)

    delivery_admin.main()

    assert json.loads(capsys.readouterr().out) == report
    if expected:
        action, ids = expected
        administer.assert_awaited_once_with(
            Path("fixture.db"), "event-17", action, "operator-note", ids
        )
        read.assert_not_awaited()
    else:
        read.assert_awaited_once_with(Path("fixture.db"))
        administer.assert_not_awaited()


@pytest.mark.parametrize(
    "flags",
    [
        ["--suppress"],
        ["--delivery", "event-17"],
        ["--delivery", "event-17", "--suppress"],
        ["--evidence-ref", "operator-note"],
    ],
    ids=["action-only", "delivery-only", "missing-evidence", "evidence-only"],
)
def test_incomplete_delivery_action_cannot_open_or_modify_storage(monkeypatch, flags):
    read = AsyncMock()
    administer = AsyncMock()
    monkeypatch.setattr(delivery_admin, "read_report", read)
    monkeypatch.setattr(delivery_admin, "administer", administer)
    monkeypatch.setattr(sys, "argv", ["delivery-admin", "--database", "missing.db", *flags])
    with pytest.raises(SystemExit) as error:
        delivery_admin.main()
    assert error.value.code == 2
    read.assert_not_called()
    administer.assert_not_called()


def test_invalid_message_ids_fail_without_dispatching_or_echoing_input(monkeypatch, capsys):
    administer = AsyncMock()
    monkeypatch.setattr(delivery_admin, "administer", administer)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "delivery-admin",
            "--database",
            "fixture.db",
            "--delivery",
            "event-17",
            "--message-ids",
            "private-invalid-input",
            "--evidence-ref",
            "operator-note",
        ],
    )
    with pytest.raises(SystemExit) as error:
        delivery_admin.main()
    assert error.value.code == 1
    administer.assert_not_called()
    message = capsys.readouterr().err
    assert "ValueError" in message
    assert "private-invalid-input" not in message


def test_recovery_cli_forwards_all_selected_homes_and_reason(monkeypatch, capsys):
    request = AsyncMock(return_value={"openrent:1": "queued", "zoopla:2": "ineligible"})
    monkeypatch.setattr(recovery_admin, "request_recovery", request)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recovery-admin",
            "--database",
            "fixture.db",
            "--property-id",
            "openrent:1",
            "--property-id",
            "zoopla:2",
            "--reason",
            "new evidence",
        ],
    )
    recovery_admin.main()
    request.assert_awaited_once_with(
        Path("fixture.db"), ["openrent:1", "zoopla:2"], reason="new evidence"
    )
    assert json.loads(capsys.readouterr().out) == request.return_value


def test_selection_report_cli_validates_policy_before_reading_storage(monkeypatch, capsys):
    read = Mock(return_value={"creates_deliveries": False})
    monkeypatch.setattr(notification_report, "read_report", read)
    monkeypatch.setattr(
        sys,
        "argv",
        ["notification-report", "--database", "fixture.db", "--minimum-fit-score", "73"],
    )
    notification_report.main()
    assert read.call_args.args[0] == Path("fixture.db")
    assert read.call_args.args[1].min_fit_score == 73
    assert json.loads(capsys.readouterr().out) == {"creates_deliveries": False}
    read.reset_mock()
    monkeypatch.setattr(
        sys,
        "argv",
        ["notification-report", "--database", "fixture.db", "--minimum-fit-score", "101"],
    )
    with pytest.raises(SystemExit) as error:
        notification_report.main()
    assert error.value.code == 1
    read.assert_not_called()
