"""Tests for scripts/js_coverage_report.py — the V8-coverage aggregator."""

import json
from pathlib import Path

from scripts.js_coverage_report import (
    _flatten_ranges,
    _format_missing,
    _Range,
    _url_path,
    aggregate_entries,
    is_code_line,
    line_coverage,
    render_report,
)

# ---------------------------------------------------------------------------
# is_code_line — skip blanks, line and block comments
# ---------------------------------------------------------------------------


def test_is_code_line_skips_blank_lines() -> None:
    assert not is_code_line("")
    assert not is_code_line("   ")


def test_is_code_line_skips_line_comments() -> None:
    assert not is_code_line("// foo")
    assert not is_code_line("    // indented")


def test_is_code_line_skips_block_comment_lines() -> None:
    assert not is_code_line("/* open")
    assert not is_code_line(" * middle")
    assert not is_code_line(" */")


def test_is_code_line_keeps_real_code() -> None:
    assert is_code_line("const x = 1;")
    assert is_code_line("    return state;")


# ---------------------------------------------------------------------------
# line_coverage — byte ranges → per-line boolean
# ---------------------------------------------------------------------------


def test_line_coverage_marks_executed_lines() -> None:
    source = "a;\nb;\nc;"
    # Whole file executed (innermost range = whole script, count=1)
    ranges = [_Range(0, len(source), 1)]
    assert line_coverage(source, ranges) == [True, True, True]


def test_line_coverage_marks_uncovered_when_count_zero() -> None:
    source = "a;\nb;\nc;"
    ranges = [_Range(0, len(source), 0)]
    assert line_coverage(source, ranges) == [False, False, False]


def test_line_coverage_innermost_range_wins() -> None:
    """A nested zero-count range carves a hole in an outer executed range."""
    # source: 0..1='a', 2='\n', 3..4='b', 5='\n', 6..7='c'
    source = "a;\nb;\nc;"
    outer = _Range(0, len(source), 1)
    inner_dead = _Range(3, 5, 0)  # covers "b;"
    cov = line_coverage(source, [outer, inner_dead])
    assert cov == [True, False, True]


def test_line_coverage_ignores_whitespace_when_deciding() -> None:
    """Whitespace-only positions don't trigger coverage; need a non-ws hit."""
    source = "    \nx;\n"
    ranges = [_Range(0, len(source), 1)]
    cov = line_coverage(source, ranges)
    # Line 0 is all whitespace; the func still marks it because no non-ws bytes
    # are present at all → loop never triggers covered=True → stays False.
    assert cov[0] is False
    assert cov[1] is True


# ---------------------------------------------------------------------------
# _format_missing — compress consecutive ints into ranges
# ---------------------------------------------------------------------------


def test_format_missing_empty_returns_empty_string() -> None:
    assert _format_missing([]) == ""


def test_format_missing_single_line() -> None:
    assert _format_missing([5]) == "5"


def test_format_missing_runs_compressed_to_ranges() -> None:
    assert _format_missing([1, 2, 3, 7, 10, 11]) == "1-3, 7, 10-11"


def test_format_missing_non_consecutive_lines() -> None:
    assert _format_missing([2, 4, 6, 8]) == "2, 4, 6, 8"


# ---------------------------------------------------------------------------
# _flatten_ranges — extract ranges across functions
# ---------------------------------------------------------------------------


def test_flatten_ranges_collects_across_functions() -> None:
    entry = {
        "functions": [
            {"ranges": [{"startOffset": 0, "endOffset": 10, "count": 1}]},
            {"ranges": [{"startOffset": 5, "endOffset": 7, "count": 0}]},
        ]
    }
    flat = _flatten_ranges(entry)
    assert len(flat) == 2
    assert flat[0].start == 0
    assert flat[0].end == 10
    assert flat[0].count == 1
    assert flat[1].count == 0


# ---------------------------------------------------------------------------
# _url_path — extract path portion from a URL
# ---------------------------------------------------------------------------


def test_url_path_strips_scheme_and_host() -> None:
    assert _url_path("http://127.0.0.1:8000/static/play.mjs") == "/static/play.mjs"


def test_url_path_passthrough_when_no_scheme() -> None:
    assert _url_path("/static/play.mjs") == "/static/play.mjs"


def test_url_path_host_only_returns_root() -> None:
    assert _url_path("http://example.com") == "/"


# ---------------------------------------------------------------------------
# aggregate_entries — merge JSON files, filter, group by path
# ---------------------------------------------------------------------------


def test_aggregate_entries_filters_to_static_mjs(tmp_path: Path) -> None:
    j = tmp_path / "one.json"
    j.write_text(
        json.dumps(
            [
                {
                    "url": "http://localhost/static/play.mjs",
                    "source": "x;",
                    "functions": [
                        {"ranges": [{"startOffset": 0, "endOffset": 2, "count": 1}]}
                    ],
                },
                {
                    "url": "https://cdn.example.com/highlight.min.js",
                    "source": "y;",
                    "functions": [],
                },
                {
                    "url": "http://localhost/static/style.css",
                    "source": "z;",
                    "functions": [],
                },
            ]
        )
    )
    out = aggregate_entries([j])
    assert list(out) == ["/static/play.mjs"]
    assert out["/static/play.mjs"]["source"] == "x;"


def test_aggregate_entries_merges_ranges_from_multiple_files(tmp_path: Path) -> None:
    def make(p: Path, count: int, start: int, end: int) -> None:
        p.write_text(
            json.dumps(
                [
                    {
                        "url": "http://x/static/play.mjs",
                        "source": "a;\nb;\nc;",
                        "functions": [
                            {
                                "ranges": [
                                    {
                                        "startOffset": start,
                                        "endOffset": end,
                                        "count": count,
                                    }
                                ]
                            }
                        ],
                    }
                ]
            )
        )

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    make(a, 1, 0, 2)
    make(b, 1, 6, 8)
    out = aggregate_entries([a, b])
    assert len(out["/static/play.mjs"]["ranges"]) == 2


def test_aggregate_entries_skips_entries_without_source(tmp_path: Path) -> None:
    j = tmp_path / "no-source.json"
    j.write_text(
        json.dumps(
            [
                {
                    "url": "http://x/static/play.mjs",
                    "functions": [],
                }
            ]
        )
    )
    assert aggregate_entries([j]) == {}


# ---------------------------------------------------------------------------
# render_report — produces a readable per-file table
# ---------------------------------------------------------------------------


def test_render_report_includes_per_file_and_total_rows() -> None:
    by_path = {
        "/static/play.mjs": {
            "source": "a;\nb;\nc;",
            "ranges": [_Range(0, 8, 1)],
        },
    }
    out = render_report(by_path)
    assert "/static/play.mjs" in out
    assert "TOTAL" in out
    assert "3/3" in out
    assert "100.0%" in out


def test_render_report_shows_missing_lines_for_uncovered_code() -> None:
    by_path = {
        "/static/play.mjs": {
            "source": "a;\nb;\nc;",
            "ranges": [_Range(0, 2, 1), _Range(2, 8, 0)],
        },
    }
    out = render_report(by_path)
    # Line 1 covered, lines 2-3 missing
    assert "1/3" in out
    assert "2-3" in out


def test_render_report_skips_blank_and_comment_lines_from_total() -> None:
    """Blank lines and comment-only lines must not count toward the total
    (or appear in `missing`)."""
    source = "// header comment\n\na;\n// b is intentionally skipped\nc;"
    # All bytes covered.
    by_path = {
        "/static/x.mjs": {
            "source": source,
            "ranges": [_Range(0, len(source), 1)],
        },
    }
    out = render_report(by_path)
    # Only `a;` and `c;` count → 2/2.
    assert "2/2" in out
