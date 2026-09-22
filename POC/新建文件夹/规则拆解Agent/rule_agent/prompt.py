from __future__ import annotations

import json
from pathlib import Path

from .excel_reader import RuleRecord


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system_prompt.txt"


def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"系统提示词文件不存在: {PROMPT_PATH}")
    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("系统提示词不能为空")
    return prompt


def build_user_prompt(record: RuleRecord) -> str:
    """只传递规则原文；拆解方法和输出格式全部由系统提示词决定。"""
    payload = {
        "source_rule": {
            "rule_id": record.rule_id,
            "source_rule_name": record.source_rule_name,
            "item": {"code": record.item_code, "name": record.item_name},
            "rule_text": record.rule_text,
        }
    }
    return json.dumps(payload, ensure_ascii=False)
