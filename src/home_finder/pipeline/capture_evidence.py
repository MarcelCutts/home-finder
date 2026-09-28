"""Verify private capture references and explicitly expire managed raw bodies.

Inspection never writes. Expiry requires the exact reviewed manifest checksum,
an elapsed declared deadline, and a terminal capture. It never touches a DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from home_finder.models.observations import Digest, RawCaptureReference
from home_finder.pipeline.capture import CapturePlan
from home_finder.pipeline.runner import runner_lock


class RawCaptureUnavailable(ValueError):
    def __init__(self, reason: Literal["expired", "missing", "incomplete"]) -> None:
        self.reason = reason
        super().__init__(f"Raw capture is {reason}")


class CaptureRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    index: int = Field(ge=0, le=11, strict=True)
    url: HttpUrl
    kind: Literal["search", "detail", "reference"]
    state: Literal["started", "received", "truncated", "cancelled", "error"]
    observed_at: AwareDatetime | None = None
    status: int | None = Field(default=None, ge=100, le=599, strict=True)
    response_headers: dict[str, str] = Field(default_factory=dict, max_length=3)
    body_file: str | None = None
    body_bytes: int | None = Field(default=None, ge=0, le=8_000_000, strict=True)
    body_sha256: Digest | None = None

    @model_validator(mode="after")
    def consistent_body(self) -> CaptureRecord:
        body = (self.body_file, self.body_bytes, self.body_sha256)
        if any(v is not None for v in body) and (
            any(v is None for v in body) or self.body_file != f"{self.index:02d}.body"
        ):
            raise ValueError("Unexpected response filename or incomplete body metadata")
        if self.state == "received" and (self.body_file is None or self.status is None):
            raise ValueError("Received response lacks body/status metadata")
        if any(
            k not in {"content-type", "retry-after", "cf-mitigated"} or len(v) > 4096
            for k, v in self.response_headers.items()
        ):
            raise ValueError("Unsupported retained response headers")
        return self


class CaptureManifest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    schema_version: Literal[1]
    mode: Literal["live", "fixture"]
    state: Literal["running", "completed", "stopped", "cancelled", "failed"]
    started_at: AwareDatetime
    plan: CapturePlan
    attempts: tuple[CaptureRecord, ...] = Field(max_length=12)

    @model_validator(mode="after")
    def matches_plan(self) -> CaptureManifest:
        if len(self.attempts) > len(self.plan.requests):
            raise ValueError("Capture has more attempts than its plan")
        for index, record in enumerate(self.attempts):
            request = self.plan.requests[index]
            if record.index != index or record.url != request.url or record.kind != request.kind:
                raise ValueError("Capture attempt does not match its plan")
            if record.body_bytes is not None and record.body_bytes > self.plan.max_response_bytes:
                raise ValueError("Response exceeds the declared capture bound")
            if (
                self.mode == "fixture"
                and (record.state == "received" or record.observed_at is not None)
                and record.observed_at != request.fixture_observed_at
            ):
                raise ValueError("Fixture observation time differs from its supplied provenance")
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate capture manifest key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Nonfinite value in capture manifest")


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Anchor each path component without following symlinks (Linux/POSIX)."""
    parts = path.absolute().parts
    if ".." in parts:
        raise ValueError("Capture paths cannot contain parent traversal")
    fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read_file(directory_fd: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise ValueError("Capture input must be a bounded regular file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Capture input exceeds its byte bound")
    return raw


@dataclass(frozen=True)
class VerifiedCapture:
    """Metadata is verified here; each body is verified only when read."""

    directory_fd: int
    manifest: CaptureManifest
    sha256: str

    def expired(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("Retention checks require an aware clock")
        retention = self.manifest.plan.raw_retention
        return retention is not None and retention.retain_until <= now

    def body(self, index: int, *, now: datetime, for_expiry: bool = False) -> bytes:
        if not for_expiry and self.expired(now):
            raise RawCaptureUnavailable("expired")
        if not 0 <= index < len(self.manifest.attempts):
            raise ValueError("Capture record index is outside the manifest")
        record = self.manifest.attempts[index]
        if record.body_file is None:
            raise RawCaptureUnavailable("incomplete")
        try:
            raw = _read_file(
                self.directory_fd, record.body_file, self.manifest.plan.max_response_bytes
            )
        except FileNotFoundError:
            raise RawCaptureUnavailable("missing") from None
        if len(raw) != record.body_bytes or hashlib.sha256(raw).hexdigest() != record.body_sha256:
            raise ValueError("Response bytes do not match the capture hash/size")
        return raw

    def reference(self, index: int, body: bytes) -> RawCaptureReference:
        record = self.manifest.attempts[index]
        if len(body) != record.body_bytes or hashlib.sha256(body).hexdigest() != record.body_sha256:
            raise ValueError("Cannot reference unverified response bytes")
        assert record.body_sha256 is not None
        return RawCaptureReference(
            capture_manifest_sha256=self.sha256,
            record_index=index,
            response_sha256=record.body_sha256,
            response_bytes=len(body),
            capture_mode=self.manifest.mode,
        )


@contextmanager
def verified_capture(path: Path) -> Iterator[VerifiedCapture]:
    with _directory(path) as fd:
        raw = _read_file(fd, "manifest.json", 256_000)
        try:
            value = json.loads(
                raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant
            )
            manifest = CaptureManifest.model_validate(value)
        except (ValueError, RecursionError):
            # Validation errors can echo input bodies or credentials. Keep the
            # public error bounded and free of manifest values.
            raise ValueError("Invalid capture manifest (shape, duplicate keys or bounds)") from None
        if manifest.state == "running":
            raise ValueError("A running capture is not an immutable replay input")
        yield VerifiedCapture(fd, manifest, hashlib.sha256(raw).hexdigest())


def resolve_reference(path: Path, reference: RawCaptureReference, *, now: datetime) -> bytes:
    """Resolve an exact reference now; a hash in the DB alone promises no bytes."""
    with verified_capture(path) as capture:
        if capture.sha256 != reference.capture_manifest_sha256:
            raise ValueError("Capture manifest checksum differs from the observation reference")
        body = capture.body(reference.record_index, now=now)
        actual = capture.reference(reference.record_index, body)
        if (
            actual.response_sha256 != reference.response_sha256
            or actual.response_bytes != reference.response_bytes
            or (
                reference.capture_mode is not None and actual.capture_mode != reference.capture_mode
            )
        ):
            raise ValueError("Response metadata differs from the observation reference")
        return body


def inspect_capture(path: Path, *, now: datetime) -> dict[str, Any]:
    """Return metadata and verified availability, never source content or URLs."""
    with verified_capture(path) as capture:
        expired = capture.expired(now)
        records = []
        for record in capture.manifest.attempts:
            try:
                capture.body(record.index, now=now, for_expiry=True)
                state = "expired_retained" if expired else "available"
            except RawCaptureUnavailable as exc:
                state = exc.reason
            records.append({"index": record.index, "raw_state": state})
        retention = capture.manifest.plan.raw_retention
        return {
            "manifest_sha256": capture.sha256,
            "capture_mode": capture.manifest.mode,
            "retention": "managed" if retention else "legacy_unmanaged",
            "retain_until": retention.retain_until.isoformat() if retention else None,
            "records": records,
        }


def _expiry_receipt(directory_fd: int, value: dict[str, Any]) -> None:
    name = f".raw-expiry-{uuid4().hex}.tmp"
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, "raw-expiry.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(name, dir_fd=directory_fd)


def expire_capture(path: Path, *, expected_sha256: str, now: datetime) -> dict[str, Any]:
    """Explicit deletion of at most 12 verified expired body files; rerunnable."""
    with _directory(path):
        pass  # Reject symlink/traversal input before creating the namespace lock.
    # Same local acquisition namespace lock. No second active writer or running
    # manifest can be purged. A local administrator must still protect this root.
    with runner_lock(str(path.absolute().parent / "capture-authority")) as acquired:
        if not acquired:
            raise ValueError("Capture namespace is in use")
        with verified_capture(path) as capture:
            if capture.sha256 != expected_sha256:
                raise ValueError("Expiry requires the exact reviewed manifest checksum")
            if not capture.expired(now):
                raise ValueError("Only explicitly managed captures past their deadline may expire")
            names = []
            absent = []
            # Verify all remaining bodies before removing any. Corruption is an
            # audit failure, not permission to delete unrelated or changed bytes.
            for record in capture.manifest.attempts:
                if record.body_file is None:
                    continue
                try:
                    capture.body(record.index, now=now, for_expiry=True)
                except RawCaptureUnavailable as exc:
                    if exc.reason != "missing":
                        raise
                    absent.append(record.index)
                else:
                    names.append((record.index, record.body_file))
            report = {
                "schema_version": 1,
                "manifest_sha256": capture.sha256,
                "expired_at": now.isoformat(),
                "state": "removing",
                "removal_requested": [index for index, _ in names],
                "already_absent": absent,
            }
            _expiry_receipt(capture.directory_fd, report)
            for _index, name in names:
                os.unlink(name, dir_fd=capture.directory_fd)
            os.fsync(capture.directory_fd)
            report["state"] = "completed"
            _expiry_receipt(capture.directory_fd, report)
            return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--expire", action="store_true", help="Remove only expired managed bodies")
    parser.add_argument("--expected-manifest-sha256")
    args = parser.parse_args()
    if args.expire != bool(args.expected_manifest_sha256):
        parser.error("Expiry requires --expire and --expected-manifest-sha256 together")
    try:
        now = datetime.now(UTC)
        result = (
            expire_capture(args.capture, expected_sha256=args.expected_manifest_sha256, now=now)
            if args.expire
            else inspect_capture(args.capture, now=now)
        )
    except (ValueError, OSError):
        parser.exit(1, "Capture could not be verified or is not eligible for expiry.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
