"""交付目标路由（PR-4-3）：命中 / 歧义 / 不可达 / 回退默认。

路由是 P0 确定性规则：名称与别名（忽略大小写与空白）子串匹配，
最长匹配优先；未指定、未命中、歧义、不可达都回退默认输出根并给出原因。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataagent.pipeline.output.targets import (
    DeliverTarget,
    DeliverTargetRouter,
    RouteReason,
    targets_from_config,
)
from dataagent.platform.config import Settings


def _target(
    root: Path, name: str, aliases: tuple[str, ...] = ()
) -> tuple[DeliverTarget, Path]:
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    return DeliverTarget(name=name, path=path, aliases=aliases), path


def test_not_specified_uses_default(tmp_path: Path) -> None:
    default = tmp_path / "default"
    route = DeliverTargetRouter((), default).route(None)

    assert route.output_dir == default
    assert route.reason is RouteReason.NOT_SPECIFIED
    assert route.target is None
    assert not route.routed


def test_blank_text_uses_default(tmp_path: Path) -> None:
    route = DeliverTargetRouter((), tmp_path / "default").route("   ")
    assert route.reason is RouteReason.NOT_SPECIFIED


def test_matches_target_name_case_insensitively(tmp_path: Path) -> None:
    target, path = _target(tmp_path, "NAS_Reports")
    route = DeliverTargetRouter([target], tmp_path / "default").route(
        "把结果放到 nas_reports 目录"
    )

    assert route.routed
    assert route.reason is RouteReason.MATCHED
    assert route.target == target
    assert route.output_dir == path


def test_matches_alias_ignoring_whitespace(tmp_path: Path) -> None:
    target, path = _target(tmp_path, "nas_finance", aliases=("公司 NAS 财报库",))
    route = DeliverTargetRouter([target], tmp_path / "default").route(
        "存到公司NAS财报库"
    )

    assert route.routed
    assert route.output_dir == path


def test_longest_match_wins(tmp_path: Path) -> None:
    generic, _ = _target(tmp_path, "generic", aliases=("nas",))
    specific, specific_path = _target(
        tmp_path, "specific", aliases=("公司 nas 财报库",)
    )
    route = DeliverTargetRouter([generic, specific], tmp_path / "default").route(
        "把公司 NAS 财报库的结果归档"
    )

    assert route.routed
    assert route.target == specific
    assert route.output_dir == specific_path


def test_equal_matches_are_ambiguous_and_fall_back(tmp_path: Path) -> None:
    first, _ = _target(tmp_path, "first", aliases=("财报库",))
    second, _ = _target(tmp_path, "second", aliases=("财报库",))
    default = tmp_path / "default"
    route = DeliverTargetRouter([first, second], default).route("存到财报库")

    assert route.reason is RouteReason.AMBIGUOUS
    assert route.output_dir == default
    assert "first" in route.detail and "second" in route.detail


def test_no_match_falls_back(tmp_path: Path) -> None:
    target, _ = _target(tmp_path, "nas_finance", aliases=("财报库",))
    default = tmp_path / "default"
    route = DeliverTargetRouter([target], default).route("存到本地就行")

    assert route.reason is RouteReason.NO_MATCH
    assert route.output_dir == default
    assert route.target is None


def test_missing_path_is_unreachable(tmp_path: Path) -> None:
    missing = DeliverTarget("nas", tmp_path / "missing", ("nas",))
    default = tmp_path / "default"
    route = DeliverTargetRouter([missing], default).route("存到 nas")

    assert route.reason is RouteReason.UNREACHABLE
    assert route.output_dir == default
    assert "不存在" in route.detail


def test_file_path_is_unreachable(tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x", encoding="utf-8")
    target = DeliverTarget("nas", file_path, ("nas",))
    route = DeliverTargetRouter([target], tmp_path / "default").route("存到 nas")

    assert route.reason is RouteReason.UNREACHABLE
    assert "不是目录" in route.detail


def test_unwritable_path_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, _ = _target(tmp_path, "nas", aliases=("nas",))
    monkeypatch.setattr(
        "dataagent.pipeline.output.targets.os.access", lambda *args: False
    )
    route = DeliverTargetRouter([target], tmp_path / "default").route("存到 nas")

    assert route.reason is RouteReason.UNREACHABLE
    assert "不可写" in route.detail


def test_targets_from_settings() -> None:
    settings = Settings(
        deliver_targets={
            "nas_finance": {"path": "/mnt/nas/finance", "aliases": ["财报库"]}
        }
    )

    (target,) = targets_from_config(settings.deliver_targets)
    assert target.name == "nas_finance"
    assert target.path == Path("/mnt/nas/finance")
    assert target.aliases == ("财报库",)


def test_route_fields_for_logging(tmp_path: Path) -> None:
    target, path = _target(tmp_path, "nas", aliases=("nas",))
    route = DeliverTargetRouter([target], tmp_path / "default").route("存到 nas")

    fields = route.as_fields()
    assert fields["target"] == "nas"
    assert fields["reason"] == "matched"
    assert fields["output_dir"] == str(path)
    assert fields["detail"] is None
