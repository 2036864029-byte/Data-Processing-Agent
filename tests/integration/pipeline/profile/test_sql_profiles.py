"""页面画像落库集成测试（T092，B4）。

两段写入按 (doc_id, page_no) upsert；route_reason 原样往返；
路由段重写不丢已写入的质量段。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from dataagent.contracts.policies import (
    ChainId,
    PageProfile,
    PdfType,
    ProfileClass,
    ProfileLayout,
    ProfileQuality,
    ProfileSignals,
    RouteSuggestion,
    Tier,
)
from dataagent.pipeline.profile.sql import SqlProfileStore
from dataagent.pipeline.profile.writer import PageProfileWriter
from dataagent.pipeline.route.routes import RouteDecision, RouteLevel
from dataagent.platform.store.sql.engine import Database

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[4]
DOC_ID = "c" * 64
ROUTE_REASON = ["low_char_density", "Odd_Plugin_Reason"]


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
def writer(pg_url: str) -> PageProfileWriter:
    command.upgrade(alembic_config(pg_url), "head")
    return PageProfileWriter(SqlProfileStore(Database(pg_url)))


def route_decision() -> RouteDecision:
    profile = PageProfile.model_validate(
        {
            "doc_id": DOC_ID,
            "page_no": 4,
            "class": ProfileClass(
                pdf_type=PdfType.MIXED,
                class_confidence=0.83,
                route=RouteSuggestion.OCR,
                route_reason=ROUTE_REASON,
            ),
            "layout": ProfileLayout(
                columns=2, has_table=True, is_complex=True, rotation=90
            ),
        }
    )
    return RouteDecision(
        doc_id=DOC_ID,
        level=RouteLevel.PAGE,
        page_no=4,
        chain=ChainId.PDF_OCR,
        reasons=tuple(ROUTE_REASON),
        profile_snapshot=profile.model_dump(by_alias=True, mode="json"),
    )


def test_two_stage_round_trip(writer: PageProfileWriter) -> None:
    writer.write_route(route_decision())
    signals = ProfileSignals(
        char_density=9.5,
        glyph_coverage=0.96,
        empty_region_ratio=0.25,
        header_footer_ratio=0.04,
    )
    quality = ProfileQuality(extract_quality=0.87, tier=Tier.T1)

    writer.write_quality(DOC_ID, 4, signals=signals, quality=quality)

    loaded = writer.get(DOC_ID, 4)
    assert loaded.pdf_type is PdfType.MIXED
    assert loaded.class_.route_reason == ROUTE_REASON
    assert loaded.columns == 2
    assert loaded.rotation == 90
    assert loaded.signals == signals
    assert loaded.quality == quality


def test_route_rewrite_preserves_quality_in_database(
    writer: PageProfileWriter,
) -> None:
    writer.write_route(route_decision())
    signals = ProfileSignals(
        char_density=9.5,
        glyph_coverage=0.96,
        empty_region_ratio=0.25,
        header_footer_ratio=0.04,
    )
    writer.write_quality(
        DOC_ID,
        4,
        signals=signals,
        quality=ProfileQuality(extract_quality=0.87, tier=Tier.T1),
    )

    writer.write_route(route_decision())

    loaded = writer.get(DOC_ID, 4)
    assert loaded.signals == signals
    assert loaded.quality is not None
    assert loaded.quality.extract_quality == 0.87
