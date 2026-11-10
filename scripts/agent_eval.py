#!/usr/bin/env python
"""跑评测场景：replay 离线、record 与 live 接真实模型（M68 §4.2.8）。

用法见 ``--help``。replay 不需要网络与密钥；record/live 需要 ``MAA_EVAL_API_KEY`` 与
``--base-url``/``--model``（或同名环境变量）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maa_api.agent.evals.runner import (  # noqa: E402
    EvalOptions,
    EvalRunner,
    LlmEndpoint,
    write_result,
)
from maa_api.agent.evals.scenario import Scenario, load_scenarios  # noqa: E402

DEFAULT_OUT = Path("resource/evals")
DEFAULT_SCENARIOS = Path("tests/evals/scenarios")
DEFAULT_CASSETTES = Path("tests/evals/cassettes")
DEFAULT_FIXTURES = Path("tests/evals/fixtures")


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器；单独导出便于测试。"""
    parser = argparse.ArgumentParser(
        prog="agent_eval", description="跑代理评测场景（默认 regression replay）"
    )
    parser.add_argument("--suite", choices=("regression", "capability", "all"), default="regression")
    parser.add_argument("--mode", choices=("replay", "record", "live"), default="replay")
    parser.add_argument("--scenario", action="append", default=[], metavar="ID")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--tag", action="append", default=[], metavar="TAG")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--min-pass-rate", type=float, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scenarios-dir", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--cassettes-dir", type=Path, default=DEFAULT_CASSETTES)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--base-url", default=os.environ.get("MAA_EVAL_BASE_URL"))
    parser.add_argument("--model", default=os.environ.get("MAA_EVAL_MODEL"))
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="把结果写入应用库，供能力效果看板读取；失败只警告，不影响退出码",
    )
    parser.add_argument("--list", action="store_true", dest="list_only")
    return parser


def _resolve_llm(args: argparse.Namespace) -> LlmEndpoint | None:
    """record/live 需要真实端点；缺配置返回 None。"""
    key = os.environ.get("MAA_EVAL_API_KEY", "")
    if not args.base_url or not args.model or not key:
        return None
    return LlmEndpoint(base_url=args.base_url, api_key=key, model=args.model)


def _print_case(result: Any) -> None:
    label = {"passed": "通过", "failed": "失败", "error": "出错", "skipped": "跳过"}[
        result.status
    ]
    print(
        f"{label} {result.scenario}#{result.attempt}"
        f"  {result.duration_ms}ms  模型调用 {result.model_calls}"
    )
    if result.status != "passed":
        detail = result.error or next(
            (item.details[0] for item in result.graders if item.details), ""
        )
        if detail:
            print(f"  {detail}")
    elif any(item.status == "skipped" for item in result.graders):
        skipped = [item for item in result.graders if item.status == "skipped"]
        if skipped and skipped[0].details:
            print(f"  跳过：{skipped[0].details[0]}")


def main(argv: list[str] | None = None) -> int:
    """脚本入口；返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    scenarios_dir = (REPO_ROOT / args.scenarios_dir).resolve()
    cassettes_dir = (REPO_ROOT / args.cassettes_dir).resolve()
    fixtures_dir = (REPO_ROOT / args.fixtures_dir).resolve()
    out_dir = (REPO_ROOT / args.out).resolve()

    suites: tuple[str, ...] | None
    if args.suite == "all":
        suites = ("regression", "capability")
    else:
        suites = (args.suite,)
    try:
        scenarios = load_scenarios(
            scenarios_dir,
            suites=suites,
            ids=args.scenario,
            agent=args.agent,
            tags=args.tag,
        )
    except Exception as exc:  # noqa: BLE001 - 场景错误直接报告
        print(f"场景加载失败：{exc}")
        return 2
    if not scenarios:
        print("没有匹配的场景")
        return 2

    if args.list_only:
        for scenario in scenarios:
            print(f"{scenario.id}  {scenario.suite}  {scenario.title}")
        return 0

    llm = _resolve_llm(args)
    if args.mode in ("record", "live"):
        if llm is None:
            print(
                "record/live 需要 --base-url、--model 与 MAA_EVAL_API_KEY"
                "（或 MAA_EVAL_BASE_URL / MAA_EVAL_MODEL）"
            )
            return 3

    from tests.evals.environment import build_default_environment

    environment = build_default_environment(REPO_ROOT)
    failed = False
    for suite in suites or ("regression",):
        selected = [scenario for scenario in scenarios if scenario.suite == suite]
        if not selected:
            continue
        options = EvalOptions(
            mode=args.mode,
            cassette_dir=cassettes_dir,
            out_dir=out_dir,
            real_llm=llm,
            judge_model=args.judge_model or (llm.model if llm else None),
            repeat=max(1, args.repeat),
            strict=bool(args.strict),
            keep_temp=bool(args.keep_temp),
        )
        runner = EvalRunner(environment, options)
        result = runner.run(
            selected,
            suite_label=suite,
            filters={
                "scenario": list(args.scenario),
                "agent": args.agent,
                "tags": list(args.tag),
                "repeat": args.repeat,
            },
        )
        for case in result.cases:
            _print_case(case)
        summary = result.summary()
        result_path = write_result(result, out_dir)
        if args.publish:
            from maa_api.agent.evals.publish import (
                PublishError,
                publish_suite_result,
            )

            try:
                run_id = publish_suite_result(result, {s.id: s for s in selected})
            except PublishError as exc:
                print(f"发布失败：{exc}", file=sys.stderr)
            else:
                print(f"已发布 {run_id}")
        print(
            f"{suite} {args.mode}：{summary['passed']}/{summary['total']} 通过"
            f"（{summary['pass_rate'] * 100:.1f}%），结果 {result_path}"
        )
        threshold = args.min_pass_rate
        if threshold is None and suite == "regression":
            threshold = 1.0
        if threshold is not None and summary["pass_rate"] < threshold:
            failed = True
    print("EVAL FAIL" if failed else "EVAL OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
