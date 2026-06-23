"""Aggregate Playwright V8 coverage JSON into a per-file line-coverage report.

Each browser test (when run via `make js-browser-coverage`) writes a JSON
file containing V8 coverage records into `$JS_COVERAGE_DIR`. This script
reads them all, filters to the project's own `web/static/*.mjs` files, and
prints `<file>: <covered>/<total> lines (<percent>%)  missing: <ranges>`.

The V8 format is byte-range-based: each `function` carries `ranges` where
each range has a `count`. Innermost (smallest) range wins for any given
byte. A source line is `covered` if at least one non-whitespace byte on
that line is in an innermost range with count > 0.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class _Range:
    start: int
    end: int
    count: int

    @property
    def size(self) -> int:
        return self.end - self.start


def _flatten_ranges(coverage_entry: dict[str, Any]) -> list[_Range]:
    """Pull every range out of every function in one V8 coverage entry."""
    out: list[_Range] = []
    for fn in coverage_entry.get("functions", ()):
        for rng in fn.get("ranges", ()):
            out.append(_Range(rng["startOffset"], rng["endOffset"], rng["count"]))
    return out


def _innermost_count(ranges: Sequence[_Range], offset: int) -> int:
    """Return the count of the smallest range containing `offset`.

    V8 nests ranges (function body, then inner blocks). The innermost
    (smallest by size, breaking ties by latest declaration) wins."""
    best: _Range | None = None
    for r in ranges:
        if r.start <= offset < r.end:
            if best is None or r.size <= best.size:
                best = r
    return 0 if best is None else best.count


def line_coverage(source: str, ranges: Sequence[_Range]) -> list[bool]:
    """Return a per-line boolean: True if at least one non-whitespace byte
    on that line falls inside an executed innermost range."""
    lines = source.split("\n")
    covered = [False] * len(lines)
    offset = 0
    for i, line in enumerate(lines):
        for j, ch in enumerate(line):
            if ch.isspace():
                continue
            if _innermost_count(ranges, offset + j) > 0:
                covered[i] = True
                break
        offset += len(line) + 1  # +1 for the \n
    return covered


def is_code_line(line: str) -> bool:
    """Lines that don't count toward 'total': blanks and comment-only lines.

    Block-comment middle/end lines (` * foo`, ` */`) are also ignored."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("//"):
        return False
    if stripped.startswith(("/*", "*", "*/")):
        return False
    return True


def _summarize(source: str, covered: Sequence[bool]) -> tuple[int, int, list[int]]:
    """Return (covered_count, total_count, missing_line_numbers)."""
    lines = source.split("\n")
    total = 0
    hit = 0
    missing: list[int] = []
    for i, line in enumerate(lines, start=1):
        if not is_code_line(line):
            continue
        total += 1
        if covered[i - 1]:
            hit += 1
        else:
            missing.append(i)
    return hit, total, missing


def _format_missing(line_numbers: Sequence[int]) -> str:
    """Compress consecutive missing line numbers into ranges: [1,2,3,7] → '1-3, 7'."""
    if not line_numbers:
        return ""
    parts: list[str] = []
    start = prev = line_numbers[0]
    for n in line_numbers[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def aggregate_entries(
    coverage_files: Iterable[Path],
    filter_prefix: str = "/static/",
    filter_suffix: str = ".mjs",
) -> dict[str, dict[str, Any]]:
    """Merge V8 coverage from many JSON files keyed by URL path.

    Returns `{path: {"source": str, "ranges": list[_Range]}}` for every
    project file matching the prefix/suffix filter."""
    by_path: dict[str, dict[str, Any]] = {}
    for jf in coverage_files:
        entries = json.loads(jf.read_text())
        for entry in entries:
            url = entry.get("url", "")
            path = _url_path(url)
            if filter_prefix not in path or not path.endswith(filter_suffix):
                continue
            source = entry.get("source")
            if not source:
                continue
            normalized = path[path.index(filter_prefix) :]
            slot = by_path.setdefault(
                normalized, {"source": source, "ranges": []}
            )
            slot["ranges"].extend(_flatten_ranges(entry))
    return by_path


def _url_path(url: str) -> str:
    """Strip scheme+host from a URL, leaving just the path."""
    if "://" not in url:
        return url
    after_scheme = url.split("://", 1)[1]
    return "/" + after_scheme.split("/", 1)[1] if "/" in after_scheme else "/"


def render_report(by_path: dict[str, dict[str, Any]]) -> str:
    """Return a human-readable per-file report as a single string."""
    lines: list[str] = []
    lines.append(f"{'File':<40} {'Lines':>14} {'%':>7}  Missing")
    lines.append("-" * 80)
    total_hit = 0
    total_total = 0
    for path in sorted(by_path):
        slot = by_path[path]
        cov = line_coverage(slot["source"], slot["ranges"])
        hit, total, missing = _summarize(slot["source"], cov)
        pct = (hit / total * 100) if total else 100.0
        lines.append(
            f"{path:<40} {hit:>6}/{total:<6} {pct:>6.1f}%  {_format_missing(missing)}"
        )
        total_hit += hit
        total_total += total
    lines.append("-" * 80)
    overall_pct = (total_hit / total_total * 100) if total_total else 100.0
    lines.append(
        f"{'TOTAL':<40} {total_hit:>6}/{total_total:<6} {overall_pct:>6.1f}%"
    )
    return "\n".join(lines)


def main() -> int:  # pragma: no cover -- integration entrypoint
    in_dir = os.environ.get("JS_COVERAGE_DIR")
    if not in_dir:
        print("JS_COVERAGE_DIR not set", file=sys.stderr)
        return 2
    files = sorted(Path(in_dir).glob("*.json"))
    if not files:
        print(f"no coverage JSON files in {in_dir}", file=sys.stderr)
        return 2
    by_path = aggregate_entries(files)
    if not by_path:
        print("no project files matched the coverage data", file=sys.stderr)
        return 2
    print(render_report(by_path))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
