from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .client import ModelClient
from .prompt import build_user_prompt, load_system_prompt
from .schema import load_schema
from .validation import (
    ExpressionParser,
    _find_location_values,
    _query_sources,
    _shared_structured_data_for_source,
    _source_matches,
    _structured_data,
    evaluate_expression,
    validate_context,
    validate_result,
)


class ReviewService:
    def __init__(self, client: ModelClient):
        self.client = client
        self.schema = load_schema()
        self.metrics = []

    @staticmethod
    def load_context(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("复核上下文必须是JSON对象")
        validate_context(payload)
        return payload

    def review(
        self,
        context: dict[str, Any],
        review_round: int = 1,
    ) -> dict[str, Any]:
        if review_round < 1:
            raise ValueError("review_round必须大于等于1")
        validate_context(context)
        self.metrics = []
        system_prompt = load_system_prompt()
        result = self.client.generate(
            system_prompt,
            build_user_prompt(context, review_round),
            self.schema,
        )
        result["mdtrt_id"] = context["mdtrt_id"]
        self._fill_medical_document_sources(result, context)
        result = self._remove_null_fields(result)
        self._normalize_fact_evidence(result)
        self._normalize_structured_evidence(result, context)
        self._capture_metrics()
        self._set_computed_overall_status(result)
        validate_result(result, context, review_round)
        return result

    @classmethod
    def _remove_null_fields(cls, value: Any) -> Any:
        """Omit empty JSON fields without changing non-null review content."""
        if isinstance(value, dict):
            return {
                key: cls._remove_null_fields(item)
                for key, item in value.items()
                if item is not None or key == "document_id"
            }
        if isinstance(value, list):
            return [cls._remove_null_fields(item) for item in value if item is not None]
        return value

    @staticmethod
    def _normalize_fact_evidence(result: dict[str, Any]) -> None:
        """Treat an omitted evidence list as empty; validation still enforces evidence by status."""
        fact_reviews = result.get("fact_reviews")
        if not isinstance(fact_reviews, list):
            return
        for fact_review in fact_reviews:
            if isinstance(fact_review, dict) and "evidence" not in fact_review:
                fact_review["evidence"] = []

    @staticmethod
    def _normalize_structured_evidence(
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        """Split combined structured quotes into real field values when safe."""
        fact_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for proposition in context.get("propositions", []):
            if not isinstance(proposition, dict):
                continue
            proposition_id = str(proposition.get("proposition_id", ""))
            for fact in proposition.get("evidence", []):
                if not isinstance(fact, dict):
                    continue
                key = (
                    proposition_id,
                    str(fact.get("一级证据域", "")),
                    str(fact.get("二级证据标签", "")),
                    str(fact.get("target_fact", "")),
                )
                fact_index[key] = fact

        structured_pool = context.get("structured_data_pool", {})
        summary_pool = context.get("expense_summary_pool", {})
        if not isinstance(structured_pool, dict):
            structured_pool = {}
        if not isinstance(summary_pool, dict):
            summary_pool = {}

        for fact_review in result.get("fact_reviews", []):
            if not isinstance(fact_review, dict):
                continue
            key = (
                str(fact_review.get("proposition_id", "")),
                str(fact_review.get("一级证据域", "")),
                str(fact_review.get("二级证据标签", "")),
                str(fact_review.get("target_fact", "")),
            )
            fact_context = fact_index.get(key)
            evidence_items = fact_review.get("evidence")
            if fact_context is None or not isinstance(evidence_items, list):
                continue
            query_result = fact_context.get("query_result", {})
            if not isinstance(query_result, dict):
                continue
            normalized_items: list[Any] = []
            structured_data = _structured_data(query_result, structured_pool, summary_pool)
            for evidence in evidence_items:
                if not isinstance(evidence, dict) or evidence.get("evidence_type") != "STRUCTURED":
                    normalized_items.append(evidence)
                    continue
                source = evidence.get("source")
                location = evidence.get("location")
                quote = evidence.get("quote")
                if not all(isinstance(value, str) and value.strip() for value in (source, location, quote)):
                    normalized_items.append(evidence)
                    continue
                values: list[Any] = []
                if _source_matches(source, _query_sources(query_result)):
                    values.extend(_find_location_values(structured_data, location))
                values.extend(
                    _find_location_values(
                        _shared_structured_data_for_source(source, structured_pool, summary_pool),
                        location,
                    )
                )
                value_set = {str(value).strip() for value in values if value is not None}
                if quote.strip() in value_set:
                    normalized_items.append(evidence)
                    continue
                parts = [
                    part.strip()
                    for part in re.split(r"[,，、;；|\n]+", quote)
                    if part.strip()
                ]
                if len(parts) > 1 and all(part in value_set for part in parts):
                    for part in parts:
                        split_evidence = dict(evidence)
                        split_evidence["quote"] = part
                        normalized_items.append(split_evidence)
                else:
                    normalized_items.append(evidence)
            fact_review["evidence"] = normalized_items

    @staticmethod
    def _fill_medical_document_sources(
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        """Derive medical evidence sources from their document IDs."""
        documents = {
            str(document.get("document_id")): document
            for document in context.get("medical_documents", [])
            if isinstance(document, dict) and document.get("document_id")
        }
        fact_reviews = result.get("fact_reviews")
        if not isinstance(fact_reviews, list):
            return
        for fact_review in fact_reviews:
            if not isinstance(fact_review, dict):
                continue
            evidence_items = fact_review.get("evidence")
            if not isinstance(evidence_items, list):
                continue
            for evidence in evidence_items:
                if not isinstance(evidence, dict):
                    continue
                if evidence.get("evidence_type") != "MEDICAL_DOCUMENT":
                    continue
                document = documents.get(str(evidence.get("document_id", "")))
                if document is not None and document.get("source"):
                    evidence["source"] = document["source"]

    @staticmethod
    def _set_computed_overall_status(result: dict[str, Any]) -> None:
        """Use deterministic program logic for the overall proposition result."""
        relation = result.get("proposition_relation")
        if not isinstance(relation, dict):
            return
        if relation.get("status") != "CONFIRMED":
            result["overall_logic_status"] = "UNKNOWN"
            return
        expression = relation.get("expression")
        reviews = result.get("proposition_reviews")
        if not isinstance(expression, str) or not isinstance(reviews, list):
            return
        proposition_statuses: dict[str, str] = {}
        for review in reviews:
            if not isinstance(review, dict):
                return
            proposition_id = review.get("proposition_id")
            status = review.get("status")
            if not isinstance(proposition_id, str) or not isinstance(status, str):
                return
            proposition_statuses[proposition_id] = status
        parsed_expression = ExpressionParser(expression).parse()
        result["overall_logic_status"] = evaluate_expression(
            parsed_expression,
            proposition_statuses,
        )

    def _capture_metrics(self) -> None:
        metrics = getattr(self.client, "last_metrics", None)
        if metrics is not None:
            self.metrics.append(metrics)

    @staticmethod
    def write_result(path: Path, result: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        result = ReviewService._remove_null_fields(result)
        temporary_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
