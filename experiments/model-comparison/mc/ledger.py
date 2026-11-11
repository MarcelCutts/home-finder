"""Shared estimated-cost ledger; reservations are not provider invoice limits."""

from __future__ import annotations

import fcntl
import json
import math
import os
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "ledger.json"


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    """Atomically share settled costs and outstanding reservations across processes."""

    def __init__(
        self, caps: dict[str, float], reserve: dict[str, float], path: Path = LEDGER_PATH
    ) -> None:
        if caps.keys() != reserve.keys() or any(
            not math.isfinite(value) or value <= 0 for value in [*caps.values(), *reserve.values()]
        ):
            raise ValueError("caps and reserves must be finite positive amounts for each provider")
        self.caps = dict(caps)
        self.reserve = dict(reserve)
        self.path = path
        self._lock = threading.Lock()

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state: dict[str, Any] = {
                "spent": dict.fromkeys(self.caps, 0.0),
                "caps": self.caps,
                "reservations": {},
            }
            if self.path.exists():
                state.update(json.loads(self.path.read_text()))
            if state["caps"] != self.caps:
                raise ValueError(
                    "existing ledger caps differ; reconcile explicitly before resuming"
                )
            yield state
            # Keep the lock inode; replace the data file atomically.
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as tmp:
                temporary = Path(tmp.name)
                try:
                    json.dump(state, tmp, indent=2, allow_nan=False)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    temporary.replace(self.path)
                finally:
                    temporary.unlink(missing_ok=True)

    @property
    def spent(self) -> dict[str, float]:
        with self._locked() as state:
            return dict(state["spent"])

    def check(self, provider: str) -> str:
        with self._locked() as state:
            pending = [r for r in state["reservations"].values() if r["provider"] == provider]
            if any(r["state"] == "unknown" for r in pending):
                raise BudgetExceeded(f"{provider}: unresolved dispatch; reconcile before resuming")
            projected = state["spent"][provider] + sum(r["amount"] for r in pending)
            if projected + self.reserve[provider] > self.caps[provider]:
                raise BudgetExceeded(f"{provider}: spending plus reservations reaches the cap")
            token = uuid.uuid4().hex
            state["reservations"][token] = {
                "provider": provider,
                "amount": self.reserve[provider],
                "state": "reserved",
            }
            return token

    @staticmethod
    def _reservation(state: dict[str, Any], provider: str, token: str) -> dict[str, Any]:
        reservation: dict[str, Any] = state["reservations"][token]
        if reservation["provider"] != provider:
            raise ValueError("reservation provider mismatch")
        return reservation

    def add(self, provider: str, amount: float, token: str) -> None:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("settlement must be a finite nonnegative amount")
        with self._locked() as state:
            self._reservation(state, provider, token)
            state["spent"][provider] += amount
            del state["reservations"][token]

    def release(self, provider: str, token: str) -> None:
        """Release only a known rejection or a request that was never sent."""
        with self._locked() as state:
            self._reservation(state, provider, token)
            del state["reservations"][token]

    def hold(self, provider: str, token: str) -> None:
        with self._locked() as state:
            self._reservation(state, provider, token)["state"] = "unknown"
