from __future__ import annotations

import json
from typing import Any


SEARCH_FIELDS = ["hilist_name", "hosplist_name"]
FALLBACK_SEARCH_FIELDS = ["set_name"]

SYSTEM_PROMPT = """你是医保查询关键词规划助手。程序已经决定哪些事实需要查询费用明细。你只负责针对每个target_fact独立提取若干检索关键词，并分别进行名称规范化和同义扩展。

要求：
1. 必须为输入中的每个fact_id输出且只输出一项，不得合并不同事实。
2. 每个事实输出1至6个keyword_groups。不同keyword_groups之间是OR关系；同一组required_terms中的词必须同时命中，是AND关系。
3. source_phrase优先使用该事实的target_fact、violation_item或violation_description中的连续原文；为了名称检索，也可以使用与当前事实直接相关的规范名称或常见同义表达，但不能从其他事实借用或扩展成无关项目。
4. 优先提取可出现在收费项目名称中的具体对象、操作、治疗、药品、耗材或文书名称。不要输出“获取”“确认”“是否”“相关”“本次”等动作词或空泛词。
5. required_terms用于处理词序、括号和限定词差异。必须保留部位、大小、侧别等会改变项目含义的限定信息。例如“大关节松动训练”输出["关节松动训练", "大关节"]。
6. alternative_terms只放有把握的常见名称、简称或词序变体；没有可靠扩展时返回空数组。不得虚构具体药名、手术名或收费编码。
7. violation_item和violation_description只作为当前事实的辅助锚点。只有与该target_fact直接相关时才可形成关键词组，不能让所有事实都只查询监管原文。
8. 不推荐收费类别，不判断合规性，不选择来源、视图或返回字段。
9. 只输出JSON对象，不输出解释、Markdown或代码围栏。

输出结构：
{
  "rule_id": "...",
  "facts": [
    {
      "fact_id": "F01",
      "keyword_groups": [
        {
          "source_phrase": "原文关键词或与当前事实直接相关的规范名称",
          "required_terms": ["必要词1", "必要词2"],
          "alternative_terms": ["常见变体"]
        }
      ]
    }
  ]
}"""


def build_user_prompt(
    rule_id: str,
    violation_item: str,
    violation_description: str,
    facts: list[dict[str, Any]],
) -> str:
    payload = {
        "rule_id": rule_id,
        "violation_item": violation_item,
        "violation_description": violation_description,
        "facts": facts,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_repair_prompt(
    rule_id: str,
    violation_item: str,
    violation_description: str,
    facts: list[dict[str, Any]],
    previous_result: dict[str, Any],
    validation_error: str,
) -> str:
    return (
        build_user_prompt(
            rule_id,
            violation_item,
            violation_description,
            facts,
        )
        + "\n\n上一次输出未通过程序校验。请根据校验错误重新输出完整JSON对象。"
        + "source_phrase可以是原文关键词或与当前事实直接相关的规范名称，但不得引入无关项目。"
        + "不要解释，不要输出Markdown。\n"
        + json.dumps(
            {
                "validation_error": validation_error,
                "previous_result": previous_result,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
