from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system_prompt.txt"


def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"系统提示词文件不存在: {PROMPT_PATH}")
    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("系统提示词不能为空")
    return prompt


def build_user_prompt(context: dict[str, Any], review_round: int) -> str:
    logic_basis_quote_sources = []
    fact_source_constraints = []
    for field, value in context.get("review_basis", {}).items():
        if isinstance(value, str) and value.strip():
            logic_basis_quote_sources.append(
                {"location": f"review_basis.{field}", "text": value}
            )
    for proposition in context.get("propositions", []):
        proposition_id = proposition.get("proposition_id", "")
        for field in ("statement", "logic_notes"):
            value = proposition.get(field)
            if isinstance(value, str) and value.strip():
                logic_basis_quote_sources.append(
                    {
                        "location": f"propositions.{proposition_id}.{field}",
                        "text": value,
                    }
                )
        for fact in proposition.get("evidence", []):
            query_result = fact.get("query_result", {})
            if not isinstance(query_result, dict):
                continue
            source = query_result.get("source")
            if source is None:
                source = query_result.get("sources", query_result.get("source_list"))
            has_documents = bool(query_result.get("document_refs"))
            if not has_documents:
                records = query_result.get("data", query_result.get("records", []))
                if isinstance(records, list):
                    has_documents = any(
                        isinstance(record, dict) and "recordContent" in record
                        for record in records
                    )
            fact_source_constraints.append(
                {
                    "proposition_id": proposition_id,
                    "一级证据域": fact.get("一级证据域"),
                    "二级证据标签": fact.get("二级证据标签"),
                    "target_fact": fact.get("target_fact"),
                    "available_sources": source,
                    "allowed_evidence_type": (
                        "MEDICAL_DOCUMENT" if has_documents else "STRUCTURED"
                    ),
                }
            )
    payload = {
        "review_round": review_round,
        "review_context": context,
        "logic_basis_quote_sources": logic_basis_quote_sources,
        "fact_source_constraints": fact_source_constraints,
    }
    return (
        "请严格依据系统提示词复核以下输入，并且只输出合法JSON对象：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def build_repair_prompt(
    context: dict[str, Any],
    review_round: int,
    previous_result: dict[str, Any],
    validation_error: str,
) -> str:
    return (
        build_user_prompt(context, review_round)
        + "\n\n上一次输出未通过程序校验。请根据以下反馈修正后，重新输出完整合法JSON对象。"
        + "不要解释错误，不要新增字段，不要改变输出结构。"
        + "basis_quotes只能从logic_basis_quote_sources某一项的text中逐字复制连续原文；"
        + "不得把statement与logic_notes拼接成新句子，也不得概括或添加连接语。\n"
        + "病历证据quote默认逐字复制同一字段的连续原文；原文过长时可以用...或……省略中间内容，"
        + "但省略号两侧的片段必须都来自同一字段且顺序一致，不得改写或猜测。\n"
        + "同一事实可以有多个主要或次要来源；如果该事实query_result.source（或sources/source_list）包含多个来源，"
        + "可以引用其中任意一个已经返回的完整来源项目，但不得改写来源名称。\n"
        + "每条证据只能使用同一proposition_id、一级证据域、二级证据标签和target_fact对应的来源；"
        + "不得把另一个标签的来源当作当前事实的来源。\n"
        + json.dumps(
            {
                "validation_error": validation_error,
                "previous_result": previous_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
