"""Frozen research inputs, deterministic features, evidence identities and collection."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, TypeAdapter, ValidationError, model_validator

from autohedge.contracts import Contract, Instrument, Text
from autohedge.data._adapter import utc_now
from autohedge.data.contracts import (
    CandleRequest,
    MarketData,
    QualityAssessment,
    QualityPolicy,
)
from autohedge.data.history import HistorySnapshot
from autohedge.data.provenance import content_id
from autohedge.data.providers import HistoryProvider, NewsProvider, QuoteProvider
from autohedge.data.quality import parse_market_data, validate_market_data
from autohedge.errors import AutoHedgeError, DataQualityError, ProviderError
from autohedge.indicators import IndicatorConfig, IndicatorSeries, calculate_indicators

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _as_of(value) -> datetime:
    try:
        return (
            TypeAdapter(AwareDatetime).validate_python(value).astimezone(timezone.utc)
        )
    except ValidationError:
        raise DataQualityError("invalid_clock") from None


class EvidenceReference(Contract):
    """Content-bound observations, not semantic events or independent confirmations."""

    evidence_id: Digest
    kind: Literal["quote", "history", "indicators", "news"]
    source: Text
    source_event_id: Text | None = None
    event_time: AwareDatetime
    received_at: AwareDatetime
    known_at: AwareDatetime

    @model_validator(mode="after")
    def ordered_times(self):
        if not self.event_time <= self.received_at <= self.known_at:
            raise ValueError("Evidence times are inconsistent")
        return self


def _evidence(
    data: MarketData, indicators: IndicatorSeries
) -> tuple[EvidenceReference, ...]:
    def reference(
        kind,
        value,
        source,
        event_time,
        received_at,
        *,
        known_at=None,
        source_event_id=None,
    ):
        return EvidenceReference(
            evidence_id=content_id(
                {
                    "schema": "autohedge.evidence.v1",
                    "kind": kind,
                    "instrument": data.quote.instrument.model_dump(mode="json"),
                    "record": value.model_dump(mode="json"),
                }
            ),
            kind=kind,
            source=source,
            source_event_id=source_event_id,
            event_time=event_time,
            received_at=received_at,
            known_at=known_at if known_at is not None else received_at,
        )

    quote, history = data.quote, data.history
    return (
        reference("quote", quote, quote.source, quote.event_time, quote.received_at),
        reference(
            "history",
            history,
            history.source,
            history.candles[-1].close_time,
            history.received_at,
        ),
        reference(
            "indicators",
            indicators,
            indicators.implementation,
            indicators.latest.close_time,
            indicators.received_at,
            known_at=indicators.as_of,
        ),
        *(
            reference(
                "news",
                article,
                article.source,
                article.published_at,
                article.received_at,
                source_event_id=article.article_id,
            )
            for article in data.news
        ),
    )


class _SnapshotContent(Contract):
    schema_version: Literal[1] = 1
    instrument: Instrument
    as_of: AwareDatetime
    market_data: MarketData
    indicators: IndicatorSeries
    quality: QualityAssessment
    history_acquisition: HistorySnapshot | None = None
    history_acquisition_id: Digest | None = None
    evidence: Annotated[
        tuple[EvidenceReference, ...], Field(min_length=3, max_length=103)
    ]

    @model_validator(mode="after")
    def bound_and_reproducible(self):
        if self.quality.checked_at != self.as_of:
            raise ValueError("Snapshot quality time does not match its knowledge time")
        if self.quality.policy.min_candles < self.indicators.config.minimum_candles:
            raise ValueError("Snapshot policy does not cover indicator warm-up")
        try:
            assessment = validate_market_data(
                self.market_data,
                expected=self.instrument,
                now=self.as_of,
                policy=self.quality.policy,
            )
            calculated = calculate_indicators(
                self.market_data.history,
                as_of=self.as_of,
                config=self.indicators.config,
            )
        except DataQualityError:
            raise ValueError("Invalid snapshot data or knowledge time") from None
        if (
            self.quality != assessment
            or self.indicators != calculated
            or not calculated.latest.ready
        ):
            raise ValueError(
                "Snapshot quality or computed indicators do not match inputs"
            )
        if self.evidence != _evidence(self.market_data, calculated):
            raise ValueError("Snapshot evidence does not match its source records")
        if any(item.known_at > self.as_of for item in self.evidence):
            raise ValueError("Snapshot contains evidence not yet known")
        if self.history_acquisition is None:
            if self.history_acquisition_id is not None:
                raise ValueError("History acquisition ID requires its recorded pages")
        elif (
            self.history_acquisition.series != self.market_data.history
            or self.history_acquisition_id != content_id(self.history_acquisition)
        ):
            raise ValueError("History acquisition does not match the snapshot")
        return self


class ResearchSnapshot(_SnapshotContent):
    snapshot_id: Digest

    @model_validator(mode="after")
    def valid_content_identity(self):
        if self.snapshot_id != content_id(
            self.model_dump(mode="json", exclude={"snapshot_id"})
        ):
            raise ValueError("Snapshot content identity mismatch")
        return self


def _configuration(policy, config):
    try:
        config = IndicatorConfig.model_validate(
            config if config is not None else IndicatorConfig()
        )
        policy = QualityPolicy.model_validate(
            policy if policy is not None else QualityPolicy(require_news=True)
        )
        policy = QualityPolicy.model_validate(
            {
                **policy.model_dump(),
                "min_candles": max(policy.min_candles, config.minimum_candles),
            }
        )
        return policy, config
    except ValidationError:
        raise DataQualityError("invalid_snapshot_configuration") from None


def build_research_snapshot(
    data: MarketData,
    *,
    expected: Instrument,
    as_of: datetime,
    policy: QualityPolicy | None = None,
    indicator_config: IndicatorConfig | None = None,
    history_acquisition: HistorySnapshot | None = None,
) -> ResearchSnapshot:
    """Build from observations; dated news and ready indicators are required by default."""
    as_of = _as_of(as_of)
    policy, config = _configuration(policy, indicator_config)
    data = parse_market_data(data)
    if len(data.history.candles) < config.minimum_candles:
        raise DataQualityError("indicators_not_ready")
    quality = validate_market_data(data, expected=expected, now=as_of, policy=policy)
    indicators = calculate_indicators(data.history, as_of=as_of, config=config)
    try:
        acquisition = (
            HistorySnapshot.model_validate(history_acquisition)
            if history_acquisition is not None
            else None
        )
        payload = _SnapshotContent(
            instrument=expected,
            as_of=as_of,
            market_data=data,
            indicators=indicators,
            quality=quality,
            history_acquisition=acquisition,
            history_acquisition_id=content_id(acquisition)
            if acquisition is not None
            else None,
            evidence=_evidence(data, indicators),
        )
        return ResearchSnapshot(**payload.model_dump(), snapshot_id=content_id(payload))
    except ValidationError:
        raise DataQualityError("invalid_research_snapshot") from None


def parse_research_snapshot(payload: object) -> ResearchSnapshot:
    """Revalidate provenance, content IDs and actual calculations at an input boundary."""
    try:
        return ResearchSnapshot.model_validate(payload)
    except ValidationError:
        raise DataQualityError("invalid_research_snapshot") from None


def _effective_policy(captured: QualityPolicy, runtime: QualityPolicy) -> QualityPolicy:
    try:
        runtime = QualityPolicy.model_validate(runtime)
    except ValidationError:
        raise DataQualityError("invalid_policy") from None
    values = {
        key: min(value, getattr(runtime, key))
        for key, value in captured.model_dump().items()
        if key not in {"min_candles", "require_news"}
    }
    return QualityPolicy(
        **values,
        min_candles=max(captured.min_candles, runtime.min_candles),
        require_news=captured.require_news or runtime.require_news,
    )


def check_snapshot_quality(
    snapshot: ResearchSnapshot,
    *,
    expected: Instrument,
    now: datetime,
    policy: QualityPolicy,
) -> QualityAssessment:
    """Freshness tick for an already validated immutable snapshot; never relax its policy."""
    now = _as_of(now)
    if snapshot.instrument != expected:
        raise DataQualityError("instrument_mismatch")
    if snapshot.as_of > now:
        raise DataQualityError("future_research_snapshot")
    return validate_market_data(
        snapshot.market_data,
        expected=expected,
        now=now,
        policy=_effective_policy(snapshot.quality.policy, policy),
    )


def snapshot_context(snapshot: ResearchSnapshot) -> dict:
    """Bound history/feature arrays to their latest point; retain news without silent trimming."""
    history = snapshot.market_data.history
    return {
        "snapshot_id": snapshot.snapshot_id,
        "instrument": snapshot.instrument.model_dump(mode="json"),
        "as_of": snapshot.as_of.isoformat(),
        "quote": snapshot.market_data.quote.model_dump(mode="json"),
        "history": {
            "source": history.source,
            "interval_seconds": history.interval_seconds,
            "candle_count": len(history.candles),
            "first_open_time": history.candles[0].open_time.isoformat(),
            "received_at": history.received_at.isoformat(),
            "latest_candle": history.candles[-1].model_dump(mode="json"),
            "history_digest": snapshot.indicators.history_digest,
            "history_acquisition_id": snapshot.history_acquisition_id,
        },
        "indicators": {
            "implementation": snapshot.indicators.implementation,
            "config": snapshot.indicators.config.model_dump(mode="json"),
            "minimum_candles": snapshot.indicators.config.minimum_candles,
            "annualization_seconds": snapshot.indicators.annualization_seconds,
            "ready": snapshot.indicators.latest.ready,
            "latest": snapshot.indicators.latest.model_dump(mode="json"),
        },
        "quality_at_snapshot": snapshot.quality.model_dump(mode="json"),
        "news": [
            article.model_dump(mode="json") for article in snapshot.market_data.news
        ],
        "evidence": [item.model_dump(mode="json") for item in snapshot.evidence],
    }


def collect_research_snapshot(
    request: CandleRequest,
    *,
    quote_provider: QuoteProvider,
    history_provider: HistoryProvider,
    news_provider: NewsProvider | None,
    policy: QualityPolicy | None = None,
    indicator_config: IndicatorConfig | None = None,
    clock=utc_now,
) -> ResearchSnapshot:
    """Collect history, news, then a fresh quote. No partial result or hidden fallback."""
    policy, config = _configuration(policy, indicator_config)
    try:
        request = CandleRequest.model_validate(request)
    except ValidationError:
        raise DataQualityError("invalid_history_request") from None
    if request.count < config.minimum_candles:
        raise DataQualityError("indicators_not_ready")
    if request.count < policy.min_candles:
        raise DataQualityError("insufficient_candles")
    if policy.require_news and news_provider is None:
        raise DataQualityError("missing_news")
    before = _as_of(clock())
    if request.as_of > before:
        raise DataQualityError("future_history_request")
    try:
        history = HistorySnapshot.model_validate(
            history_provider.fetch_history(request)
        )
        if history.request != request:
            raise DataQualityError("history_request_mismatch")
        after_history = _as_of(clock())
        if after_history < before:
            raise DataQualityError("clock_regression")
        if history.series.received_at > after_history:
            raise DataQualityError("future_data")
        news = (
            news_provider.fetch_news(request.instrument)
            if news_provider is not None
            else ()
        )
        after_news = _as_of(clock())
        if after_news < after_history:
            raise DataQualityError("clock_regression")
        data = parse_market_data(
            {
                "quote": quote_provider.fetch_quote(request.instrument),
                "history": history.series,
                "news": news,
            }
        )
        completed = _as_of(clock())
        if completed < after_news:
            raise DataQualityError("clock_regression")
        return build_research_snapshot(
            data,
            expected=request.instrument,
            as_of=completed,
            policy=policy,
            indicator_config=config,
            history_acquisition=history,
        )
    except AutoHedgeError:
        raise
    except ValidationError:
        raise DataQualityError("invalid_provider_snapshot") from None
    except Exception:
        raise ProviderError("snapshot", "provider collection failed") from None
