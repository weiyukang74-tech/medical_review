from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .config import Settings


class ModelClient(Protocol):
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GenerationMetrics:
    elapsed_seconds: float
    first_token_seconds: float | None
    output_tokens: int | None
    tokens_per_second: float | None
    tokens_estimated: bool


class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.metrics_history: list[GenerationMetrics] = []
        self.last_metrics: GenerationMetrics | None = None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.settings.model_name,
            "temperature": 0,
            "enable_thinking": False,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.settings.response_format == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "review_result",
                    "strict": True,
                    "schema": schema,
                },
            }
        elif self.settings.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            url=f"{self.settings.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            started_at = time.perf_counter()
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    content, output_tokens, first_token_at, streamed = (
                        self._read_response(response, started_at)
                    )
                elapsed_seconds = time.perf_counter() - started_at
                has_usage_tokens = output_tokens is not None
                if output_tokens is None:
                    output_tokens = self._estimate_tokens(content)
                first_token_seconds = (
                    first_token_at - started_at
                    if first_token_at is not None
                    else None
                )
                tokens_per_second = (
                    output_tokens / elapsed_seconds
                    if output_tokens is not None and elapsed_seconds > 0
                    else None
                )
                metrics = GenerationMetrics(
                    elapsed_seconds=elapsed_seconds,
                    first_token_seconds=first_token_seconds,
                    output_tokens=output_tokens,
                    tokens_per_second=tokens_per_second,
                    tokens_estimated=not has_usage_tokens,
                )
                self.last_metrics = metrics
                self.metrics_history.append(metrics)
                return self._parse_json_content(content)
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                KeyError,
                IndexError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt >= self.settings.max_retries:
                    break
                time.sleep(min(2**attempt, 4))
        raise RuntimeError(
            f"模型调用失败，已尝试 {self.settings.max_retries + 1} 次: {last_error}"
        ) from last_error

    @classmethod
    def _read_response(
        cls,
        response: Any,
        started_at: float,
    ) -> tuple[str, int | None, float | None, bool]:
        first_line = response.readline()
        while first_line and not first_line.strip():
            first_line = response.readline()
        stripped_first_line = first_line.decode("utf-8", errors="replace").lstrip()
        if not stripped_first_line.startswith("data:"):
            raw_payload = first_line + response.read()
            payload = json.loads(raw_payload.decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            return content, usage.get("completion_tokens"), time.perf_counter(), False

        content_parts: list[str] = []
        completion_tokens: int | None = None
        first_token_at: float | None = None
        pending_line = first_line
        while pending_line:
            line = pending_line.decode("utf-8", errors="replace").strip()
            if line.startswith("data:"):
                event_text = line[5:].strip()
                if event_text and event_text != "[DONE]":
                    event = json.loads(event_text)
                    usage = event.get("usage") or {}
                    if usage.get("completion_tokens") is not None:
                        completion_tokens = int(usage["completion_tokens"])
                    choices = event.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece is None:
                            piece = choices[0].get("text")
                        if isinstance(piece, str) and piece:
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            content_parts.append(piece)
            if line == "data: [DONE]":
                break
            pending_line = response.readline()
        return "".join(content_parts), completion_tokens, first_token_at, True

    @staticmethod
    def _estimate_tokens(content: str) -> int:
        return max(1, round(len(content) / 2))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    @staticmethod
    def _parse_json_content(content: Any) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("模型返回的 message.content 不是字符串")
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("模型输出必须是 JSON 对象")
        return parsed
