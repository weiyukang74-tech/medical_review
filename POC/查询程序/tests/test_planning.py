from __future__ import annotations

import unittest

from evidence_retriever.planning import index_query_plans, resolve_query_plan


class QueryPlanResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {
            "query_id": "QP-P01-01",
            "supports_facts": [
                {
                    "proposition_id": "P01",
                    "一级证据域": "手术操作",
                    "二级证据标签": "操作记录",
                    "target_fact": "确认胸腹水穿刺操作",
                }
            ],
            "entities": [
                {
                    "entity_id": "E01",
                    "source_exact": "胸腹水穿刺",
                    "core_terms": ["胸腹水"],
                    "qualifiers": ["穿刺"],
                    "normalized_terms": ["胸腔穿刺", "腹腔穿刺"],
                    "alias_terms": [],
                }
            ],
            "search_fields": ["hilist_name", "hosplist_name"],
            "fallback_search_fields": ["set_name"],
            "fallback_strategy": ["ALL_CHRG_TYPE_EXPANDED"],
        }
        self.index = index_query_plans({"plans": [self.plan]})

    def test_exact_fact_uses_own_plan(self) -> None:
        resolved = resolve_query_plan(
            self.index,
            ("P01", "手术操作", "操作记录", "确认胸腹水穿刺操作"),
        )
        self.assertIs(resolved, self.plan)

    def test_missing_fact_inherits_same_proposition_entities(self) -> None:
        resolved = resolve_query_plan(
            self.index,
            ("P01", "结算", "结算单据", "确认操作类费用构成"),
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["plan_origin"], "INHERITED_SAME_PROPOSITION")
        self.assertEqual(resolved["entities"][0]["source_exact"], "胸腹水穿刺")
        self.assertEqual(resolved["inherited_from_query_ids"], ["QP-P01-01"])

    def test_missing_fact_does_not_inherit_other_proposition(self) -> None:
        resolved = resolve_query_plan(
            self.index,
            ("P02", "结算", "结算单据", "确认操作类费用构成"),
        )
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
