"""错误码封闭集测试（T009，A2）。

宪法：缺陷码是封闭集——枚举穷尽、无运行时字符串字面量、新增码必须走版本升级。
"""

import ast
from pathlib import Path

import pytest

from dataagent.contracts.errors import DefectCode

# 逐字引用 contracts/defect-codes.md 的 11 个缺陷码
EXPECTED_CODES = [
    "encoding_loss",
    "low_density",
    "region_empty",
    "table_structure_broken",
    "table_cross_page_break",
    "reading_order_broken",
    "column_mixed",
    "header_footer_leak",
    "page_rotated",
    "page_number_broken",
    "plugin_reason_unknown",
]


def _repo_root() -> Path:
    p = Path(__file__).resolve()
    while not (p / "pyproject.toml").is_file():
        p = p.parent
    return p


def _code_string_literals(path: Path) -> set[str]:
    """返回源码中的字符串字面量（排除模块 / 函数 / 类的 docstring）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            docstring_ids.add(id(node.body[0].value))
    literals: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        ):
            literals.add(node.value)
    return literals


def test_enum_exhaustive() -> None:
    """枚举穷尽：与 defect-codes.md 的 11 码逐字一致。"""
    assert len(DefectCode) == 11
    assert {code.value for code in DefectCode} == set(EXPECTED_CODES)


def test_no_duplicate_values() -> None:
    values = [code.value for code in DefectCode]
    assert len(values) == len(set(values))


def test_unknown_code_rejected() -> None:
    """封闭集：集合外字符串无法构造缺陷码。"""
    with pytest.raises(ValueError):
        DefectCode("not_a_defect_code")


def test_new_code_requires_version_upgrade() -> None:
    """新增码必须走版本升级：与契约码表不一致即失败（此测试即契约校验点）。"""
    assert sorted(code.value for code in DefectCode) == sorted(EXPECTED_CODES)


def test_no_raw_defect_code_string_literals_outside_contracts() -> None:
    """无运行时字符串字面量：业务代码不得硬编码缺陷码字符串，必须用 DefectCode 枚举。"""
    root = _repo_root()
    src = root / "src" / "dataagent"
    allowed_files = {"errors.py", "defects.py"}
    values = {code.value for code in DefectCode}
    offenders: list[tuple[str, str]] = []
    for path in sorted(src.rglob("*.py")):
        if path.name in allowed_files:
            continue
        for literal in sorted(_code_string_literals(path) & values):
            offenders.append((str(path.relative_to(root)), literal))
    assert not offenders, f"缺陷码以字符串字面量散落出现（必须用 DefectCode 枚举）: {offenders}"
