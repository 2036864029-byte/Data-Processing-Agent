"""质检报告落库集成测试（T095，B5）。

质检结果先落库再触发下游；按 (doc_id, page_no) upsert，缺陷清单可完整还原。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from dataagent.pipeline.quality.gate import QualityGate
from dataagent.pipeline.quality.l1_check import L1_CHECKER, L1Metrics, L1PageInput
from dataagent.pipeline.quality.sql import SqlQualityResultStore
from dataagent.platform.store.sql.engine import Database

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[4]
DOC_ID = "e" * 64


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
def store(pg_url: str) -> SqlQualityResultStore:
    command.upgrade(alembic_config(pg_url), "head")
    return SqlQualityResultStore(Database(pg_url))


def gate_for(store: SqlQualityResultStore) -> QualityGate:
    return QualityGate(checker=L1_CHECKER, store=store)


def page_input(*, glyph_coverage: float) -> L1PageInput:
    return L1PageInput(
        doc_id=DOC_ID,
        page_no=12,
        metrics=L1Metrics(
            glyph_coverage=glyph_coverage,
            char_density=8.0,
            cid_unmapped_ratio=0.001,
            empty_region_ratio=0.05,
            reading_order_score=0.97,
            header_footer_leak=0.01,
            page_number_ok=True,
        ),
    )


def test_report_round_trip_with_defects(store: SqlQualityResultStore) -> None:
    report = gate_for(store).run_page(page_input(glyph_coverage=0.40))

    loaded = store.load(DOC_ID, 12)
    assert loaded == report
    assert loaded is not None
    assert loaded.defects[0].code.value == "encoding_loss"


def test_report_upsert_replaces_previous_verdict(
    store: SqlQualityResultStore,
) -> None:
    gate = gate_for(store)
    gate.run_page(page_input(glyph_coverage=0.40))
    healthy = gate.run_page(page_input(glyph_coverage=0.99))

    loaded = store.load(DOC_ID, 12)
    assert loaded == healthy
    assert loaded is not None
    assert loaded.defects == []
