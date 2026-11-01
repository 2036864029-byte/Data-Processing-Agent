"""路由决策落库集成测试（T088，B3）。

每条决策含理由与画像快照；顺序与内容可完整还原（FR-004 可事后解释）。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from dataagent.contracts.policies import ChainId
from dataagent.pipeline.route.routes import RouteDecision, RouteLevel
from dataagent.pipeline.route.sql import SqlRouteDecisionStore
from dataagent.platform.store.sql.engine import Database

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[4]
DOC_ID = "a" * 64


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
def store(pg_url: str) -> SqlRouteDecisionStore:
    command.upgrade(alembic_config(pg_url), "head")
    return SqlRouteDecisionStore(Database(pg_url))


def test_route_decisions_round_trip(store: SqlRouteDecisionStore) -> None:
    decisions = [
        RouteDecision(
            doc_id=DOC_ID,
            level=RouteLevel.FORMAT,
            page_no=None,
            chain=None,
            reasons=("pdf_magic", "family:pdf"),
            profile_snapshot={"sniff": {"family": "pdf", "state": "confident"}},
        ),
        RouteDecision(
            doc_id=DOC_ID,
            level=RouteLevel.PAGE,
            page_no=2,
            chain=ChainId.PDF_OCR,
            reasons=("low_char_density", "route:ocr", "pdf_type:text_based"),
            profile_snapshot={
                "class": {
                    "pdf_type": "text_based",
                    "route": "ocr",
                    "route_reason": ["low_char_density"],
                }
            },
        ),
    ]
    for decision in decisions:
        store.save(decision)

    loaded = store.for_doc(DOC_ID)

    assert [decision.as_dict() for decision in loaded] == [
        decision.as_dict() for decision in decisions
    ]


def test_route_reason_list_order_preserved(store: SqlRouteDecisionStore) -> None:
    reasons = ("first", "second", "third")
    store.save(
        RouteDecision(
            doc_id="b" * 64,
            level=RouteLevel.PAGE,
            page_no=1,
            chain=ChainId.PDF_NATIVE,
            reasons=reasons,
            profile_snapshot={},
        )
    )

    loaded = store.for_doc("b" * 64)

    assert loaded[0].reasons == reasons
