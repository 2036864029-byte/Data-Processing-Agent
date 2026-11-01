"""输出落库集成测试（T114，B7）。

交付产物按页 upsert；缺口页同样落库（gap = true），不得跳过。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from dataagent.contracts.artifacts import PageArtifact
from dataagent.contracts.errors import DefectCode
from dataagent.pipeline.merge.merger import merge_document
from dataagent.pipeline.output.delivery import decide_delivery
from dataagent.pipeline.output.sql import SqlDocumentOutputStore
from dataagent.pipeline.repair.fallback import FallbackPath
from dataagent.pipeline.repair.gap import PageGap
from dataagent.platform.store.sql.engine import Database

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[4]
DOC_ID = "d" * 64


def alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def pg_url(pg_container) -> str:
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(pg_container.port)
    return (
        f"postgresql+psycopg://{pg_container.username}:{pg_container.password}"
        f"@{host}:{port}/{pg_container.dbname}"
    )


@pytest.fixture
def store(pg_url: str) -> SqlDocumentOutputStore:
    command.upgrade(alembic_config(pg_url), "head")
    return SqlDocumentOutputStore(Database(pg_url))


def document():
    artifacts = [
        PageArtifact(
            doc_id=DOC_ID,
            page_no=page,
            engine="engine.pdf@1.0.0",
            params_hash="params",
            markdown=f"page {page}\n",
        )
        for page in (1, 3)
    ]
    gaps = [
        PageGap(
            doc_id=DOC_ID,
            page_no=2,
            defect_codes=(DefectCode.TABLE_STRUCTURE_BROKEN,),
            fallback_used=FallbackPath.MARK_ONLY,
        )
    ]
    return merge_document(DOC_ID, artifacts, gaps)


def test_pages_including_gaps_are_persisted(store: SqlDocumentOutputStore) -> None:
    merged = document()
    decision = decide_delivery(merged, gap_tolerance=0.5)

    store.save(merged, decision)

    stored = store.load(DOC_ID)
    assert [page.page_no for page in stored] == [1, 2, 3]
    assert [page.gap for page in stored] == [False, True, False]
    assert stored[1].defect_codes == (DefectCode.TABLE_STRUCTURE_BROKEN,)
    assert all(page.delivery_level == "partial_with_gaps" for page in stored)
    assert "dataagent:gap" in stored[1].markdown


def test_upsert_updates_delivery_level(store: SqlDocumentOutputStore) -> None:
    merged = document()
    store.save(merged, decide_delivery(merged, gap_tolerance=0.5))
    store.save(merged, decide_delivery(merged, gap_tolerance=0.01))

    stored = store.load(DOC_ID)

    assert all(page.delivery_level == "needs_review" for page in stored)
