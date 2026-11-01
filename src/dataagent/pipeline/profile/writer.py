"""页面画像两段写入（B4）。

- 路由段（`class` / `layout`）在路由阶段写；`route_reason` 原样保留（G2）。
- 质量段（`signals` / `quality`）在解析完成后写；`glyph_coverage` 来自 `cmap_gaps`，
  仅抽取后可得，因此路由阶段恒为空（两段写入的根因）。
- 顺序由写入器保证：质量段必须在路由段之后写（`ProfileStageOrderError`）；
  路由段重写保留已写入的质量段，批级重试安全。
"""

from __future__ import annotations

from typing import Protocol

from dataagent.contracts.policies import (
    TEXT_LAYER_RECOVERED_REASON,
    PageProfile,
    ProfileLayout,
    ProfileQuality,
    ProfileSignals,
    RouteSuggestion,
)
from dataagent.pipeline.route.routes import RouteDecision, RouteLevel


class ProfileStageOrderError(Exception):
    """质量段先于路由段写入（违反两段写入顺序）。"""


class ProfileStore(Protocol):
    def load(self, doc_id: str, page_no: int) -> PageProfile | None: ...

    def save(self, profile: PageProfile) -> None: ...


class InMemoryProfileStore:
    def __init__(self) -> None:
        self._profiles: dict[tuple[str, int], PageProfile] = {}

    def load(self, doc_id: str, page_no: int) -> PageProfile | None:
        return self._profiles.get((doc_id, page_no))

    def save(self, profile: PageProfile) -> None:
        self._profiles[(profile.doc_id, profile.page_no)] = profile


class PageProfileWriter:
    def __init__(self, store: ProfileStore) -> None:
        self._store = store

    def write_route(self, decision: RouteDecision) -> PageProfile:
        """写路由段：直接采用决策时的画像快照，保证决策与画像同源。"""
        if decision.level is not RouteLevel.PAGE or decision.page_no is None:
            raise ValueError(
                "路由段写入只接受页级路由决策（RouteLevel.PAGE 且 page_no 非空）"
            )
        segment = PageProfile.model_validate(dict(decision.profile_snapshot))
        existing = self._store.load(segment.doc_id, segment.page_no)
        profile = segment
        if existing is not None and (
            existing.signals is not None or existing.quality is not None
        ):
            profile = segment.model_copy(
                update={"signals": existing.signals, "quality": existing.quality}
            )
        self._store.save(profile)
        return profile

    def write_quality(
        self,
        doc_id: str,
        page_no: int,
        *,
        signals: ProfileSignals,
        quality: ProfileQuality,
    ) -> PageProfile:
        existing = self._require(doc_id, page_no)
        profile = existing.model_copy(update={"signals": signals, "quality": quality})
        self._store.save(profile)
        return profile

    def write_extraction_layout(
        self,
        doc_id: str,
        page_no: int,
        *,
        has_table: bool,
        columns: int,
        is_complex: bool,
        rotation: int = 0,
    ) -> PageProfile:
        """抽取后回填 layout（`detect_pdf` 不提供表格/分栏/复杂度）。"""
        existing = self._require(doc_id, page_no)
        layout = ProfileLayout(
            columns=max(1, int(columns)),
            has_table=has_table,
            is_complex=is_complex,
            rotation=int(rotation),
        )
        profile = existing.model_copy(update={"layout": layout})
        self._store.save(profile)
        return profile

    def apply_text_layer_recovery(
        self,
        doc_id: str,
        page_no: int,
        *,
        reason: str = TEXT_LAYER_RECOVERED_REASON,
    ) -> PageProfile:
        """隐藏文本层恢复：route 改 native，理由追加到 route_reason（幂等）。"""
        existing = self._require(doc_id, page_no)
        reasons = list(existing.class_.route_reason)
        if reason not in reasons:
            reasons.append(reason)
        class_ = existing.class_.model_copy(
            update={"route": RouteSuggestion.NATIVE, "route_reason": reasons}
        )
        profile = existing.model_copy(update={"class_": class_})
        self._store.save(profile)
        return profile

    def get(self, doc_id: str, page_no: int) -> PageProfile:
        profile = self._store.load(doc_id, page_no)
        if profile is None:
            raise KeyError(f"页面画像不存在：{doc_id}:{page_no}")
        return profile

    def _require(self, doc_id: str, page_no: int) -> PageProfile:
        profile = self._store.load(doc_id, page_no)
        if profile is None:
            raise ProfileStageOrderError(
                f"页 {doc_id}:{page_no} 的路由段尚未写入"
            )
        return profile
