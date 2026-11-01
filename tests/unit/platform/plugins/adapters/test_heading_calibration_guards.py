"""标题标定质量护栏测试（T133 补充）：长标题 / 合并标题不得注入。"""

from types import SimpleNamespace

from dataagent.contracts.artifacts import PageArtifact
from dataagent.platform.plugins.adapters import heading_calibration
from dataagent.platform.plugins.adapters.heading_calibration import calibrate_headings


def artifact(page_no: int, markdown: str) -> PageArtifact:
    return PageArtifact(
        doc_id="a" * 64,
        page_no=page_no,
        engine="engine.pdf@1.0.0",
        params_hash="params",
        markdown=markdown,
    )


def test_merged_and_overlong_headings_are_skipped(monkeypatch) -> None:
    merged = "第一章 甲 第一节 乙 一、丙"
    overlong = "很长的正文句子，包含多个自然段内容，不应被当成标题。"
    artifacts = (
        artifact(1, merged + "\n正文\n"),
        artifact(2, overlong + "\n"),
    )

    def fake_process(path: str, pages: list[int] | None = None):
        return SimpleNamespace(markdown=f"# {merged}\n\n正文\n# {overlong}\n")

    monkeypatch.setattr(
        heading_calibration.pdf_inspector, "process_pdf", fake_process
    )

    calibrated, outcome = calibrate_headings("book.pdf", artifacts)

    assert outcome.applied is False
    assert calibrated == artifacts
