from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .client import OpenAICompatibleClient
from .config import Settings, load_env_file
from .excel_reader import read_published_rules
from .service import RuleDecompositionService


AGENT_ROOT = Path(__file__).resolve().parents[1]
POC_ROOT = AGENT_ROOT.parent
DEFAULT_INPUT = POC_ROOT / "负面清单" / "负面清单.xlsx"
DEFAULT_OUTPUT_DIR = POC_ROOT / "规则拆解结果"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将医保负面清单逐条拆解为事实与逻辑 JSON")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="负面清单 Excel 路径")
    parser.add_argument("--output-dir", "--output", dest="output_dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="规则拆解结果目录")
    parser.add_argument("--env-file", type=Path, help="可选的 .env 配置文件")
    parser.add_argument("--limit", type=int, help="只处理前 N 条已发布规则")
    parser.add_argument(
        "--write-mode",
        choices=("append",),
        default="append",
        help="结果写入方式；当前固定为 append，只追加尚未存在的 rule_id",
    )
    thinking_group = parser.add_mutually_exclusive_group()
    thinking_group.add_argument("--thinking", dest="thinking_override", action="store_const", const=True, help="本次运行开启模型思考模式")
    thinking_group.add_argument("--no-thinking", dest="thinking_override", action="store_const", const=False, help="本次运行关闭模型思考模式")
    parser.set_defaults(thinking_override=None)
    parser.add_argument("--dry-run", action="store_true", help="只校验和读取 Excel，不调用模型")
    return parser


def print_decomposition_summary(timings: tuple, output_dir: Path) -> None:
    print("模型输出摘要：")
    aggregate = json.loads((output_dir / "规则拆解结果.json").read_text(encoding="utf-8"))
    by_rule_id = {entry["rule_id"]: entry["result"] for entry in aggregate}
    for timing in timings:
        result = by_rule_id[timing.rule_id]
        print(f"[{timing.rule_id}]")
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        records = read_published_rules(args.input.resolve())
        if args.limit is not None:
            if args.limit <= 0:
                raise ValueError("--limit 必须大于 0")
            records = records[: args.limit]
        if args.dry_run:
            print(f"Excel 校验通过，共读取 {len(records)} 条规则：")
            for record in records:
                print(f"- {record.rule_id} | {record.source_rule_name} | 源版本 {record.source_version}")
            return 0

        if args.env_file:
            load_env_file(args.env_file.resolve())
        settings = Settings.from_env()
        if args.thinking_override is not None:
            settings = replace(settings, enable_thinking=args.thinking_override)
            settings.validate()
        client = OpenAICompatibleClient(settings)
        service = RuleDecompositionService(client)
        output_dir = args.output_dir.resolve()
        mode_label = "开启" if settings.enable_thinking is True else "关闭" if settings.enable_thinking is False else "模型默认"
        print(f"思考模式：{mode_label}")
        print("写入模式：单文件追加（已有 rule_id 跳过，不覆盖）")
        summary = service.run(records, output_dir)
        print(
            f"规则拆解完成：成功处理 {summary.total} 条"
            f"（新增 {summary.created}，更新 {summary.updated}，跳过 {summary.skipped}）"
        )
        for timing in summary.timings:
            print(
                f"[{timing.output_filename}] 拆解耗时：{timing.elapsed_seconds:.2f} 秒"
            )
        print(f"本次总耗时：{summary.elapsed_seconds:.2f} 秒")
        print(f"模型：{settings.model_name}（{settings.model_param_scale}）")
        print(f"输出目录：{output_dir}")
        if settings.enable_thinking is True:
            print_decomposition_summary(summary.timings, output_dir)
        return 0
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
