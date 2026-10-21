"""Public research facade; each cycle owns its agents, report and conversation."""

from pathlib import Path
import os
import tempfile

from autohedge.config import Settings, load_settings
from autohedge.domain import (
    Instrument,
    MarketSnapshot,
    PortfolioSnapshot,
    ResearchRequest,
)
from autohedge.data.contracts import MarketData
from autohedge.errors import ConfigurationError
from autohedge.logging import configure_logging
from autohedge.orchestration import ResearchOrchestrator, utc_now
from autohedge.snapshots import ResearchSnapshot, parse_research_snapshot


class AutoHedge:
    def __init__(
        self,
        name: str = "autohedge",
        description: str = "multi-agent research",
        output_dir: str = "outputs",
        output_file_path: str | None = None,
        output_type: str = "list",
        *,
        settings: Settings | None = None,
        team_factory=None,
        clock=utc_now,
        monotonic=None,
    ):
        if output_type not in {"list", "dict", "str"}:
            raise ConfigurationError("output_type must be list, dict or str")
        self.name = name
        self.description = description
        self.output_type = output_type
        self.output_dir = Path(output_dir)
        self.output_file_path = output_file_path
        self.settings = settings if settings is not None else load_settings()
        configure_logging(self.settings)
        # Resolve our configuration before the SDK's import-time dotenv behavior.
        from swarms import Conversation

        # Swarms may install its own sinks while importing; restore app ownership.
        configure_logging(self.settings)
        self._conversation_type = Conversation
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.last_report = None
        self.conversation = self._conversation_type(time_enabled=True)

        def default_factory(now, *, snapshot_mode=False):
            settings = (
                self.settings.model_copy(update={"search_enabled": False})
                if snapshot_mode
                else self.settings
            )
            settings.require_research_credentials()
            from autohedge.workers import build_agent_team

            return build_agent_team(now, settings=settings)

        self._snapshot_team_factory = (
            (lambda now: default_factory(now, snapshot_mode=True))
            if team_factory is None
            else None
        )

        options = {
            "clock": clock,
            "timeout": self.settings.cycle_timeout,
            "quality_policy": self.settings.market_quality,
        }
        if monotonic is not None:
            options["monotonic"] = monotonic
        self.orchestrator = ResearchOrchestrator(
            team_factory if team_factory is not None else default_factory, **options
        )

    def run_report(
        self,
        task: str,
        *,
        market: MarketSnapshot | None = None,
        portfolio: PortfolioSnapshot | None = None,
        instrument: Instrument | None = None,
        market_data: MarketData | None = None,
        snapshot: ResearchSnapshot | None = None,
    ):
        """Return a validated, non-executable report; failures leave no completed report."""
        self.last_report = None
        self.conversation = self._conversation_type(time_enabled=True)
        if snapshot is not None:
            snapshot = parse_research_snapshot(snapshot)
        request = ResearchRequest(
            task=task,
            market=market,
            portfolio=portfolio,
            instrument=instrument,
            market_data=market_data,
            snapshot=snapshot,
        )
        report = self.orchestrator.run(
            request,
            team_factory=self._snapshot_team_factory if snapshot is not None else None,
        )
        # Conversation is a presentation of this cycle, never another agent's memory.
        self.conversation.add(role="user", content=request.task)
        for result in report.stages:
            self.conversation.add(role=result.stage.value, content=result.content)
        for warning in report.warnings:
            self.conversation.add(role="warning", content=warning)
        if self.output_file_path is not None:
            destination = Path(self.output_file_path)
            if not destination.is_absolute():
                destination = self.output_dir / destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=destination.parent, delete=False
                ) as stream:
                    temporary = stream.name
                    stream.write(report.model_dump_json(indent=2))
                os.replace(temporary, destination)
            finally:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
        self.last_report = report
        return report

    def run(
        self,
        task: str,
        *,
        market: MarketSnapshot | None = None,
        portfolio: PortfolioSnapshot | None = None,
        instrument: Instrument | None = None,
        market_data: MarketData | None = None,
        snapshot: ResearchSnapshot | None = None,
    ):
        report = self.run_report(
            task,
            market=market,
            portfolio=portfolio,
            instrument=instrument,
            market_data=market_data,
            snapshot=snapshot,
        )
        if self.output_type == "dict":
            return report.model_dump(mode="json")
        if self.output_type == "str":
            return report.model_dump_json(indent=2)
        return self.conversation.return_messages_as_list()
