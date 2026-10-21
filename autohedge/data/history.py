"""Immutable normalized OHLCV pages, content-addressed storage and explicit replay."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    Field,
    HttpUrl,
    StrictInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from autohedge.contracts import Contract
from autohedge.data.contracts import CandleRequest, CandleSeries
from autohedge.data.quality import validate_candle_response
from autohedge.errors import DataQualityError

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def epoch_seconds(value: datetime) -> int:
    delta = value - EPOCH
    return delta.days * 86400 + delta.seconds


def window_end(request: CandleRequest) -> datetime:
    seconds = epoch_seconds(request.as_of)
    return EPOCH + timedelta(
        seconds=seconds // request.interval_seconds * request.interval_seconds
    )


class CandlePage(Contract):
    """Normalized page and its request cursor; no keys or raw response bodies."""

    endpoint: HttpUrl
    before_timestamp: Annotated[StrictInt, Field(ge=0)]
    requested_limit: Annotated[StrictInt, Field(ge=1, le=1000)]
    series: CandleSeries

    @field_validator("endpoint")
    @classmethod
    def safe_endpoint(cls, value):
        if (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
        ):
            raise ValueError(
                "History endpoint must be HTTPS without credentials or query"
            )
        return value

    @model_validator(mode="after")
    def aligned_and_bounded(self):
        if len(self.series.candles) > self.requested_limit:
            raise ValueError("Provider returned more rows than requested")
        for candle in self.series.candles:
            timestamp = epoch_seconds(candle.open_time)
            if candle.open_time.microsecond or timestamp % self.series.interval_seconds:
                raise ValueError("Unaligned OHLCV interval")
            if timestamp < 0 or timestamp > self.before_timestamp:
                raise ValueError("Page exceeded its requested cursor")
        return self


class HistorySnapshot(Contract):
    schema_version: Literal[1] = 1
    request: CandleRequest
    series: CandleSeries
    pages: Annotated[tuple[CandlePage, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def reproducible_window(self):
        try:
            validate_candle_response(self.series, self.request)
        except DataQualityError:
            raise ValueError("History does not cover its requested window") from None
        end = window_end(self.request)
        start = end - timedelta(
            seconds=self.request.count * self.request.interval_seconds
        )
        if start < EPOCH or self.series.candles[-1].close_time != end:
            raise ValueError("History does not end at the latest completed interval")
        collected = {}
        cursor = epoch_seconds(end) - 1
        previous_receipt = self.request.as_of
        for page in self.pages:
            if page.before_timestamp != cursor:
                raise ValueError("History page cursor does not match preceding page")
            series = page.series
            if (
                series.instrument != self.series.instrument
                or series.interval_seconds != self.series.interval_seconds
                or series.source != self.series.source
                or page.endpoint != self.pages[0].endpoint
                or series.received_at < previous_receipt
            ):
                raise ValueError("History page identity or receipt time mismatch")
            previous_receipt = series.received_at
            next_cursor = epoch_seconds(series.candles[0].open_time)
            if next_cursor >= cursor:
                raise ValueError("History pagination made no progress")
            for candle in series.candles:
                if candle.close_time > self.request.as_of:
                    raise ValueError("Page contains future or incomplete candles")
                existing = collected.get(candle.open_time)
                if existing is not None and existing != candle:
                    raise ValueError("Conflicting overlapping candles")
                collected[candle.open_time] = candle
            cursor = next_cursor
        selected = tuple(
            collected[key] for key in sorted(collected) if start <= key < end
        )
        if (
            selected != self.series.candles
            or len(selected) != self.request.count
            or self.series.received_at != previous_receipt
        ):
            raise ValueError("History cannot be reproduced from its pages")
        return self


class HistoryStore:
    """Files are addressed by SHA-256 and created atomically without overwriting.

    Hashes detect corruption, not authenticity against someone who can replace
    the entire store. Use trusted paths and filesystem access control.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def save(self, snapshot: HistorySnapshot) -> str:
        try:
            snapshot = HistorySnapshot.model_validate(snapshot)
        except ValidationError:
            raise DataQualityError("invalid_history") from None
        payload = json.dumps(
            snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(payload) > _MAX_ARTIFACT_BYTES:
            raise DataQualityError("history_too_large")
        digest = hashlib.sha256(payload).hexdigest()
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as file:
                temporary = Path(file.name)
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            try:
                os.link(temporary, self.directory / f"{digest}.json")
            except FileExistsError:
                if self._read(digest) != payload:
                    raise DataQualityError("history_integrity")
        except OSError:
            raise DataQualityError("history_io") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    raise DataQualityError("history_io") from None
        return digest

    def _read(self, digest: str) -> bytes:
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise DataQualityError("invalid_history_id")
        try:
            with (self.directory / f"{digest}.json").open("rb") as file:
                payload = file.read(_MAX_ARTIFACT_BYTES + 1)
        except FileNotFoundError:
            raise DataQualityError("history_not_found") from None
        except OSError:
            raise DataQualityError("history_io") from None
        if (
            len(payload) > _MAX_ARTIFACT_BYTES
            or hashlib.sha256(payload).hexdigest() != digest
        ):
            raise DataQualityError("history_integrity")
        return payload

    def load(self, digest: str) -> HistorySnapshot:
        payload = self._read(digest)
        try:
            return HistorySnapshot.model_validate_json(payload)
        except ValidationError:
            raise DataQualityError("invalid_history") from None

    def replay(self, digest: str, *, available_at: datetime) -> CandleSeries:
        try:
            available_at = TypeAdapter(AwareDatetime).validate_python(available_at)
        except ValidationError:
            raise DataQualityError("invalid_clock") from None
        snapshot = self.load(digest)
        if snapshot.series.received_at > available_at:
            raise DataQualityError("history_not_yet_available")
        return snapshot.series
