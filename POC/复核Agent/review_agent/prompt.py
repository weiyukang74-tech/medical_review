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
        "请严格依据系统提示词复核以下输入，并且只输出合法JSON对象。\n"
        "本次复核必须以review_context.review_basis.violation_description中的原始规则为准，"
        "不得让后续proposition或target_fact新增原规则没有要求的‘诊断名称必须直接填写’条件。"
        "病原学明确阳性并存在相符症状时，应综合支持对应临床疾病；诊断字段缺名仅表示记录不完整。"
        "轻症等严重程度应结合生命体征、心肺检查、并发症、治疗场所和患者去向综合判断，"
        "不得仅因缺少同名文字而判NOT_SUPPORTED。\n"
        "除STRUCTURED证据的document_id外，不要输出null；其他可选字段没有值时直接省略。每个fact_reviews项都必须输出evidence数组，UNKNOWN且无证据时输出空数组。STRUCTURED证据必须填写真实查询结果source，每条证据只引用一个真实字段值，不要把多个值拼成一条quote；MEDICAL_DOCUMENT证据必须填写真实非空document_id且省略source。每个proposition_reviews项必须保留reason。\n"
        "以下为待复核输入：\n"
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
        + "也可以在开头或结尾使用省略号表示省略前文或后文；所有非省略片段必须来自同一字段且逐字存在。"
        + "空格、换行或全角形式的差异可以忽略，文字、数字和符号不得改写；优先保持原文顺序，无法保持顺序时也不得改写或猜测。\n"
        + "MEDICAL_DOCUMENT证据只填写真实document_id、location和quote，source由程序根据document_id补齐；STRUCTURED证据必须填写当前真实查询结果中的source，每条证据只引用一个真实字段值，不要把多个值拼成一条quote；"
        + "同一事实可以引用当前query_result或本案共享structured_data_pool、expense_summary_pool中的真实结构化证据；"
        + "跨事实引用共享记录时，source必须与记录ID中的数据源编码一致，location和quote必须真实存在。\n"
        + "证据优先使用当前事实的查询来源；也可以使用本案共享证据池中能够直接支持当前临床事实的真实记录，"
        + "但必须保持来源、字段和值可验证，不得仅凭标签名称推测或借用无关证据。\n"
        + "完成各事实判断后，必须综合同一命题内全部已返回证据建立支持链。"
        + "SUPPLEMENTARY表示取证必要性，不代表证据证明力较弱；病原学阳性等直接结果不得因标签为SUPPLEMENTARY而忽略。"
        + "诊断名称缺失不等于疾病不存在，典型症状列表也不得在无明确限定时解释为全部必须满足。\n"
        + "原始违规描述的规则语义高于后续生成的proposition和target_fact；后两者不得新增原规则没有要求的"
        + "‘诊断名称必须直接填写’条件。病原学阳性可直接支持相应感染，若仅分型或严重程度缺失，应判UNKNOWN而非否定疾病。\n"
        + "除STRUCTURED证据的document_id外，不要输出null；其他可选字段没有值时直接省略。每个fact_reviews项都必须输出evidence数组，UNKNOWN且无证据时输出空数组。STRUCTURED证据必须填写source，每条证据只引用一个真实字段值，不要把多个值拼成一条quote；MEDICAL_DOCUMENT证据必须填写真实非空document_id且省略source。每个proposition_reviews项必须保留reason。\n"
        + json.dumps(
            {
                "validation_error": validation_error,
                "previous_result": previous_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
