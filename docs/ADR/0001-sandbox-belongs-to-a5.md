# ADR-0001：沙箱设施归属 A5（而非 A6）

- **状态**：Accepted
- **日期**：2026-11-01
- **相关**：A5（插件注册表与加载器，T035）、A6（续跑与资源预检）

## 背景

A5 的插件加载器分热 / 冷路径：冷路径插件（OCR / 外部增强器 / connector / exporter）
必须在子进程中执行，需要超时、资源上限与进程隔离。A6 的进程池 / 心跳也会复用同一执行设施。
若把沙箱留给 A6，A5 的加载器无法独立完成与验收。

## 决策

将 `platform/runtime/sandbox.py`（`SubprocessSandbox` / `SandboxLimits` / `SandboxResult`）
归入 **A5（T035）** 实现；A6 只复用，不重复实现。A5 的加载器冷路径直接调用该设施。

## 后果

- A5 可在 A6 之前独立完成与测试，阶段依赖更清晰。
- `platform/runtime/` 成为执行设施的单一归属（sandbox / heartbeat / resources / pool）。
- P0 只实现 `subprocess` 模式；manifest 声明 `container` / `remote` 时加载器显式拒绝，
  留待后续阶段（P1）扩展。
- Windows 只保证超时与进程隔离；POSIX 额外设置 `RLIMIT_AS` / `RLIMIT_CPU`。
