"""Opt-in pytest timing artifacts, including xdist workers and fixture setup.

Use --test-profile=path.json. Durations are observed wall seconds, not CPU cost;
parallel phase totals overlap and must not be described as elapsed run time.
"""

from __future__ import annotations

import json
import os
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--test-profile",
        "--timing-report",
        dest="test_profile",
        metavar="PATH",
        help="Write test phase, fixture setup, worker and host timing data as JSON",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("test_profile"):
        config.pluginmanager.register(TestProfile(config), "home-finder-test-profile")


def _load_average() -> list[float] | None:
    return list(os.getloadavg()) if hasattr(os, "getloadavg") else None


def _peak_rss_mib() -> float | None:
    if os.name != "posix":
        return None
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024 if platform.system() == "Darwin" else 1024)


class TestProfile:
    """One recorder per pytest process; only the controller writes the artifact."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.started = time.perf_counter()
        self.started_epoch = time.time()
        self.cpu_started = time.process_time()
        self.started_at = datetime.now(UTC).isoformat()
        self.load_start = _load_average()
        self.phases: list[dict[str, Any]] = []
        self.fixtures: dict[str, dict[str, Any]] = {}
        self.workers: dict[str, dict[str, Any]] = {}
        self.collection_seconds = 0.0
        self.collected = 0
        self.deselected = 0
        self.payload: dict[str, Any] | None = None

    @pytest.hookimpl(wrapper=True)
    def pytest_collection(self):
        start = time.perf_counter()
        try:
            return (yield)
        finally:
            self.collection_seconds = time.perf_counter() - start

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = len(session.items)

    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        self.deselected += len(items)

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        start = time.perf_counter()
        try:
            return (yield)
        finally:
            seconds = time.perf_counter() - start
            # baseid distinguishes same-named fixtures in different conftests.
            key = f"{fixturedef.baseid or '<root>'}::{fixturedef.argname}"
            row = self.fixtures.setdefault(
                key,
                {"scope": fixturedef.scope, "count": 0, "seconds": 0.0, "max_seconds": 0.0},
            )
            row["count"] += 1
            row["seconds"] += seconds
            row["max_seconds"] = max(row["max_seconds"], seconds)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # xdist forwards every report to the controller, including worker crashes.
        # Keeping those reports here preserves partial data if a worker dies.
        if hasattr(self.config, "workerinput"):
            return
        self.phases.append(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "outcome": report.outcome,
                "seconds": report.duration,
                "worker": getattr(report, "worker_id", "main"),
                "start": report.start,
                "stop": report.stop,
            }
        )

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:
        self.workers[node.gateway.id] = {
            # A process that exits abruptly never sends workeroutput.
            **getattr(node, "workeroutput", {}).get("test_profile", {}),
            "crashed": error is not None,
        }

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        process = {
            "wall_seconds": time.perf_counter() - self.started,
            "cpu_seconds": time.process_time() - self.cpu_started,
            "peak_rss_mib": _peak_rss_mib(),
            "collection_seconds": self.collection_seconds,
            "selected": self.collected,
            "deselected": self.deselected,
            "fixtures": self.fixtures,
        }
        if hasattr(self.config, "workeroutput"):
            self.config.workeroutput["test_profile"] = process
            return

        from hypothesis import settings

        payload = {
            "schema_version": 1,
            "started_at": self.started_at,
            "exit_code": int(exitstatus),
            "wall_seconds": process["wall_seconds"],
            "environment": {
                "python": platform.python_version(),
                "platform": platform.system(),
                "pytest": pytest.__version__,
                "cpu_count": os.cpu_count(),
                "affinity_count": (
                    len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
                ),
                "load_average_start": self.load_start,
                "load_average_end": _load_average(),
                "workers_requested": self.config.getoption("numprocesses", default=0),
                "worker_cap": self.config.getoption("maxprocesses", default=None),
                "distribution": self.config.getoption("dist", default="no"),
                "marker_expression": self.config.option.markexpr,
                "coverage": bool(self.config.getoption("cov_source", default=[]))
                and not self.config.getoption("no_cov", default=False),
                "hypothesis_profile": settings.get_current_profile_name(),
                "schema_template": self.config.pluginmanager.has_plugin(
                    "tests.plugins.schema_template"
                ),
            },
            "controller": process,
            "workers": self.workers,
            "phase_outcomes": dict(Counter(f"{p['phase']}:{p['outcome']}" for p in self.phases)),
            "phases": self.phases,
        }
        completed = [phase for phase in self.phases if phase["start"] > 0]
        if completed:
            first = min(phase["start"] for phase in completed)
            last = max(phase["stop"] for phase in completed)
            window = max(0.0, last - first)
            busy: dict[str, float] = {}
            for phase in completed:
                worker = phase["worker"]
                busy[worker] = busy.get(worker, 0.0) + phase["seconds"]
            # Include allocated workers that received no tests. Restarted workers
            # reuse a slot, so counting gateway IDs would understate occupancy.
            slots = max(1, self.config.getoption("numprocesses", default=0) or 0)
            for worker in self.workers:
                busy.setdefault(worker, 0.0)
            payload["test_window"] = {
                "startup_seconds": max(0.0, first - self.started_epoch),
                "seconds": window,
                "finish_seconds": max(0.0, time.time() - last),
                "worker_phase_seconds": busy,
                "worker_slots": slots,
                "occupied_fraction": sum(busy.values()) / (slots * window) if window else 0.0,
            }
        self.payload = payload
        destination = Path(self.config.getoption("test_profile"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2) + "\n")

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if self.payload and (window := self.payload.get("test_window")):
            terminalreporter.write_line(
                f"Test timing: startup {window['startup_seconds']:.2f}s; "
                f"test window {window['seconds']:.2f}s; "
                f"finish (worker shutdown/session hooks) {window['finish_seconds']:.2f}s; "
                f"workers inside test phases for {window['occupied_fraction']:.0%} of the window"
            )
        terminalreporter.write_line(f"Test profile: {self.config.getoption('test_profile')}")
