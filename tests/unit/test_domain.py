from datetime import datetime, timedelta, timezone
import unittest

from pydantic import ValidationError

from autohedge.domain import Instrument, MarketSnapshot, PortfolioSnapshot, Position
from autohedge.domain import ResearchReport, ResearchRequest, Signal

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
INSTRUMENT = Instrument(
    asset_id="solana-mint", symbol="SOL", venue="solana", quote_currency="USDC"
)


class DomainTests(unittest.TestCase):
    def test_invalid_prices_and_naive_times_are_rejected(self):
        for price in (-1, 0, "NaN", "Infinity", True):
            with self.subTest(price=price), self.assertRaises(ValidationError):
                MarketSnapshot(
                    instrument=INSTRUMENT,
                    source="fixture",
                    observed_at=NOW,
                    price=price,
                )
        with self.assertRaises(ValidationError):
            MarketSnapshot(
                instrument=INSTRUMENT,
                source="fixture",
                observed_at=NOW.replace(tzinfo=None),
                price=10,
            )

    def test_amounts_are_decimal_and_timestamps_normalize_to_utc(self):
        snapshot = MarketSnapshot(
            instrument=INSTRUMENT,
            source="fixture",
            observed_at=NOW.astimezone(timezone(timedelta(hours=2))),
            price="0.123456789",
        )
        self.assertEqual(str(snapshot.price), "0.123456789")
        self.assertEqual(snapshot.observed_at, NOW)
        self.assertEqual(snapshot.observed_at.tzinfo, timezone.utc)
        with self.assertRaises(ValidationError):
            snapshot.price = 2

    def test_signal_does_not_accept_unknown_actions_or_execution_parameters(self):
        data = dict(
            action="HOLD",
            instrument=INSTRUMENT,
            created_at=NOW,
            confidence=0.5,
            reasoning="insufficient data",
        )
        self.assertEqual(Signal(**data).action.value, "HOLD")
        for extra in (
            {"action": "TRADE"},
            {"quantity": 100},
            {"confidence": float("nan")},
            {"confidence": True},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                Signal(**{**data, **extra})

    def test_negative_or_duplicate_positions_and_blank_tasks_are_rejected(self):
        with self.assertRaises(ValidationError):
            Position(instrument=INSTRUMENT, quantity=-1)
        position = Position(instrument=INSTRUMENT, quantity=1)
        with self.assertRaises(ValidationError):
            PortfolioSnapshot(
                observed_at=NOW, cash=1, equity=2, positions=(position, position)
            )
        with self.assertRaises(ValidationError):
            ResearchRequest(task="   ")

    def test_incomplete_report_and_live_flag_cannot_validate(self):
        from uuid import uuid4

        data = dict(
            run_id=uuid4(),
            request=ResearchRequest(task="analyze"),
            started_at=NOW,
            completed_at=NOW,
            stages=(),
        )
        for extra in ({}, {"execution_enabled": True}):
            with self.assertRaises(ValidationError):
                ResearchReport(**{**data, **extra})
