"""全局测试夹具（T006）：确定性随机种子、临时目录、确定性样本文件。

宪法 I：同一输入必须产生同一产物，可复现是验收前提——测试同样必须确定性。
"""

import os
import random
from pathlib import Path

import pytest

FIXED_SEED = int(os.environ.get("DATAAGENT_TEST_SEED", "20261031"))


@pytest.fixture(autouse=True)
def _deterministic_seed() -> None:
    """每个测试开始前重设随机种子，避免用例间状态污染。"""
    random.seed(FIXED_SEED)
    yield


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """独立临时数据目录，测试结束自动清理。"""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def sample_files(data_dir: Path) -> dict[str, Path]:
    """固定内容的确定性样本文件（内容哈希可复现，供后续阶段复用）。"""
    samples: dict[str, Path] = {}
    for name, content in {
        "tiny.txt": "hello dataagent\n",
        "simple.csv": "a,b,c\n1,2,3\n4,5,6\n",
        "empty.txt": "",
    }.items():
        p = data_dir / name
        p.write_text(content, encoding="utf-8")
        samples[name] = p
    return samples
