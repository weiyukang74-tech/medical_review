from __future__ import annotations

import json
import unittest
from pathlib import Path

from evidence_retriever.context_builder import build_review_context
from evidence_retriever.service import RetrievalService


PROGRAM_ROOT = Path(__file__).resolve().parents[1]
POC_ROOT = PROGRAM_ROOT.parent
WORKSPACE_ROOT = POC_ROOT.parent
RULE_EVIDENCE = POC_ROOT / "规则证据结果" / "规则证据结果.json"
MAPPING = POC_ROOT / "标签视图映射" / "规则证据标签关系映射表_v2.xlsx"
MEDICAL_DATA = WORKSPACE_ROOT / "datat" / "脱敏病历.md"


class RetrievalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = RetrievalService(MAPPING, MEDICAL_DATA)
        self.rules = self.service.load_rule_evidence(RULE_EVIDENCE)

    def test_p01_has_compact_tag_results_and_full_medical_record(self) -> None:
        result = self.service.run(
            self.rules,
            medins_id="H44030500096",
            mdtrt_id="119797913",
            proposition_id="P01",
            secondary_mode="auto",
        )

        self.assertEqual(set(result), {"rule_id", "propositions"})
        self.assertEqual(result["rule_id"], "RULE-001")
        self.assertEqual(len(result["propositions"]), 1)
        proposition = result["propositions"][0]
        self.assertEqual(proposition["proposition_id"], "P01")

        by_tag = {
            (entry["一级证据域"], entry["二级证据标签"]): entry
            for entry in proposition["evidence"]
        }
        self.assertEqual(
            by_tag[("患者", "性别")]["query_result"]["data"],
            [{"gend": "2"}],
        )

        reproductive = by_tag[("患者", "生育状态")]["query_result"]
        self.assertEqual(reproductive["status"], "RETRIEVED")
        self.assertEqual(reproductive["source"], "DS-M-IP-002 入院记录")
        record = reproductive["data"][0]
        self.assertEqual(record["recordName"], "再次入院记录")
        self.assertGreater(len(record["recordContent"]), 20)
        self.assertIn("现病史", record["recordContent"])
        self.assertIn("绝经后", record["recordContent"]["月经史"])

        pathology = by_tag[("病理", "病理标志")]["query_result"]
        self.assertEqual(pathology["status"], "NOT_RETRIEVED")
        self.assertEqual(pathology["data"], [])

    def test_diagnosis_keyword_comes_from_target_fact(self) -> None:
        result = self.service.run(
            self.rules,
            medins_id="H44030500096",
            mdtrt_id="119797913",
            proposition_id="P01",
            secondary_mode="auto",
        )
        evidence = result["propositions"][0]["evidence"]
        diagnosis = next(
            entry for entry in evidence if entry["二级证据标签"] == "标准诊断"
        )["query_result"]

        self.assertEqual(
            diagnosis["matched_by"],
            {
                "field": "diseName",
                "operator": "contains",
                "value": "乳腺癌",
                "value_source": "target_fact",
            },
        )
        self.assertEqual(len(diagnosis["data"]), 2)
        self.assertTrue(
            all("乳腺癌" in record["diseName"] for record in diagnosis["data"])
        )

    def test_full_output_is_json_serializable(self) -> None:
        result = self.service.run(
            self.rules,
            medins_id="H44030500096",
            mdtrt_id="119797913",
            secondary_mode="auto",
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertIn('"propositions"', serialized)
        self.assertNotIn('"evidence_pool"', serialized)
        self.assertNotIn('"summary"', serialized)

    def test_review_context_merges_rules_and_deduplicates_documents(self) -> None:
        query_result = self.service.run(
            self.rules,
            medins_id="H44030500096",
            mdtrt_id="119797913",
            secondary_mode="auto",
        )
        context = build_review_context(self.rules, query_result)

        self.assertEqual(
            set(context),
            {
                "rule_id",
                "review_basis",
                "propositions",
                "structured_data_pool",
                "medical_documents",
                "program_checks",
            },
        )
        self.assertEqual(context["rule_id"], "RULE-001")
        self.assertEqual(context["review_basis"]["violation_item"], "依西美坦片")
        self.assertEqual(len(context["propositions"]), 3)

        p01 = next(
            proposition
            for proposition in context["propositions"]
            if proposition["proposition_id"] == "P01"
        )
        reproductive = next(
            item
            for item in p01["evidence"]
            if item["二级证据标签"] == "生育状态"
        )
        self.assertEqual(reproductive["necessity"], "REQUIRED")
        document_id = reproductive["query_result"]["document_refs"][0]

        document = next(
            item
            for item in context["medical_documents"]
            if item["document_id"] == document_id
        )
        self.assertEqual(document["recordName"], "再次入院记录")
        self.assertIn("现病史", document["recordContent"])
        self.assertIn("绝经后", document["recordContent"]["月经史"])

        all_document_refs = [
            document_ref
            for proposition in context["propositions"]
            for evidence in proposition["evidence"]
            for document_ref in evidence["query_result"].get("document_refs", [])
        ]
        self.assertGreater(len(all_document_refs), len(set(all_document_refs)))
        self.assertEqual(
            len(context["medical_documents"]),
            len(set(all_document_refs)),
        )

        diagnosis = next(
            item
            for item in p01["evidence"]
            if item["二级证据标签"] == "标准诊断"
        )
        self.assertNotIn("matched_by", diagnosis["query_result"])
        structured_refs = diagnosis["query_result"]["structured_data_refs"]
        self.assertEqual(
            len(structured_refs),
            2,
        )
        self.assertTrue(
            all(reference in context["structured_data_pool"] for reference in structured_refs)
        )


if __name__ == "__main__":
    unittest.main()
