#!/usr/bin/env bash
# DataAgent 工程门禁脚本（宪法 G9）——违反即失败。
# 空仓库不误报：被检查的目标文件/目录不存在时视为通过。
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SRC="src/dataagent"
failures=0

fail() {
  echo "GATE FAIL: $1"
  failures=$((failures + 1))
}

# 1) 页码基准转换符号（page_from_plugin / page_to_internal）仅出现在 contracts/pages.py
#    ——宪法 II：全工程唯一转换点，内部一律 1-indexed。
for sym in page_from_plugin page_to_internal; do
  hits=$(grep -rn "$sym" "$SRC" --include="*.py" 2>/dev/null \
    | grep -v "$SRC/contracts/pages.py" || true)
  if [ -n "$hits" ]; then
    fail "页码转换符号 $sym 出现在 $SRC/contracts/pages.py 之外"
    printf '%s\n' "$hits"
  fi
done

# 2) apps/worker 内禁止 asyncio.run（CPU 密集路径禁事件循环）
hits=$(grep -rn "asyncio\.run" "$SRC/apps/worker" --include="*.py" 2>/dev/null || true)
if [ -n "$hits" ]; then
  fail "apps/worker 内出现 asyncio.run"
  printf '%s\n' "$hits"
fi

# 3) 业务代码禁止直接 redis.xadd / boto3.client（必须经 platform/store/ 抽象）
for pat in "redis\.xadd" "boto3\.client"; do
  hits=$(grep -rn "$pat" "$SRC" --include="*.py" 2>/dev/null \
    | grep -v "$SRC/platform/store" || true)
  if [ -n "$hits" ]; then
    fail "业务代码直接 $pat（仅允许 platform/store/，宪法 II）"
    printf '%s\n' "$hits"
  fi
done

# 4) 算子（pipeline/repair）禁止 IO：open( / requests / 数据库连接（宪法 V：算子必须幂等且无副作用）
hits=$(grep -rn -E "\bopen\(|requests\.|import requests" "$SRC/pipeline/repair" --include="*.py" 2>/dev/null || true)
if [ -n "$hits" ]; then
  fail "pipeline/repair 算子出现 IO（open / requests / db）"
  printf '%s\n' "$hits"
fi

# 5) pydantic_ai 仅允许 kernel/policies/model_*.py（宪法 I + D11）
hits=$(grep -rn "pydantic_ai" "$SRC" --include="*.py" 2>/dev/null \
  | grep -vE "$SRC/kernel/policies/model_.+\.py" || true)
if [ -n "$hits" ]; then
  fail "pydantic_ai 出现在 kernel/policies/model_*.py 之外"
  printf '%s\n' "$hits"
fi

# 6) 环境 / 安装脚本禁止 pip install / poetry / conda（G7：uv 唯一，禁 pip / poetry / conda）
hits=$(grep -rn -E "pip install|poetry|conda" scripts deploy .github \
  --include="*.sh" --include="*.yml" --include="*.yaml" --include="Dockerfile*" \
  --exclude="check-gates.sh" 2>/dev/null || true)
if [ -n "$hits" ]; then
  fail "环境脚本出现 pip install / poetry / conda（G7，仅允许 uv）"
  printf '%s\n' "$hits"
fi

if [ "$failures" -gt 0 ]; then
  echo "check-gates: ${failures} 项失败"
  exit 1
fi
echo "check-gates: 全部门禁通过"
