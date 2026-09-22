from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .mapping import SourceRoute, index_routes, load_routes
from .medical_data import MarkdownMedicalData
from .planning import PlanKey, index_query_plans, resolve_query_plan


EXPECTED_FIELDS: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("患者", "性别", "患者信息"): ("gend",),
    ("患者", "生育状态", "患者信息"): ("matnStas",),
    ("诊断", "标准诊断", "诊断信息"): ("diseName",),
    ("评估", "疾病分期", "诊断信息"): ("diseName",),
    ("病情", "活动进展", "诊断信息"): ("diseName",),
}

DIAGNOSIS_RESULT_FIELDS = (
    "diseName",
    "diseCodg",
    "maindiseFlag",
    "inoutDiseType",
)

DIAGNOSIS_SUFFIXES = (
    "术后",
    "待查",
    "可能",
    "复发",
    "转移",
)

EXPENSE_FIELD_ALIASES = {
    "chrg_type": "chrgType",
    "hilist_name": "hilistName",
    "hosplist_name": "hosplistName",
    "set_name": "setName",
}


class RetrievalService:
    def __init__(self, mapping_path: Path, medical_data_path: Path):
        self.mapping_path = mapping_path
        self.medical_data_path = medical_data_path
        self.routes = load_routes(mapping_path)
        self.route_index = index_routes(self.routes)
        self.medical_data = MarkdownMedicalData(medical_data_path)
        self.query_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def load_rule_evidence(path: Path) -> list[dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("规则证据 JSON 顶层必须是数组")
        return payload

    @staticmethod
    def _query_key(route: SourceRoute, medins_id: str, mdtrt_id: str) -> str:
        return json.dumps(
            {
                "source_code": route.source_code,
                "view_name": route.view_name,
                "medinsId": medins_id,
                "mdtrtId": mdtrt_id,
                "recordName": route.record_name,
                "recordType": route.record_type,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def _evidence_id(query_key: str) -> str:
        digest = hashlib.sha1(query_key.encode("utf-8")).hexdigest()[:10].upper()
        return f"EV-{digest}"

    def _execute_route(
        self,
        route: SourceRoute,
        medins_id: str,
        mdtrt_id: str,
    ) -> dict[str, Any]:
        query_key = self._query_key(route, medins_id, mdtrt_id)
        if query_key in self.query_cache:
            return self.query_cache[query_key]

        records: list[dict[str, object]] = []
        if "当前六视图无对应" in route.view_name:
            status = "VIEW_UNAVAILABLE"
        elif route.document_kind == "病历":
            if route.record_name is None and route.record_type is None:
                status = "RECORD_NAME_NOT_MAPPED"
            else:
                records = self.medical_data.query_records(
                    medins_id,
                    mdtrt_id,
                    route.record_name,
                    route.record_type,
                )
                status = "FOUND" if records else "NOT_FOUND"
        else:
            records = self.medical_data.query_structured(route.view_name, medins_id, mdtrt_id)
            status = "FOUND" if records else "NOT_FOUND"

        evidence = {
            "evidence_id": self._evidence_id(query_key),
            "route": route,
            "status": status,
            "records": records,
        }
        self.query_cache[query_key] = evidence
        return evidence

    @staticmethod
    def _missing_expected_fields(
        records: list[dict[str, object]],
        expected_fields: tuple[str, ...],
    ) -> list[str]:
        return [
            field
            for field in expected_fields
            if all(field not in record or record[field] in (None, "") for record in records)
        ]

    @staticmethod
    def _needs_secondary(attempts: list[dict[str, Any]]) -> bool:
        if not attempts:
            return True
        statuses = {attempt["status"] for attempt in attempts}
        return bool(
            statuses <= {"NOT_FOUND", "VIEW_UNAVAILABLE", "RECORD_NAME_NOT_MAPPED"}
            or "EXPECTED_FIELD_MISSING" in statuses
        )

    @staticmethod
    def _source_label(route: SourceRoute) -> str:
        return f"{route.source_code} {route.source_name}"

    @staticmethod
    def _diagnosis_keyword(
        target_fact: str,
        records: list[dict[str, object]],
    ) -> str | None:
        candidates: set[str] = set()
        for record in records:
            diagnosis_name = str(record.get("diseName", "")).strip()
            if not diagnosis_name:
                continue
            candidates.add(diagnosis_name)
            normalized = diagnosis_name
            for suffix in DIAGNOSIS_SUFFIXES:
                if normalized.endswith(suffix):
                    normalized = normalized[: -len(suffix)].strip()
            if len(normalized) >= 2:
                candidates.add(normalized)
        matches = [candidate for candidate in candidates if candidate in target_fact]
        return max(matches, key=len) if matches else None

    def _format_structured_records(
        self,
        route: SourceRoute,
        primary_domain: str,
        secondary_tag: str,
        target_fact: str,
        records: list[dict[str, object]],
        query_plan: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, object]], dict[str, Any] | None]:
        if route.view_name == "费用明细":
            if query_plan is None:
                return [], {
                    "status": "QUERY_SCOPE_UNRESOLVED",
                    "reason": "当前事实无专属计划，且同一申诉命题中没有可继承的争议实体",
                }
            return self._filter_expense_records(records, query_plan)
        if route.view_name == "诊断信息":
            keyword = self._diagnosis_keyword(target_fact, records)
            if keyword:
                matched_records = [
                    record
                    for record in records
                    if keyword in str(record.get("diseName", ""))
                ]
                projected = [
                    {
                        field: record[field]
                        for field in DIAGNOSIS_RESULT_FIELDS
                        if field in record
                    }
                    for record in matched_records
                ]
                return projected, {
                    "field": "diseName",
                    "operator": "contains",
                    "value": keyword,
                    "value_source": "target_fact",
                }

        expected_fields = EXPECTED_FIELDS.get(
            (primary_domain, secondary_tag, route.view_name),
            (),
        )
        if expected_fields:
            return [
                {
                    field: record[field]
                    for field in expected_fields
                    if field in record
                }
                for record in records
                if any(record.get(field) not in (None, "") for field in expected_fields)
            ], None
        return records, None

    @staticmethod
    def _normalize_search_text(value: object) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return re.sub(r"[\s\-_/\\,，。;；:：()（）\[\]【】]+", "", text)

    @classmethod
    def _filter_expense_records(
        cls,
        records: list[dict[str, object]],
        query_plan: dict[str, Any],
    ) -> tuple[list[dict[str, object]], dict[str, Any]]:
        charge_type = query_plan.get("charge_type") or {}
        primary_codes = {str(code).zfill(2) for code in charge_type.get("primary", [])}
        fallback_codes = {str(code).zfill(2) for code in charge_type.get("fallback", [])}
        search_fields = [
            EXPENSE_FIELD_ALIASES.get(field, field)
            for field in query_plan.get("search_fields", [])
        ]
        fallback_fields = [
            EXPENSE_FIELD_ALIASES.get(field, field)
            for field in query_plan.get("fallback_search_fields", [])
        ]

        def charge_code(record: dict[str, object]) -> str:
            return str(record.get("chrgType", record.get("chrg_type", "")) or "").zfill(2)

        def matches_name(
            record: dict[str, object],
            fields: list[str],
            terms: list[str],
        ) -> bool:
            normalized_terms = [cls._normalize_search_text(term) for term in terms]
            values = [cls._normalize_search_text(record.get(field, "")) for field in fields]
            return any(term and term in value for term in normalized_terms for value in values)

        def matches_core_qualifiers(
            record: dict[str, object],
            fields: list[str],
            core_terms: list[str],
            qualifiers: list[str],
        ) -> bool:
            required_terms = [
                cls._normalize_search_text(term)
                for term in [*core_terms, *qualifiers]
                if str(term).strip()
            ]
            if not required_terms:
                return False
            values = [cls._normalize_search_text(record.get(field, "")) for field in fields]
            return any(all(term in value for term in required_terms) for value in values)

        entities = query_plan.get("entities")
        if not isinstance(entities, list) or not entities:
            legacy_terms = [
                str(term.get("value", "")).strip()
                for term in query_plan.get("search_terms", [])
                if str(term.get("value", "")).strip()
            ]
            entities = [
                {
                    "entity_id": "E01",
                    "source_exact": legacy_terms[0] if legacy_terms else "",
                    "core_terms": legacy_terms[:1],
                    "qualifiers": [],
                    "normalized_terms": legacy_terms[1:],
                    "alias_terms": [],
                }
            ]

        all_matched: list[dict[str, object]] = []
        entity_results: list[dict[str, Any]] = []
        all_terms: list[str] = []
        for entity in entities:
            source_exact = str(entity.get("source_exact", "")).strip()
            core_terms = [
                str(term).strip()
                for term in entity.get("core_terms", [])
                if str(term).strip()
            ]
            qualifiers = [
                str(term).strip()
                for term in entity.get("qualifiers", [])
                if str(term).strip()
            ]
            expanded_terms = list(
                dict.fromkeys(
                    str(term).strip()
                    for term in list(entity.get("normalized_terms", []))
                    + list(entity.get("alias_terms", []))
                    if str(term).strip() and str(term).strip() != source_exact
                )
            )
            all_terms.extend([source_exact, *core_terms, *qualifiers, *expanded_terms])
            stages = [
                (
                    "PRIMARY_CHRG_TYPE_SOURCE_EXACT",
                    lambda record: charge_code(record) in primary_codes,
                    search_fields,
                    [source_exact],
                ),
                (
                    "PRIMARY_CHRG_TYPE_CORE_QUALIFIER",
                    lambda record: charge_code(record) in primary_codes,
                    search_fields,
                    None,
                ),
                (
                    "PRIMARY_CHRG_TYPE_EXPANDED",
                    lambda record: charge_code(record) in primary_codes,
                    search_fields,
                    expanded_terms,
                ),
                (
                    "FALLBACK_CHRG_TYPE_SOURCE_EXACT",
                    lambda record: charge_code(record) in fallback_codes,
                    search_fields,
                    [source_exact],
                ),
                (
                    "FALLBACK_CHRG_TYPE_CORE_QUALIFIER",
                    lambda record: charge_code(record) in fallback_codes,
                    search_fields,
                    None,
                ),
                (
                    "FALLBACK_CHRG_TYPE_EXPANDED",
                    lambda record: charge_code(record) in fallback_codes,
                    search_fields,
                    expanded_terms,
                ),
                (
                    "ALL_CHRG_TYPE_SOURCE_EXACT",
                    lambda record: True,
                    search_fields,
                    [source_exact],
                ),
                (
                    "ALL_CHRG_TYPE_CORE_QUALIFIER",
                    lambda record: True,
                    search_fields,
                    None,
                ),
                (
                    "ALL_CHRG_TYPE_EXPANDED",
                    lambda record: True,
                    search_fields,
                    expanded_terms,
                ),
                (
                    "PRIMARY_AND_FALLBACK_SET_NAME_SOURCE_EXACT",
                    lambda record: charge_code(record) in primary_codes | fallback_codes,
                    fallback_fields,
                    [source_exact],
                ),
                (
                    "PRIMARY_AND_FALLBACK_SET_NAME_EXPANDED",
                    lambda record: charge_code(record) in primary_codes | fallback_codes,
                    fallback_fields,
                    expanded_terms,
                ),
                (
                    "ALL_CHRG_TYPE_SET_NAME_SOURCE_EXACT",
                    lambda record: True,
                    fallback_fields,
                    [source_exact],
                ),
                (
                    "ALL_CHRG_TYPE_SET_NAME_EXPANDED",
                    lambda record: True,
                    fallback_fields,
                    expanded_terms,
                ),
            ]
            entity_matches: list[dict[str, object]] = []
            matched_stage = "NO_MATCH"
            matched_terms: list[str] = []
            for stage_name, charge_predicate, fields, stage_terms in stages:
                if not fields:
                    continue
                if stage_terms is None:
                    entity_matches = [
                        record
                        for record in records
                        if charge_predicate(record)
                        and matches_core_qualifiers(
                            record,
                            fields,
                            core_terms,
                            qualifiers,
                        )
                    ]
                    current_matched_terms = [*core_terms, *qualifiers]
                else:
                    if not stage_terms:
                        continue
                    entity_matches = [
                        record
                        for record in records
                        if charge_predicate(record)
                        and matches_name(record, fields, stage_terms)
                    ]
                    current_matched_terms = stage_terms
                if entity_matches:
                    matched_stage = stage_name
                    matched_terms = current_matched_terms
                    break
            all_matched.extend(entity_matches)
            entity_results.append(
                {
                    "entity_id": entity.get("entity_id"),
                    "source_exact": source_exact,
                    "stage": matched_stage,
                    "matched_terms": matched_terms,
                    "record_count": len(entity_matches),
                }
            )

        return all_matched, {
            "query_id": query_plan.get("query_id"),
            "supports_facts": query_plan.get("supports_facts"),
            "plan_origin": query_plan.get("plan_origin"),
            "supplement_reason": query_plan.get("supplement_reason"),
            "entity_results": entity_results,
            "search_terms": list(dict.fromkeys(term for term in all_terms if term)),
            "record_count": len(all_matched),
            "deduplicated": False,
            "fields_trimmed": False,
        }

    @staticmethod
    def _expense_plan_route(
        primary_domain: str,
        secondary_tag: str,
    ) -> SourceRoute:
        return SourceRoute(
            mapping_id=f"PLAN-{primary_domain}-{secondary_tag}",
            medins_id="运行时传入",
            mdtrt_id="运行时传入",
            primary_domain=primary_domain,
            secondary_tag=secondary_tag,
            source_level="PRIMARY",
            source_code="DS-S-002",
            source_name="费用明细",
            view_name="费用明细",
            document_kind="具体信息",
            record_name=None,
            record_type=None,
            mapping_status="PLANNED",
        )

    def _run_routes(
        self,
        routes: list[SourceRoute],
        primary_domain: str,
        secondary_tag: str,
        medins_id: str,
        mdtrt_id: str,
    ) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        for route in routes:
            evidence = self._execute_route(route, medins_id, mdtrt_id)
            expected_fields = EXPECTED_FIELDS.get(
                (primary_domain, secondary_tag, route.view_name),
                (),
            )
            missing_fields = []
            if evidence["status"] == "FOUND" and expected_fields:
                missing_fields = self._missing_expected_fields(
                    evidence["records"],
                    expected_fields,
                )
            attempts.append(
                {
                    "route": route,
                    "status": "EXPECTED_FIELD_MISSING" if missing_fields else evidence["status"],
                    "records": evidence["records"],
                }
            )
        return attempts

    def _retrieve_tag(
        self,
        proposition_id: str,
        tag: dict[str, Any],
        medins_id: str,
        mdtrt_id: str,
        secondary_mode: str,
        query_plan_index: dict[PlanKey, dict[str, Any]],
    ) -> dict[str, Any]:
        primary_domain = str(tag.get("一级证据域", "")).strip()
        secondary_tag = str(tag.get("二级证据标签", "")).strip()
        target_fact = str(tag.get("target_fact", "")).strip()
        plan_key = (proposition_id, primary_domain, secondary_tag, target_fact)
        route_groups = self.route_index.get(
            (primary_domain, secondary_tag),
            {"PRIMARY": [], "SECONDARY": []},
        )
        mapped_to_expense = any(
            route.view_name == "费用明细"
            for route in [*route_groups["PRIMARY"], *route_groups["SECONDARY"]]
        )
        has_exact_plan = plan_key in query_plan_index
        query_plan = (
            resolve_query_plan(query_plan_index, plan_key)
            if has_exact_plan or mapped_to_expense
            else None
        )
        primary_routes = list(route_groups["PRIMARY"])
        if query_plan is not None and not any(
            route.view_name == "费用明细" for route in primary_routes
        ):
            primary_routes.insert(
                0,
                self._expense_plan_route(primary_domain, secondary_tag),
            )

        primary_attempts = self._run_routes(
            primary_routes,
            primary_domain,
            secondary_tag,
            medins_id,
            mdtrt_id,
        )
        execute_secondary = secondary_mode == "always" or (
            secondary_mode == "auto" and self._needs_secondary(primary_attempts)
        )
        expense_scope_unresolved = query_plan is None and any(
            route.view_name == "费用明细"
            for route in [*primary_routes, *route_groups["SECONDARY"]]
        )
        if secondary_mode == "auto" and expense_scope_unresolved:
            execute_secondary = True
        if secondary_mode == "auto" and query_plan is not None:
            planned_primary_found = False
            for attempt in primary_attempts:
                if (
                    attempt["status"] != "FOUND"
                    or attempt["route"].view_name != "费用明细"
                ):
                    continue
                formatted, _ = self._format_structured_records(
                    attempt["route"],
                    primary_domain,
                    secondary_tag,
                    target_fact,
                    attempt["records"],
                    query_plan,
                )
                if formatted:
                    planned_primary_found = True
                    break
            execute_secondary = execute_secondary or not planned_primary_found
        secondary_attempts = []
        if execute_secondary:
            secondary_attempts = self._run_routes(
                route_groups["SECONDARY"],
                primary_domain,
                secondary_tag,
                medins_id,
                mdtrt_id,
            )

        def attempt_has_relevant_data(attempt: dict[str, Any]) -> bool:
            if attempt["status"] != "FOUND":
                return False
            route = attempt["route"]
            if route.view_name != "费用明细":
                return True
            formatted, _ = self._format_structured_records(
                route,
                primary_domain,
                secondary_tag,
                target_fact,
                attempt["records"],
                query_plan,
            )
            return bool(formatted)

        successful_primary = [
            attempt for attempt in primary_attempts if attempt_has_relevant_data(attempt)
        ]
        successful_secondary = [
            attempt for attempt in secondary_attempts if attempt_has_relevant_data(attempt)
        ]
        successful_attempts = successful_primary or successful_secondary
        all_attempts = primary_attempts + secondary_attempts

        source_attempts = successful_attempts or all_attempts
        source = "、".join(
            dict.fromkeys(
                self._source_label(attempt["route"])
                for attempt in source_attempts
            )
        ) or None

        data: list[dict[str, object]] = []
        matched_by: dict[str, Any] | None = None
        for attempt in successful_attempts:
            route = attempt["route"]
            if route.document_kind == "病历":
                data.extend(attempt["records"])
                continue
            formatted, current_match = self._format_structured_records(
                route,
                primary_domain,
                secondary_tag,
                target_fact,
                attempt["records"],
                query_plan,
            )
            data.extend(formatted)
            if current_match is not None:
                matched_by = current_match

        query_result: dict[str, Any] = {
            "status": (
                "RETRIEVED"
                if data
                else "QUERY_SCOPE_UNRESOLVED"
                if expense_scope_unresolved
                else "NOT_RETRIEVED"
            ),
            "source": source,
        }
        if expense_scope_unresolved:
            query_result["query_scope_status"] = "QUERY_SCOPE_UNRESOLVED"
            query_result["query_scope_reason"] = (
                "当前事实无专属计划，且同一申诉命题中没有可继承的争议实体；"
                "费用明细未执行默认全量返回"
            )
        if matched_by is not None:
            query_result["matched_by"] = matched_by
        query_result["data"] = data

        return {
            "一级证据域": primary_domain,
            "二级证据标签": secondary_tag,
            "target_fact": target_fact,
            "query_result": query_result,
        }

    def run(
        self,
        rule_evidence: list[dict[str, Any]],
        medins_id: str,
        mdtrt_id: str,
        rule_id: str | None = None,
        proposition_id: str | None = None,
        include_supplementary: bool = True,
        secondary_mode: str = "auto",
        query_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.query_cache.clear()
        query_plan_index = index_query_plans(query_plan)
        rule_results: list[dict[str, Any]] = []
        for rule in rule_evidence:
            current_rule_id = str(rule.get("rule_id", ""))
            if rule_id and current_rule_id != rule_id:
                continue

            proposition_results: list[dict[str, Any]] = []
            propositions = rule.get("result", {}).get("appeal_propositions", [])
            for proposition in propositions:
                current_proposition_id = str(proposition.get("proposition_id", ""))
                if proposition_id and current_proposition_id != proposition_id:
                    continue

                tags = list(proposition.get("required_tags", []))
                if include_supplementary:
                    tags.extend(proposition.get("supplementary_tags", []))
                proposition_results.append(
                    {
                        "proposition_id": current_proposition_id,
                        "evidence": [
                            self._retrieve_tag(
                                current_proposition_id,
                                tag,
                                medins_id,
                                mdtrt_id,
                                secondary_mode,
                                query_plan_index,
                            )
                            for tag in tags
                        ],
                    }
                )

            if proposition_results:
                rule_results.append(
                    {
                        "rule_id": current_rule_id,
                        "propositions": proposition_results,
                    }
                )

        if not rule_results:
            raise ValueError("没有找到符合筛选条件的规则或申诉命题")
        if len(rule_results) == 1:
            return rule_results[0]
        return {"rules": rule_results}
