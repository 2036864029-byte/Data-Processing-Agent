"""资源预检：启动自检建议值 / 用户覆盖校验 / OOM 降批链。

宪法「性能与稳定性预算」：批大小与 worker 数按公式
`min(核数×1.25, 可用内存/单进程上限, 32)` 计算，**禁止**写死 `cpu_count()`；
用户覆盖后必须做可行性校验，超出机器能力告警并降级，不允许「改完偷偷 OOM」；
OOM 自动降批 32→16→8，降到最低仍失败则停止并告警。
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_BATCH_PAGES = 32
MEMORY_PER_PROCESS_MB = 50
OOM_BATCH_LADDER: tuple[int, ...] = (32, 16, 8)


class OomExhaustedError(RuntimeError):
    """降批已到最低档仍 OOM：停止该任务并告警。"""


@dataclass(frozen=True, slots=True)
class MachineProfile:
    """启动自检探测到的机器能力（显式传入，公式不直接读宿主）。"""

    cpu_cores: int
    available_memory_mb: int

    def __post_init__(self) -> None:
        if self.cpu_cores < 1:
            raise ValueError("cpu_cores 必须 ≥ 1")
        if self.available_memory_mb < 1:
            raise ValueError("available_memory_mb 必须 ≥ 1")


@dataclass(frozen=True, slots=True)
class ResourceSetting:
    batch_pages: int
    workers: int
    warnings: tuple[str, ...] = ()


def _recommended(profile: MachineProfile, memory_per_process_mb: int) -> int:
    if memory_per_process_mb < 1:
        raise ValueError("memory_per_process_mb 必须 ≥ 1")
    cpu_bound = int(profile.cpu_cores * 1.25)
    memory_bound = profile.available_memory_mb // memory_per_process_mb
    return max(1, min(cpu_bound, memory_bound, MAX_BATCH_PAGES))


def recommended_batch_pages(
    profile: MachineProfile, *, memory_per_process_mb: int = MEMORY_PER_PROCESS_MB
) -> int:
    return _recommended(profile, memory_per_process_mb)


def recommended_workers(
    profile: MachineProfile, *, memory_per_process_mb: int = MEMORY_PER_PROCESS_MB
) -> int:
    return _recommended(profile, memory_per_process_mb)


def resolve_resources(
    profile: MachineProfile,
    *,
    batch_pages: int | None = None,
    workers: int | None = None,
    memory_per_process_mb: int = MEMORY_PER_PROCESS_MB,
) -> ResourceSetting:
    """合并启动自检建议值与用户覆盖；超限告警并降到建议值。"""
    batch_ceiling = recommended_batch_pages(
        profile, memory_per_process_mb=memory_per_process_mb
    )
    worker_ceiling = recommended_workers(
        profile, memory_per_process_mb=memory_per_process_mb
    )
    warnings: list[str] = []

    resolved_batch = batch_ceiling if batch_pages is None else batch_pages
    if resolved_batch < 1:
        raise ValueError("batch_pages 必须 ≥ 1")
    if resolved_batch > batch_ceiling:
        warnings.append(
            f"batch_pages={resolved_batch} 超出机器能力，降级为 {batch_ceiling}"
        )
        resolved_batch = batch_ceiling

    resolved_workers = worker_ceiling if workers is None else workers
    if resolved_workers < 1:
        raise ValueError("workers 必须 ≥ 1")
    if resolved_workers > worker_ceiling:
        warnings.append(
            f"workers={resolved_workers} 超出机器能力，降级为 {worker_ceiling}"
        )
        resolved_workers = worker_ceiling

    return ResourceSetting(
        batch_pages=resolved_batch,
        workers=resolved_workers,
        warnings=tuple(warnings),
    )


@dataclass
class OomBatchDegrader:
    """OOM 降批链：32→16→8；最低档仍 OOM 时停止并告警。"""

    batch_pages: int = MAX_BATCH_PAGES
    ladder: tuple[int, ...] = OOM_BATCH_LADDER
    exhausted: bool = False

    def __post_init__(self) -> None:
        if self.batch_pages < 1:
            raise ValueError("batch_pages 必须 ≥ 1")

    def on_oom(self) -> int:
        """发生 OOM 时降一档并返回新批大小；无更低档则停止并告警。"""
        next_batch = next(
            (size for size in self.ladder if size < self.batch_pages), None
        )
        if next_batch is None:
            self.exhausted = True
            raise OomExhaustedError(
                f"批大小已降至最低 {self.batch_pages} 仍 OOM，停止任务并告警"
            )
        self.batch_pages = next_batch
        return next_batch
