from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rule_agent.config import Settings
from rule_agent.excel_reader import RuleRecord, read_published_rules
from rule_agent.service import RuleDecompositionService


class FakeModelClient:
    def generate(self, system_prompt: str, user_prompt: str, schema: dict) -> dict:
        return {
            "facts": [
                {"fact_id": "F01", "description": "患者是否符合透析患者条件"},
                {"fact_id": "F02", "description": "患者是否符合高磷血症条件"},
            ],
            "logical_expression": "F01 AND F02",
            "decision_semantics": "SUPPORT_APPEAL_IF_TRUE",
            "time_requirements": "按该规则及疑点费用发生时间核实",
            "unresolved": [],
        }


class RuleAgentTests(unittest.TestCase):
    def test_thinking_mode_is_allowed(self) -> None:
        settings = Settings(
            base_url="https://example.invalid/v1",
            api_key="",
            model_name="qwen3.6-27b",
            model_param_scale="27B",
            response_format="json_object",
            enable_thinking=True,
            timeout_seconds=120,
            max_retries=0,
        )
        settings.validate()

    def test_actual_excel_contains_sixty_rules(self) -> None:
        input_path = Path(__file__).resolve().parents[2] / "负面清单" / "负面清单.xlsx"
        records = read_published_rules(input_path)
        self.assertEqual(len(records), 60)

    def test_service_writes_one_json_file_per_rule(self) -> None:
        record = RuleRecord("RULE-001", "碳酸镧限定支付范围", "POC-DRUG-001", "碳酸镧", "碳酸镧仅限透析患者且存在高磷血症时使用。", date(2026, 1, 1), "V1")
        service = RuleDecompositionService(FakeModelClient())
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            summary = service.run([record], output_dir)
            self.assertEqual(summary.created, 1)
            self.assertEqual(summary.updated, 0)
            aggregate = json.loads((output_dir / "规则拆解结果.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate[0]["rule_id"], "RULE-001")
            self.assertEqual(set(aggregate[0]["result"]), {"facts", "logical_expression", "decision_semantics", "time_requirements", "unresolved"})

    def test_service_appends_new_rule_and_updates_only_matching_file(self) -> None:
        first = RuleRecord("RULE-001", "规则一", "ITEM-001", "项目一", "规则一原文", date(2026, 1, 1), "V1")
        second = RuleRecord("RULE-002", "规则二", "ITEM-002", "项目二", "规则二原文", date(2026, 1, 1), "V1")
        service = RuleDecompositionService(FakeModelClient())
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            initial = service.run([first, second], output_dir)
            aggregate_before = (output_dir / "规则拆解结果.json").read_text(encoding="utf-8")
            rerun = service.run([first], output_dir)

            self.assertEqual((initial.created, initial.updated), (2, 0))
            self.assertEqual((rerun.created, rerun.updated), (0, 1))
            self.assertEqual((output_dir / "规则拆解结果.json").read_text(encoding="utf-8"), aggregate_before)
            self.assertEqual(sorted(path.name for path in output_dir.glob("*.json")), ["规则拆解结果.json"])

    def test_append_mode_skips_existing_rule(self) -> None:
        record = RuleRecord("RULE-001", "规则一", "ITEM-001", "项目一", "规则一原文", date(2026, 1, 1), "V1")
        service = RuleDecompositionService(FakeModelClient())
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            service.run([record], output_dir)
            before = (output_dir / "规则拆解结果.json").read_text(encoding="utf-8")
            summary = service.run([record], output_dir, overwrite_existing=False)

            self.assertEqual((summary.created, summary.updated, summary.skipped), (0, 0, 1))
            self.assertEqual((output_dir / "规则拆解结果.json").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
