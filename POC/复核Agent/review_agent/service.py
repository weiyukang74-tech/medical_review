from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .client import ModelClient
from .prompt import build_user_prompt, load_system_prompt
from .schema import load_schema
from .validation import (
    ExpressionParser,
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
        self._capture_metrics()
        self._set_computed_overall_status(result)
        validate_result(result, context, review_round)
        return result

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
        temporary_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
