"""一级判定性能断言（T080，B2）。

单文件判定 ≤ 50ms，且最多读取前 256 KB（FR-003）。
"""

import time
from pathlib import Path

from dataagent.pipeline.sniff.sniffer import SAMPLE_LIMIT_BYTES, sniff_file


def make_big_csv(path: Path) -> None:
    header = "name,amount,date\n"
    row = "row,1,2026-01-01\n"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(header)
        while handle.tell() < 2 * 1024 * 1024:
            handle.write(row)


def test_reads_at_most_256kb(tmp_path: Path) -> None:
    path = tmp_path / "big.csv"
    make_big_csv(path)
    result = sniff_file(path)

    assert path.stat().st_size > SAMPLE_LIMIT_BYTES
    assert result.bytes_read == SAMPLE_LIMIT_BYTES


def test_single_file_sniff_within_50ms(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_bytes(("a,b,c\n1,2,3\n" * 2000).encode())

    durations: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        sniff_file(path)
        durations.append(time.perf_counter() - started)

    assert max(durations) < 0.050, f"一级判定超时：{durations}"
