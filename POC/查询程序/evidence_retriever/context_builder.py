from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


TagKey = tuple[str, str, str]


class MedicalDocumentCollector:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.document_ids: dict[str, str] = {}
        self.source_counts: defaultdict[tuple[str, str], int] = defaultdict(int)

    def add(self, source: str, record: dict[str, Any]) -> str:
        identity = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
        )
        existing_id = self.document_ids.get(identity)
        if existing_id is not None:
            return existing_id

        document_source = _select_record_source(source, record)
        source_code = document_source.split(maxsplit=1)[0] if document_source else "UNKNOWN"
        mdtrt_id = str(record.get("mdtrtId", "UNKNOWN"))
        counter_key = (source_code, mdtrt_id)
        self.source_counts[counter_key] += 1
        document_id = (
            f"DOC-{source_code}-{mdtrt_id}-{self.source_counts[counter_key]:02d}"
        )
        document = {
            "document_id": document_id,
            "source": document_source,
            "recordName": record.get("recordName"),
            "recordType": record.get("recordType"),
            "updateTime": record.get("updateTime"),
            "recordContent": record.get("recordContent"),
        }
        self.document_ids[identity] = document_id
        self.documents.append(document)
        return document_id


class StructuredDataCollector:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.record_ids: dict[str, str] = {}

    def add(self, source: str, record: dict[str, Any]) -> str:
        identity = json.dumps(record, ensure_ascii=False, sort_keys=True)
        existing_id = self.record_ids.get(identity)
        if existing_id is not None:
            return existing_id

        source_code = source.strip().split(maxsplit=1)[0] if source.strip() else "UNKNOWN"
        mdtrt_id = str(record.get("mdtrtId", record.get("mdtrt_id", "UNKNOWN")))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        record_id = f"SD-{source_code}-{mdtrt_id}-{digest}"
        self.record_ids[identity] = record_id
        self.records[record_id] = record
        return record_id


def _select_record_source(source: str, record: dict[str, Any]) -> str:
    candidates = [part.strip() for part in source.split("、") if part.strip()]
    if len(candidates) <= 1:
        return candidates[0] if candidates else source

    record_name = str(record.get("recordName", "")).strip()
    for candidate in candidates:
        parts = candidate.split(maxsplit=1)
        source_name = parts[1] if len(parts) == 2 else parts[0]
        source_names = [name.strip() for name in source_name.split("或") if name.strip()]
        if any(
            name == record_name or name in record_name or record_name in name
            for name in source_names
        ):
            return candidate
    return source


def _tag_key(tag: dict[str, Any]) -> TagKey:
    return (
        str(tag.get("一级证据域", "")).strip(),
        str(tag.get("二级证据标签", "")).strip(),
        str(tag.get("target_fact", "")).strip(),
    )


def _tag_metadata(proposition: dict[str, Any]) -> dict[TagKey, dict[str, Any]]:
    metadata: dict[TagKey, dict[str, Any]] = {}
    for group_name, default_necessity in (
        ("required_tags", "REQUIRED"),
        ("supplementary_tags", "SUPPLEMENTARY"),
    ):
        for tag in proposition.get(group_name, []):
            metadata[_tag_key(tag)] = {
                "necessity": tag.get("necessity") or default_necessity,
            }
    return metadata


def _source_is_medical_document(source: str | None) -> bool:
    if not source:
        return False
    source_codes = [part.strip().split(maxsplit=1)[0] for part in source.split("、")]
    return bool(source_codes) and all(code.startswith("DS-M-") for code in source_codes)


def _compact_query_result(
    query_result: dict[str, Any],
    document_collector: MedicalDocumentCollector,
    structured_collector: StructuredDataCollector,
) -> dict[str, Any]:
    source = query_result.get("source")
    compact: dict[str, Any] = {
        "status": query_result.get("status"),
        "source": source,
    }
    if query_result.get("query_scope_status"):
        compact["query_scope_status"] = query_result["query_scope_status"]
    if query_result.get("query_scope_reason"):
        compact["query_scope_reason"] = query_result["query_scope_reason"]
    records = query_result.get("data", [])
    document_records = [
        record
        for record in records
        if isinstance(record, dict) and "recordContent" in record
    ]
    structured_records = [record for record in records if record not in document_records]

    if document_records:
        compact["document_refs"] = [
            document_collector.add(str(source or ""), record)
            for record in document_records
        ]
    if structured_records:
        compact["structured_data_refs"] = list(
            dict.fromkeys(
                structured_collector.add(str(source or ""), record)
                for record in structured_records
            )
        )
    if not records:
        if _source_is_medical_document(source):
            compact["document_refs"] = []
        else:
            compact["structured_data_refs"] = []
    return compact


def _build_rule_context(
    source_rule: dict[str, Any],
    query_rule: dict[str, Any],
) -> dict[str, Any]:
    source_result = source_rule.get("result", {})
    source_propositions = {
        str(proposition.get("proposition_id", "")): proposition
        for proposition in source_result.get("appeal_propositions", [])
    }
    document_collector = MedicalDocumentCollector()
    structured_collector = StructuredDataCollector()
    propositions: list[dict[str, Any]] = []

    for query_proposition in query_rule.get("propositions", []):
        proposition_id = str(query_proposition.get("proposition_id", ""))
        source_proposition = source_propositions.get(proposition_id)
        if source_proposition is None:
            raise ValueError(f"规则证据中不存在申诉命题：{proposition_id}")
        metadata = _tag_metadata(source_proposition)
        evidence: list[dict[str, Any]] = []
        for query_evidence in query_proposition.get("evidence", []):
            key = _tag_key(query_evidence)
            if key not in metadata:
                raise ValueError(
                    "规则证据中不存在查询结果对应的标签："
                    f"{key[0]}/{key[1]}/{key[2]}"
                )
            evidence.append(
                {
                    "一级证据域": key[0],
                    "二级证据标签": key[1],
                    "target_fact": key[2],
                    "necessity": metadata[key]["necessity"],
                    "query_result": _compact_query_result(
                        query_evidence.get("query_result", {}),
                        document_collector,
                        structured_collector,
                    ),
                }
            )
        propositions.append(
            {
                "proposition_id": proposition_id,
                "statement": source_proposition.get("statement"),
                "logic_notes": source_proposition.get("logic_notes"),
                "missing_required_facts": source_proposition.get(
                    "missing_required_facts", []
                ),
                "evidence": evidence,
            }
        )

    source = source_result.get("source", {})
    regulatory_claim = source_result.get("regulatory_claim", {})
    return {
        "rule_id": query_rule.get("rule_id"),
        "review_basis": {
            "violation_item": source.get("violation_item"),
            "violation_description": source.get("violation_description"),
            "regulatory_claim": regulatory_claim.get("statement"),
            "appeal_objective": source_result.get("appeal_objective"),
        },
        "propositions": propositions,
        "structured_data_pool": structured_collector.records,
        "medical_documents": document_collector.documents,
        "program_checks": source_result.get("program_checks", []),
    }


def build_review_context(
    rule_evidence: list[dict[str, Any]],
    query_result: dict[str, Any],
) -> dict[str, Any]:
    source_rules = {
        str(rule.get("rule_id", "")): rule
        for rule in rule_evidence
    }
    query_rules = query_result.get("rules", [query_result])
    contexts: list[dict[str, Any]] = []
    for query_rule in query_rules:
        rule_id = str(query_rule.get("rule_id", ""))
        source_rule = source_rules.get(rule_id)
        if source_rule is None:
            raise ValueError(f"规则证据中不存在规则：{rule_id}")
        contexts.append(_build_rule_context(source_rule, query_rule))

    if len(contexts) == 1:
        return contexts[0]
    return {"rules": contexts}
