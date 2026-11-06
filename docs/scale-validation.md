# 规模与中断恢复验证

本文件记录 PR-4-4 的可复现验证命令、当前本机基线，以及必须在交付硬件
（16C64G）上完成的门槛项。本机结果是回归基线，不是交付验收值。

## 1. 中断恢复演练（强杀 → 重启）

```bash
uv run pytest tests/integration/test_resume_kill.py -q
```

覆盖内容：

- testcontainers 起 PG，Alembic 迁移到 head；
- 子进程 `scripts/resume_demo_task.py` 是真实 runner + PG 状态存储 + 流式落盘，
  处理 8 份 3 页 PDF；
- 父进程轮询 `task_state.outcomes`，首个文档完成并记录后强杀子进程
  （POSIX SIGKILL / Windows TerminateProcess）；
- 给已完成文档的 `.md` 追加哨兵后重启同一 `task_id`：哨兵仍在，说明已完成
  文档的产物没有被重写；
- 最终 `outcomes` 覆盖全部 8 份文档；收尾 `manifest.jsonl` 与落盘产物对账通过。

本机结果：通过（约 11s，2026-11-06）。

## 2. manifest 与落盘产物对账

函数：`pipeline.output.manifest.verify_manifest(output_dir)`

- 清单中的 `md / json / tables` key 必须存在，且解析后仍在输出根内；
- `md/`、`json/`、`tables/` 下的每个产物必须被清单引用；
- 返回 `documents / keys_checked / missing / orphans`，`ok` 要求缺失与孤儿均为空。

运维命令：

```bash
uv run python scripts/verify_manifest.py var/output
```

退出码 0 = 一致；1 = 有缺失/孤儿产物，或清单缺失、损坏。

## 3. 批量规模（本机短样本基线）

```bash
uv run python scripts/e2e_p0.py --files 100
```

本机结果（2026-11-06，Windows 单进程，合成语料）：

| 指标 | 值 |
| ---- | ---- |
| 文档 | 100（含 1 重复、2 损坏拦截） |
| 页 | 239 |
| 耗时 | 5.61 s |
| 吞吐 | ≈ 42.6 页/秒 |

```bash
uv run dataagent bench --sample var/e2e/corpus/dense_000.pdf --repeats 5
```

本机结果（3 页合成 PDF，15 次采样）：

| 指标 | 值 | 门槛 |
| ---- | ---- | ---- |
| 单页 p50 | 11.1 ms | ≤ 120 ms |
| 单页 p95 | 19.4 ms | ≤ 250 ms |
| 内存峰值 | 1.2 MB | ≤ 50 MB |

合成语料吞吐只用于回归对比；真实年报 markdown 抽取约 33 ms/页，压测前需
复核基准文档与并行度。

## 4. 目标硬件门槛（未在本机执行）

| 项目 | 门槛 | 方法 |
| ---- | ---- | ---- |
| 吞吐 | ≥ 30 万页/日（16C64G 连续 1 小时） | 真实年报/招标语料，批量入口，取稳定吞吐 |
| 内存泄漏 | 72h 增长 < 5% | 连续处理 ≥72h，定时采样 RSS |
| 中断恢复 | 任意时刻强杀后重启不重做已完成文档 | 在目标硬件复跑 §1 演练 |

三项完成前，T127 硬门禁保持未勾选；`dataagent bench` 中对应条目也保持 N/A。
