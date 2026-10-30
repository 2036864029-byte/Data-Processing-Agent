# Contract: 角色间消息（四角色仅三条消息）

**来源**: PRD 2.6 | **Plan**: plan.md | **用途**: 解析执行器 / 质检器 / 修复执行器之间全部交互的边界；禁止自由文本

## 三条消息

| 消息 | 从 → 到 | 必填字段 | 落库表 |
| ---- | ---- | ---- | ---- |
| `ParseRequest` | 调度器 → 解析器 | `task_id, doc_id, page_range, engine, params_hash, rulepack_hash, resource_hint, doc_affinity_key` | `page_artifact` 的来源记录 |
| `PageReport` | 质检器 → 调度器 / 修复执行器 | `doc_id, page_no, verdict, extract_quality, defects[], attribution, recommended_action, qc_confidence` | `page_report` |
| `OperatorResult` | 修复执行器 → 质检器 | `doc_id, page_no, candidate_artifact_id(可空), changed, cost, notes` | `action` |

## 三条硬性约束

1. **解析器不得直接调用质检器或修复执行器**，必须经调度器（否则依赖图不可控、无法审计、预算无法集中扣减）。
2. **质检结果必须先落库再触发下游**（不允许"消息到了但库里没有"，崩溃重启后无法判断该页是否判过）。
3. **`OperatorResult.changed == false` 必须被视为"此路不通"**，立即停止该动作链，不得重试同参数。

## 交互开销预算（上限，超出即设计错误）

| 开销项 | 上限 |
| ---- | ---- |
| 每次任务的角色间消息条数 | ≤ `4 × 批数 + 3 × fail 页数 + 常数` |
| 归因模型调用次数 | ≤ `fail 页数 × 1.1`（按 fail 率而非页数计） |
| artifact 跨进程传递 | **仅解析器 → 控制面一次**，方向单向 |
| 单页状态往返队列次数 | ≤ 3（对应 T2/T3 总预算） |
| 同步等待 | 禁止角色间长同步等待（一律事件驱动 + 状态落库） |

## 消息 Schema（JSON）

```jsonc
// ParseRequest
{
  "task_id": "uuidv7",
  "doc_id": "<sha256>",
  "page_range": [1, 33],
  "engine": "engine.pdf@1.0.0",
  "params_hash": "<sha256>",
  "rulepack_hash": "<sha256>",
  "resource_hint": "cpu",
  "doc_affinity_key": "<hash>"
}

// PageReport —— 完整字段见 qc-output.md
{
  "doc_id": "<sha256>",
  "page_no": 1,
  "verdict": "pass | fail | uncertain",
  "extract_quality": 0.62,
  "defects": [ /* Defect[] */ ],
  "attribution": "table_structure",
  "recommended_action": "resegment_table",
  "qc_confidence": 0.88
}

// OperatorResult
{
  "doc_id": "<sha256>",
  "page_no": 1,
  "candidate_artifact_id": "<sha256> | null",
  "changed": true,
  "cost": 0,
  "notes": "cell 重建完成"
}
```
