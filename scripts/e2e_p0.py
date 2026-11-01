"""P0 端到端验证脚本（T125 / quickstart S1–S4 + S6）。

用法：`uv run python scripts/e2e_p0.py [--files 100] [--keep]`

覆盖：
- S1 混合语料批量处理：无静默丢弃、内容去重、损坏拦截、后缀不符按内容判定；
- S2 复杂页标记 + 分级交付 + 诊断信息（报告按需明细）；
- S3 规则包发布 / 冲突阻断 / 回滚 / dry-run；
- S4 分层报告（总览 / 按原因 / 文件清单 / 决策链还原）；
- S6 Plan 探路 → decision 分支 → 批量。

退出码 0 = 全部断言通过；1 = 有断言失败。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "eval"))

from construct import PageSpec, build_pdf  # noqa: E402

from dataagent.kernel.plan import parse_plan  # noqa: E402
from dataagent.pipeline.task_runner import (  # noqa: E402
    TaskConfig,
    TaskOutcome,
    create_builtin_runner,
)
from dataagent.platform.rules.rulepack import (  # noqa: E402
    CheckType,
    RuleCheck,
    RulePack,
    RulePackConflictError,
    RulePackRegistry,
    dry_run,
)

DENSE_LINES = 40


def _dense_page(prefix: str) -> PageSpec:
    return PageSpec(
        lines=[
            (72.0, 740 - row * 18, 10, f"{prefix} paragraph {row:03d} supporting text")
            for row in range(DENSE_LINES)
        ]
    )


def _build_corpus(root: Path) -> list[Path]:
    corpus = root / "corpus"
    if corpus.exists():
        shutil.rmtree(corpus)
    corpus.mkdir(parents=True)

    files: list[Path] = []
    for index in range(71):
        path = corpus / f"dense_{index:03d}.pdf"
        path.write_bytes(
            build_pdf(
                [
                    _dense_page(f"alpha{index}"),
                    _dense_page(f"bravo{index}"),
                    _dense_page(f"charlie{index}"),
                ]
            )
        )
        files.append(path)

    for index in range(10):
        path = corpus / f"sparse_{index:03d}.pdf"
        path.write_bytes(
            build_pdf([PageSpec(lines=[(72.0, 720.0, 10, f"hi {index}")])])
        )
        files.append(path)

    for index in range(5):
        path = corpus / f"table_{index:03d}.csv"
        path.write_bytes(
            f"name,value\nalpha{index},1\nbeta{index},2\n".encode()
        )
        files.append(path)

    for index in range(5):
        path = corpus / f"notes_{index:03d}.txt"
        path.write_bytes(f"plain document {index}\nsecond line\n".encode())
        files.append(path)

    for index in range(5):
        path = corpus / f"mismatch_{index:03d}.pdf"
        path.write_bytes(f"name,value\ngamma{index},1\ndelta{index},2\n".encode())
        files.append(path)

    (corpus / "copy_a.txt").write_bytes(b"duplicated payload\n")
    (corpus / "copy_b.txt").write_bytes(b"duplicated payload\n")

    (corpus / "broken_a.bin").write_bytes(bytes(range(0, 16)) * 8)
    (corpus / "broken_b.bin").write_bytes(bytes(range(240, 256)) * 8)

    return sorted(corpus.glob("*.*"))


def _check(name: str, ok: bool, detail: str = "") -> tuple[str, bool, str]:
    return name, ok, detail


def _run_s1_s2_s4(root: Path, files: list[Path]) -> list[tuple[str, bool, str]]:
    runner = create_builtin_runner(output_dir=root / "output")
    outcome = runner.run(files, task_id="e2e-mixed")

    checks = [
        _check(
            "S1 无静默丢弃",
            len(outcome.documents) == len(files),
            f"{len(outcome.documents)}/{len(files)}",
        ),
        _check(
            "S1 内容去重",
            outcome.count("duplicate") == 1,
            f"duplicate={outcome.count('duplicate')}",
        ),
        _check(
            "S1 损坏拦截入待确认",
            outcome.count("pending") + outcome.count("rejected") == 2,
            f"pending={outcome.count('pending')}, rejected={outcome.count('rejected')}",
        ),
        _check(
            "S1 后缀不符按内容判定",
            sum(
                1
                for document in outcome.documents
                if document.family == "tabular" and document.path.endswith(".pdf")
            )
            == 5,
            "mismatch_*.pdf → tabular",
        ),
        _check(
            "S1 处理页数 > 0",
            outcome.stats.pages_processed > 0,
            f"pages={outcome.stats.pages_processed}",
        ),
        _check(
            "S2 缺口页标记 + 分级交付",
            outcome.stats.pages_failed > 0
            and any(
                document.level is not None
                and document.level.value in {"partial_with_gaps", "needs_review"}
                for document in outcome.documents
            ),
            f"gap_pages={outcome.stats.pages_failed}",
        ),
        _check(
            "S2 诊断明细可按需取用",
            any(outcome.report.detail(document.doc_id) for document in outcome.documents),
            "report.detail(doc_id)",
        ),
        _check(
            "S4 分层报告（总览 + 原因 + 文件清单）",
            outcome.report.overview.total_docs > 0 and bool(outcome.report.files),
            outcome.report.overview.top_failure or "无缺口",
        ),
        _check(
            "S1 输出文件全部落盘",
            all(
                Path(output).is_file()
                for document in outcome.documents
                for output in document.outputs
            ),
            "json + markdown",
        ),
    ]
    _print_summary(outcome)
    return checks


def _print_summary(outcome: TaskOutcome) -> None:
    print("── 任务总览 ──")
    print(
        f"文档 {len(outcome.documents)}：processed={outcome.count('processed')} "
        f"duplicate={outcome.count('duplicate')} pending={outcome.count('pending')} "
        f"rejected={outcome.count('rejected')} failed={outcome.count('failed')}"
    )
    print(
        f"页：total={outcome.stats.pages_total} processed={outcome.stats.pages_processed} "
        f"failed={outcome.stats.pages_failed} stp_rate={outcome.stats.stp_rate:.3f} "
        f"gap_ratio={outcome.stats.gap_ratio:.3f}"
    )
    print(
        f"平均质量={outcome.stats.avg_extract_quality:.3f} "
        f"耗时={outcome.stats.duration_s:.2f}s"
    )
    print("── 报告（Markdown 前 12 行）──")
    for line in outcome.report.to_markdown().splitlines()[:12]:
        print(line)


def _run_s3() -> list[tuple[str, bool, str]]:
    registry = RulePackRegistry()
    v1 = RulePack(
        rulepack="e2e_rules",
        version="1.0.0",
        fields=[],
        checks=[
            RuleCheck(
                id="stp_floor",
                type=CheckType.EXPRESSION,
                expr={">=": [{"var": "stp_rate"}, 0.8]},
                requirement="探路 STP 不低于 0.8",
            )
        ],
        thresholds={},
    )
    registry.publish(v1)
    report = dry_run(v1, [{"stp_rate": 0.9}, {"stp_rate": 0.5}])
    v2 = v1.model_copy(update={"version": "2.0.0"})
    registry.publish(v2)
    blocked = False
    try:
        registry.publish(v1)
    except RulePackConflictError:
        blocked = True
    rolled_back = registry.rollback("e2e_rules", "1.0.0")
    return [
        _check("S3 dry-run 通过 / 失败样本各 1", report.passed == 1 and report.failed == 1),
        _check("S3 版本发布生效", registry.versions("e2e_rules") == ["1.0.0", "2.0.0"]),
        _check("S3 冲突发布被阻断", blocked),
        _check("S3 回滚立即生效", rolled_back.version == "1.0.0"),
    ]


def _run_s6(root: Path) -> list[tuple[str, bool, str]]:
    plan = parse_plan(
        {
            "version": 1,
            "stages": [
                {
                    "id": "probe",
                    "kind": "processing",
                    "scope": {"pages": "1-6"},
                    "strategy": "quality",
                },
                {
                    "id": "decide",
                    "kind": "decision",
                    "on": {"metric": "stp_rate", "window": "probe"},
                    "branches": [
                        {
                            "when": {">=": [{"var": "stp_rate"}, 0.85]},
                            "goto": "bulk_fast",
                        },
                        {"else": True, "goto": "bulk_safe"},
                    ],
                },
                {
                    "id": "bulk_fast",
                    "kind": "processing",
                    "scope": {"pages": "7-"},
                    "strategy": "fast",
                },
                {
                    "id": "bulk_safe",
                    "kind": "processing",
                    "scope": {"pages": "7-"},
                    "strategy": "quality",
                },
            ],
            "guards": {"max_model_calls": 8, "max_replans": 3, "max_wall_time_s": 3600},
        }
    )
    plan_dir = root / "plan_corpus"
    if plan_dir.exists():
        shutil.rmtree(plan_dir)
    plan_dir.mkdir()
    files: list[Path] = []
    for index in range(4):
        path = plan_dir / f"plan_{index:03d}.pdf"
        path.write_bytes(
            build_pdf([_dense_page(f"plan{index}"), _dense_page(f"bulk{index}")])
        )
        files.append(path)

    runner = create_builtin_runner(
        output_dir=root / "plan_output",
        config=TaskConfig(plan=plan),
    )
    outcome = runner.run(files, task_id="e2e-plan")
    stages = (
        [stage.id for stage in outcome.plan_resolution.stages]
        if outcome.plan_resolution is not None
        else []
    )
    print("── S6 Plan ──")
    print(f"stages={stages} decisions={[d.branch_taken for d in outcome.decisions]}")
    return [
        _check(
            "S6 探路 → decision → 批量",
            stages == ["probe", "bulk_fast"],
            f"stages={stages}",
        ),
        _check(
            "S6 分支命中 fast 批量",
            outcome.decisions[0].branch_taken == "bulk_fast",
            outcome.decisions[0].metric_snapshot if outcome.decisions else "无决策",
        ),
        _check(
            "S6 同版本重放一致",
            plan.fingerprint == parse_plan(plan.spec.model_dump(by_alias=True)).fingerprint,
        ),
        _check("S6 guards 未触发", outcome.stats.stopped_by is None),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P0 端到端验证（T125）")
    parser.add_argument("--root", type=Path, default=ROOT / "var" / "e2e")
    parser.add_argument("--files", type=int, default=100)
    parser.add_argument("--keep", action="store_true", help="保留语料目录")
    args = parser.parse_args(argv)

    args.root.mkdir(parents=True, exist_ok=True)
    files = _build_corpus(args.root)
    if len(files) != args.files:
        print(f"[WARN] 语料数 {len(files)} != --files {args.files}")

    checks = _run_s1_s2_s4(args.root, files)
    checks.extend(_run_s3())
    checks.extend(_run_s6(args.root))

    print("\n── 断言结果 ──")
    failures = 0
    for name, ok, detail in checks:
        mark = "OK" if ok else "FAIL"
        suffix = f"（{detail}）" if detail else ""
        print(f"[{mark}] {name}{suffix}")
        failures += 0 if ok else 1

    if not args.keep:
        shutil.rmtree(args.root / "output", ignore_errors=True)
        shutil.rmtree(args.root / "plan_output", ignore_errors=True)

    print(f"\n{len(checks) - failures}/{len(checks)} 通过")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
