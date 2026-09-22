from __future__ import annotations

import json
from pathlib import Path

from .catalog import EvidenceTag
from .excel_reader import RuleRecord


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system_prompt_分阶段保留路径版.txt"


def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"系统提示词文件不存在: {PROMPT_PATH}")
    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("系统提示词不能为空")
    return prompt


def build_user_prompt(record: RuleRecord, tags: list[EvidenceTag]) -> str:
    grouped_catalog: dict[str, list[str]] = {}
    for tag in tags:
        grouped_catalog.setdefault(tag.primary_domain, []).append(tag.secondary_tag)

    payload = {
        "source_rule": {
            "rule_id": record.rule_id,
            "source_rule_name": record.source_rule_name,
            "case_parameters": {
                "mdtrt_id": record.mdtrt_id,
                "medins_id": record.medins_id,
                "visit_no": record.visit_no,
            },
            "item": {"code": record.item_code, "name": record.item_name},
            "rule_text": record.rule_text,
        },
        "tag_catalog": [tag.to_prompt_dict() for tag in tags],
        "tag_catalog_by_domain": grouped_catalog,
    }
    return json.dumps(payload, ensure_ascii=False)


def build_repair_prompt(
    record: RuleRecord,
    tags: list[EvidenceTag],
    validation_error: str,
) -> str:
    payload = json.loads(build_user_prompt(record, tags))
    payload["validation_feedback"] = {
        "error": validation_error,
        "instruction": (
            "上一次输出未通过标签目录校验。请只修正非法标签组合，重新输出完整合法JSON。"
            "一级证据域和二级证据标签必须来自tag_catalog中的同一条完整记录，禁止跨记录拼接。"
            "不要新增字段，不要改变既有JSON结构。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)
