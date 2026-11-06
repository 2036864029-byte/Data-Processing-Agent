"""交付清单对账（PR-4-4）。

manifest 的每个 md/json/tables key 必须有对应文件；交付目录下的每个
md/json/tables 产物必须被 manifest 引用。退出码 0 = 一致，1 = 不一致或清单不可用。

用法：`uv run python scripts/verify_manifest.py var/output`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dataagent.pipeline.output.manifest import verify_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交付清单与落盘产物对账")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)

    try:
        report = verify_manifest(args.output_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
