from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .client import ModelClient
from .excel_reader import RuleRecord
from .prompt import build_user_prompt, load_system_prompt
from .schema import load_schema


AGGREGATE_FILENAME = "规则拆解结果.json"
SEPARATOR = "-----------------"


@dataclass(frozen=True)
class RuleTiming:
    rule_id: str
    output_filename: str
    elapsed_seconds: float


@dataclass(frozen=True)
class RunSummary:
    created: int
    updated: int
    skipped: int
    timings: tuple[RuleTiming, ...]
    elapsed_seconds: float

    @property
    def total(self) -> int:
        return self.created + self.updated


class RuleDecompositionService:
    def __init__(self, client: ModelClient):
        self.client = client
        self.schema = load_schema()

    def decompose(self, record: RuleRecord) -> dict:
        """调用模型一次并原样返回 JSON；拆解行为完全由系统提示词控制。"""
        return self.client.generate(
            load_system_prompt(),
            build_user_prompt(record),
            self.schema,
        )

    @staticmethod
    def _load_entries(path: Path) -> list[dict]:
        if not path.exists():
            return []
        raw_text = path.read_text(encoding="utf-8")
        if not raw_text.strip():
            return []
        payload = json.loads(raw_text)
        if not isinstance(payload, list):
            raise ValueError(f"汇总结果必须是 JSON 数组: {path}")
        for index, entry in enumerate(payload, start=1):
            if not isinstance(entry, dict):
                raise ValueError(f"汇总结果第 {index} 项必须是 JSON 对象")
            if not isinstance(entry.get("rule_id"), str) or not isinstance(entry.get("result"), dict):
                raise ValueError(f"汇总结果第 {index} 项必须包含 rule_id 和 result 对象")
        return payload

    @staticmethod
    def _write_entries(path: Path, entries: list[dict]) -> None:
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

    def run(
        self,
        records: Iterable[RuleRecord],
        output_dir: Path,
        *,
        overwrite_existing: bool = False,
    ) -> RunSummary:
        """只追加新 rule_id；overwrite_existing 为旧调用兼容参数，不再触发覆盖。"""
        del overwrite_existing
        run_started_at = time.perf_counter()
        output_dir.mkdir(parents=True, exist_ok=True)
        aggregate_path = output_dir / AGGREGATE_FILENAME
        entries = self._load_entries(aggregate_path)
        existing_ids = {entry["rule_id"] for entry in entries}
        created = 0
        skipped = 0
        timings: list[RuleTiming] = []

        for record in records:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", record.rule_id):
                raise ValueError(f"规则编号不能安全写入结果: {record.rule_id}")
            if record.rule_id in existing_ids:
                skipped += 1
                continue

            rule_started_at = time.perf_counter()
            decomposition = self.decompose(record)
            entries.append(
                {
                    "_comment": f"{SEPARATOR} {record.rule_id} {SEPARATOR}",
                    "rule_id": record.rule_id,
                    "result": decomposition,
                }
            )
            self._write_entries(aggregate_path, entries)
            existing_ids.add(record.rule_id)
            created += 1
            timings.append(
                RuleTiming(
                    rule_id=record.rule_id,
                    output_filename=AGGREGATE_FILENAME,
                    elapsed_seconds=time.perf_counter() - rule_started_at,
                )
            )

        return RunSummary(
            created=created,
            updated=0,
            skipped=skipped,
            timings=tuple(timings),
            elapsed_seconds=time.perf_counter() - run_started_at,
        )
