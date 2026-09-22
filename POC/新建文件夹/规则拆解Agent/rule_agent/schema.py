from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "rule_decomposition.schema.json"


def load_schema() -> dict[str, Any]:
    """加载通用 JSON 对象约束；具体字段和拆解规则由系统提示词定义。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
