"""Phase 1 骨架冒烟测试：5 层包结构可导入（T001）。"""

import importlib


def test_five_layer_skeleton_imports() -> None:
    for name in ("contracts", "platform", "kernel", "pipeline", "apps"):
        mod = importlib.import_module(f"dataagent.{name}")
        assert mod.__name__.startswith("dataagent.")


def test_version_exposed() -> None:
    import dataagent

    assert dataagent.__version__ == "0.1.0"
