from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .client import ModelClient
from .prompt import build_repair_prompt, build_user_prompt, load_system_prompt
from .schema import load_schema
from .validation import ReviewValidationError, validate_context, validate_result


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
        try:
            validate_result(result, context, review_round)
            return result
        except ReviewValidationError as validation_error:
            repaired_result = self.client.generate(
                system_prompt,
                build_repair_prompt(
                    context,
                    review_round,
                    result,
                    str(validation_error),
                ),
                self.schema,
            )
            self._capture_metrics()
            validate_result(repaired_result, context, review_round)
            return repaired_result

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
