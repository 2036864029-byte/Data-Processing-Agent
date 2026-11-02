"""任务编排的组合根：把真实适配器装配成 TaskRunner。

只负责依赖构造，不承载业务逻辑；测试、CLI 与脚本共用。
"""

from __future__ import annotations

from pathlib import Path

from dataagent.contracts.plugin import PluginContext
from dataagent.kernel.policies.rule_route import RuleRoutePolicy
from dataagent.kernel.registry import PluginRegistry
from dataagent.kernel.supervisor import ProgressRepository, Supervisor
from dataagent.pipeline.ingest.ingestor import (
    Ingestor,
    InMemoryDocumentStore,
    InMemoryPendingQueue,
    PendingQueue,
)
from dataagent.pipeline.profile.writer import InMemoryProfileStore, PageProfileWriter
from dataagent.pipeline.route.routes import (
    InMemoryRouteDecisionStore,
    RegistryPdfClassifier,
    TwoLevelRouter,
)
from dataagent.pipeline.sniff.dispatcher import FormatDispatcher
from dataagent.pipeline.task.config import TaskConfig
from dataagent.pipeline.task_runner import TaskRunner
from dataagent.pipeline.telemetry import TaskTelemetry
from dataagent.platform.plugins.adapters import builtin_plugins
from dataagent.platform.store.sql.engine import Database
from dataagent.platform.store.sql.task_state import SqlTaskStateStore, TaskStateStore
from dataagent.platform.store.sql.tasks import TaskProgressStore


def create_builtin_runner(
    *,
    pending: PendingQueue | None = None,
    output_dir: Path | str = "var/output",
    config: TaskConfig | None = None,
    telemetry: TaskTelemetry | None = None,
    progress: TaskProgressStore | None = None,
    supervisor: Supervisor | None = None,
    state_repository: ProgressRepository | None = None,
    state_store: TaskStateStore | None = None,
    database: Database | None = None,
) -> TaskRunner:
    """构造内置引擎 runner；传入 database 时状态与续跑点落 PG。"""
    if state_store is None and database is not None:
        state_store = SqlTaskStateStore(database)
    document_store = InMemoryDocumentStore()
    pending_queue = pending if pending is not None else InMemoryPendingQueue()
    registry = PluginRegistry()
    for plugin in builtin_plugins():
        registry.register(plugin, enable=True)
    classifier = RegistryPdfClassifier(
        registry, PluginContext(task_id="task-local", doc_id="classifier")
    )
    router = TwoLevelRouter(
        decisions=InMemoryRouteDecisionStore(),
        policy=RuleRoutePolicy(),
        classifier=classifier,
    )
    return TaskRunner(
        ingestor=Ingestor(documents=document_store, pending=pending_queue),
        dispatcher=FormatDispatcher(pending=pending_queue),
        router=router,
        registry=registry,
        profiles=PageProfileWriter(InMemoryProfileStore()),
        output_dir=output_dir,
        config=config,
        pending=pending_queue,
        telemetry=telemetry,
        progress=progress,
        supervisor=supervisor,
        state_repository=state_repository,
        state_store=state_store,
    )
