# Contract: 决策点策略化（Policies）

**来源**: PRD V2.3 2.2 / 4.1.9 | **Plan**: plan.md | **用途**: 8 个决策点的统一接口；页级全部规则实现、任务级可模型实现

## 分界线（一句话）

**页级决策参与 STP 计算，必须规则、必须可复现；任务级决策每任务只发生几次，可用模型且不破坏可复现（Plan 冻结 + 同一 `plan_version` 重放）。**

## 8 个决策点

| # | 决策点 | 层级 | 输入 | 输出 | P0 实现 | 未来 | 是否参与指标 |
| -- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| 1 | `PlanPolicy` | L1 | 自然语言 + 任务统计 | Plan（3 种 stage kind） | 模型 | 模型 | 否 |
| 2 | `ReplanPolicy` | L1 | Plan 执行统计 | 新 Plan | 规则（阈值触发） | 模型 | 否 |
| 3 | `StrategyPolicy` | L1→L2 | 任务统计 | Strategy 枚举（整本 OCR / 降档 / 中止） | 规则 | 模型 | 否 |
| 4 | `RoutePolicy` | L2 | PageProfile（class / layout） | 链路 ID | 规则 | **保持规则** | **是** |
| 5 | `TierPolicy` | L2 | PageProfile + `extract_quality` | T0–T3 | 规则 | **保持规则** | **是** |
| 6 | `OperatorPolicy` | L2 | Defect | 算子 | 规则（mapping 表） | **保持规则** | **是** |
| 7 | `AttemptPolicy` | L2 | OperatorResult | 继续 / 停机 | 规则（三层停机） | **保持规则** | **是** |
| 8 | `EscalationPolicy` | L2 | 预算 / 归因 | 升级人工 / 继续 | 规则（预算耗尽） | 保持规则 | 是 |

## Protocol 签名（设计契约，非最终实现）

```python
from typing import Protocol

class PlanPolicy(Protocol):
    def compile(self, nl: str, task_stats: dict) -> Plan: ...

class ReplanPolicy(Protocol):
    def decide(self, exec_stats: dict) -> Plan | None: ...

class StrategyPolicy(Protocol):
    def select(self, task_stats: dict) -> Strategy: ...

class RoutePolicy(Protocol):
    def route(self, profile: PageProfile) -> ChainId: ...

class TierPolicy(Protocol):
    def tier(self, profile: PageProfile, quality: float) -> Tier: ...

class OperatorPolicy(Protocol):
    def operator(self, defect: Defect) -> OperatorId: ...

class AttemptPolicy(Protocol):
    def continue_(self, result: OperatorResult) -> bool: ...

class EscalationPolicy(Protocol):
    def escalate(self, budget: Budget, attribution: Attribution) -> bool: ...
```

## 实现约束（硬性）

1. **必须无状态**：Policy 实例不得持有跨调用状态；入参 / 出参可重放。
2. **必须可注入假实现**：测试与标定通过注入规则版 / 假实现完成，不依赖模型。
3. **页级 5 个（#4–#8）P0 即规则实现**，位于 `kernel/policies/rule_*.py`；**不得替换为模型**（宪法原则 I）。
4. **任务级 3 个（#1–#3）**：P0 用规则（阈值触发，如 40% 页失败→整本转 OCR），M3 可加模型实现 `kernel/policies/model_*.py`；模型实现**唯一允许使用 pydantic-ai**（宪法门禁）。
5. 任一 Policy 的输入输出必须可序列化并写入 Trace；页级决策的输入 / 输出参与审计。

## 与确定性优先的关系

- 页级 Policy 全部为确定性规则 → 同一输入同一输出。
- 任务级 Policy 若用模型：Plan 生成后冻结，执行 / 重放沿冻结 Plan → 同一 `plan_version` 结果逐字一致。
- 分支求值（Plan 的 `decision` stage）复用 `platform/rules/` 受限表达式，白名单仅限统计指标。
