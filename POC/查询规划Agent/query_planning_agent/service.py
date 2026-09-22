from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .client import OpenAICompatibleClient
from .prompt import (
    FALLBACK_SEARCH_FIELDS,
    SEARCH_FIELDS,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)


class PlanningValidationError(ValueError):
    pass


EXPENSE_TRIGGER_DOMAINS = {"费用", "手术操作"}

FALLBACK_STRATEGY = [
    "ALL_CHRG_TYPE_SOURCE_EXACT",
    "ALL_CHRG_TYPE_CORE_QUALIFIER",
    "ALL_CHRG_TYPE_EXPANDED",
]


def load_rule_evidence(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("规则证据JSON顶层必须是数组")
    return payload


def collect_rule_facts(
    rule: dict[str, Any],
    expense_tags: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    expense_tags = expense_tags or set()
    for proposition in rule.get("result", {}).get("appeal_propositions", []):
        proposition_id = str(proposition.get("proposition_id", "")).strip()
        for group_name in ("required_tags", "supplementary_tags"):
            for tag in proposition.get(group_name, []):
                primary = str(tag.get("一级证据域", "")).strip()
                secondary = str(tag.get("二级证据标签", "")).strip()
                facts.append(
                    {
                        "proposition_id": proposition_id,
                        "一级证据域": primary,
                        "二级证据标签": secondary,
                        "target_fact": str(tag.get("target_fact", "")).strip(),
                        "necessity": str(tag.get("necessity", "")).strip(),
                        "mapped_to_expense": (primary, secondary) in expense_tags,
                        "triggers_expense": primary in EXPENSE_TRIGGER_DOMAINS,
                    }
                )
    return facts


def _copy_fact(fact: dict[str, Any]) -> dict[str, str]:
    return {
        "proposition_id": str(fact.get("proposition_id", "")).strip(),
        "一级证据域": str(fact.get("一级证据域", "")).strip(),
        "二级证据标签": str(fact.get("二级证据标签", "")).strip(),
        "target_fact": str(fact.get("target_fact", "")).strip(),
        "necessity": str(fact.get("necessity", "")).strip(),
    }


class QueryPlanningService:
    def __init__(self, client: OpenAICompatibleClient):
        self.client = client

    def plan(
        self,
        rule: dict[str, Any],
        candidate_facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        domain_facts = [
            fact for fact in candidate_facts if fact.get("triggers_expense") is True
        ]
        if not domain_facts:
            return self.empty_plan(rule)

        rule_id = str(rule.get("rule_id", "")).strip()
        violation_item = str(
            rule.get("result", {}).get("source", {}).get("violation_item", "")
        ).strip()
        violation_description = str(
            rule.get("result", {}).get("source", {}).get("violation_description", "")
        ).strip()
        planning_facts = [
            {
                "fact_id": f"F{index:02d}",
                **_copy_fact(fact),
            }
            for index, fact in enumerate(domain_facts, start=1)
        ]
        user_prompt = build_user_prompt(
            rule_id,
            violation_item,
            violation_description,
            planning_facts,
        )
        model_result = self.client.generate(SYSTEM_PROMPT, user_prompt)
        try:
            keyword_groups = self._validate_model_result(
                model_result,
                rule_id,
                violation_item,
                violation_description,
                planning_facts,
            )
        except PlanningValidationError as validation_error:
            print(
                f"[{rule_id}] 首次查询规划未通过校验，自动修正：{validation_error}",
                flush=True,
            )
            repaired_result = self.client.generate(
                SYSTEM_PROMPT,
                build_repair_prompt(
                    rule_id,
                    violation_item,
                    violation_description,
                    planning_facts,
                    model_result,
                    str(validation_error),
                ),
            )
            keyword_groups = self._validate_model_result(
                repaired_result,
                rule_id,
                violation_item,
                violation_description,
                planning_facts,
            )

        proposition_counters: defaultdict[str, int] = defaultdict(int)
        plans: list[dict[str, Any]] = []
        for fact in planning_facts:
            proposition_id = fact["proposition_id"]
            proposition_counters[proposition_id] += 1
            plans.append(
                self._build_plan(
                    fact,
                    keyword_groups[fact["fact_id"]],
                    proposition_counters[proposition_id],
                )
            )

        result = self.empty_plan(rule)
        result["plans"] = plans
        return result

    @staticmethod
    def empty_plan(rule: dict[str, Any]) -> dict[str, Any]:
        return {
            "rule_id": str(rule.get("rule_id", "")),
            "case_parameters": {
                "mdtrt_id": str(rule.get("mdtrt_id", "") or ""),
                "medins_id": str(rule.get("medins_id", "") or ""),
                "visit_no": str(rule.get("visit_no", "") or ""),
            },
            "plans": [],
        }

    @staticmethod
    def _validate_model_result(
        result: dict[str, Any],
        rule_id: str,
        violation_item: str,
        violation_description: str,
        planning_facts: list[dict[str, str]],
    ) -> dict[str, list[dict[str, Any]]]:
        if str(result.get("rule_id", "")).strip() != rule_id:
            raise PlanningValidationError("模型输出rule_id与输入不一致")
        model_facts = result.get("facts")
        if not isinstance(model_facts, list):
            raise PlanningValidationError("模型输出facts必须是数组")

        facts_by_id = {fact["fact_id"]: fact for fact in planning_facts}
        indexed: dict[str, list[dict[str, Any]]] = {}
        for model_fact in model_facts:
            if not isinstance(model_fact, dict):
                raise PlanningValidationError("模型输出的事实必须是对象")
            fact_id = str(model_fact.get("fact_id", "")).strip()
            if fact_id not in facts_by_id:
                raise PlanningValidationError(f"模型输出了不存在的fact_id: {fact_id}")
            if fact_id in indexed:
                raise PlanningValidationError(f"模型重复输出fact_id: {fact_id}")

            groups = model_fact.get("keyword_groups")
            if not isinstance(groups, list) or not groups:
                raise PlanningValidationError(f"{fact_id}.keyword_groups不能为空")
            if len(groups) > 6:
                raise PlanningValidationError(f"{fact_id}.keyword_groups不能超过6组")

            target_fact = facts_by_id[fact_id]["target_fact"]
            seen_phrases: set[str] = set()
            validated_groups: list[dict[str, Any]] = []
            for group in groups:
                if not isinstance(group, dict):
                    raise PlanningValidationError(f"{fact_id}的关键词组必须是对象")
                source_phrase = str(group.get("source_phrase", "")).strip()
                if not source_phrase or source_phrase in seen_phrases:
                    raise PlanningValidationError(
                        f"{fact_id}.source_phrase为空或重复"
                    )
                if source_phrase in target_fact:
                    source_path = "target_fact"
                elif source_phrase in violation_item:
                    source_path = "result.source.violation_item"
                elif source_phrase in violation_description:
                    source_path = "result.source.violation_description"
                else:
                    source_path = "query_planning_agent.expansion"
                seen_phrases.add(source_phrase)
                for field in ("required_terms", "alternative_terms"):
                    values = group.get(field)
                    if not isinstance(values, list) or any(
                        not isinstance(value, str) or not value.strip()
                        for value in values
                    ):
                        raise PlanningValidationError(
                            f"{fact_id}.{field}必须是字符串数组"
                        )
                if not group["required_terms"]:
                    raise PlanningValidationError(
                        f"{fact_id}.required_terms不能为空"
                    )
                validated_group = dict(group)
                validated_group["source_path"] = source_path
                validated_groups.append(validated_group)
            indexed[fact_id] = validated_groups

        missing = sorted(set(facts_by_id) - set(indexed))
        if missing:
            raise PlanningValidationError(f"模型遗漏事实: {missing}")
        return indexed

    @staticmethod
    def _build_plan(
        fact: dict[str, str],
        keyword_groups: list[dict[str, Any]],
        proposition_sequence: int,
    ) -> dict[str, Any]:
        entities: list[dict[str, Any]] = []
        for index, group in enumerate(keyword_groups, start=1):
            source_phrase = str(group["source_phrase"]).strip()
            required_terms = list(
                dict.fromkeys(str(term).strip() for term in group["required_terms"])
            )
            alternative_terms = list(
                dict.fromkeys(
                    str(term).strip() for term in group["alternative_terms"]
                )
            )
            entities.append(
                {
                    "entity_id": f"E{index:02d}",
                    "source_exact": source_phrase,
                    "source_path": str(group["source_path"]),
                    "core_terms": required_terms[:1],
                    "qualifiers": required_terms[1:],
                    "normalized_terms": alternative_terms,
                    "alias_terms": [],
                }
            )

        proposition_id = fact["proposition_id"]
        return {
            "query_id": f"QP-{proposition_id}-{proposition_sequence:02d}",
            "supports_facts": [_copy_fact(fact)],
            "source": "DS-S-002 费用明细",
            "view_name": "费用明细",
            "query_type": "STRUCTURED",
            "plan_origin": "TAG_MAPPED",
            "supplement_reason": None,
            "entities": entities,
            "search_fields": SEARCH_FIELDS,
            "fallback_search_fields": FALLBACK_SEARCH_FIELDS,
            "fallback_strategy": FALLBACK_STRATEGY,
        }

    @staticmethod
    def write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
