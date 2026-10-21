from datetime import timedelta
import unittest

from pydantic import ValidationError

from autohedge.ai import AgentRole, ModelResult
from autohedge.ai.audit import ModelCallAudit
from autohedge.domain import StageResult, ResearchReport, ResearchRequest
from autohedge.orchestration import ResearchOrchestrator
from test_run_lifecycle import make_team
from model_fixtures import NOW, model_reply, model_request


class ModelAuditTests(unittest.TestCase):
    def audit(self, role=AgentRole.QUANT):
        req = model_request()
        result = ModelResult(
            request=req,
            reply=model_reply(req),
            started_at=NOW,
            completed_at=NOW,
            elapsed_seconds=0,
        )
        return ModelCallAudit.from_result(role, result)

    def test_roundtrip_preserves_identity_without_storing_prompts(self):
        call = self.audit()
        self.assertEqual(
            ModelCallAudit.model_validate_json(call.model_dump_json()), call
        )
        self.assertEqual(call.usage.total_tokens, 51)
        self.assertNotIn("messages", call.model_dump())
        self.assertNotIn("content", call.model_dump())
        self.assertEqual(call.role, AgentRole.QUANT)

    def test_wrong_role_or_outside_stage_times_are_rejected(self):
        for call in (
            self.audit(AgentRole.TRADING_DIRECTOR),
            self.audit().model_copy(update={"started_at": NOW - timedelta(seconds=1)}),
        ):
            with self.assertRaises(ValidationError):
                StageResult(
                    stage="quant",
                    content="research",
                    started_at=NOW,
                    completed_at=NOW,
                    model_calls=(call,),
                )
        good = StageResult(
            stage="quant",
            content="research",
            started_at=NOW,
            completed_at=NOW,
            model_calls=(self.audit(),),
        )
        self.assertEqual(len(good.model_calls), 1)

    def test_reusing_director_request_id_between_stages_is_rejected(self):
        report = ResearchOrchestrator(lambda now: make_team([]), clock=lambda: NOW).run(
            ResearchRequest(task="audit")
        )
        call = self.audit(AgentRole.TRADING_DIRECTOR)
        stages = list(report.stages)
        stages[0] = stages[0].model_copy(update={"model_calls": (call,)})
        stages[-1] = stages[-1].model_copy(update={"model_calls": (call,)})
        with self.assertRaises(ValidationError):
            ResearchReport.model_validate(
                report.model_copy(update={"stages": tuple(stages)})
            )

    def test_forged_or_unbounded_audit_cannot_bypass_nested_validation(self):
        audit = self.audit()
        for fields in (
            {"elapsed_seconds": 30},
            {"max_output_tokens": 1},
            {"completed_at": NOW - timedelta(seconds=1)},
        ):
            with self.assertRaises(ValidationError):
                StageResult(
                    stage="quant",
                    content="research",
                    started_at=NOW,
                    completed_at=NOW,
                    model_calls=(audit.model_copy(update=fields),),
                )
