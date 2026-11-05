"""layout_probe 适配器测试：按页门控、页码透传、DTO 转换。"""

from __future__ import annotations

from types import SimpleNamespace

import dataagent.platform.plugins.adapters.layout_probe as module
from dataagent.platform.plugins.adapters.layout_probe import probe_layout


def test_positions_and_roles_are_converted(monkeypatch) -> None:
    calls: dict[str, list[int]] = {}

    def fake_positions(path, pages=None, **kwargs):
        calls["items"] = list(pages or [])
        return [
            SimpleNamespace(
                text="项", page=2, x=1.0, y=2.0, width=3.0, height=4.0, mcid=9
            ),
            SimpleNamespace(
                text="目", page=3, x=5.0, y=6.0, width=7.0, height=8.0, mcid=None
            ),
        ]

    def fake_roles(path, pages=None, **kwargs):
        calls["roles"] = list(pages or [])
        return [SimpleNamespace(page=2, mcid=9, role="H2")]

    monkeypatch.setattr(module.pdf_inspector, "extract_text_with_positions", fake_positions)
    monkeypatch.setattr(module.pdf_inspector, "extract_structure_elements", fake_roles)

    layout = probe_layout("doc.pdf", item_pages=[2, 3], role_pages=[1, 2, 3])

    assert calls == {"items": [2, 3], "roles": [1, 2, 3]}
    assert layout.items[0].text == "项"
    assert layout.items[0].mcid == 9
    assert layout.items[1].mcid is None
    assert layout.roles[0].role == "H2"
    assert layout.roles[0].page == 2


def test_empty_item_pages_skip_positions(monkeypatch) -> None:
    called = {"items": False, "roles": False}

    def fake_positions(*args, **kwargs):
        called["items"] = True
        return []

    def fake_roles(*args, **kwargs):
        called["roles"] = True
        return []

    monkeypatch.setattr(module.pdf_inspector, "extract_text_with_positions", fake_positions)
    monkeypatch.setattr(module.pdf_inspector, "extract_structure_elements", fake_roles)

    probe_layout("doc.pdf", item_pages=[], role_pages=[1])

    assert called == {"items": False, "roles": True}


def test_bytes_source_uses_bytes_variants(monkeypatch) -> None:
    used: list[str] = []

    def fake_positions(data, pages=None, **kwargs):
        used.append("positions")
        return []

    def fake_roles(data, pages=None, **kwargs):
        used.append("roles")
        return []

    monkeypatch.setattr(
        module.pdf_inspector, "extract_text_with_positions_bytes", fake_positions
    )
    monkeypatch.setattr(
        module.pdf_inspector, "extract_structure_elements_bytes", fake_roles
    )

    probe_layout(b"%PDF", item_pages=[1], role_pages=[1])

    assert used == ["positions", "roles"]
