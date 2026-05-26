"""Unit tests for parse_wiki_imports.parse_sections (DB-free wiki parsing)."""
from parse_wiki_imports import parse_sections

WIKITEXT = """\
前文（セクション外なので無視される）。

=== 埼玉県新座市 ===
全メッシュ 2025-05-07 にインポート完了、2025-05-10 にすべて妥当性検査終了。
{|
| mesh || 2025-05-07 || userA || 検証済
|}

=== 東京都奥多摩町 ===
作業中。まだ完了していない。

=== 高知県高知市 ===
全メッシュ 2024-12-01 にインポート完了。

=== 空セクション ===
"""


def test_parse_sections_classifies_done_in_progress_and_skips_empty():
    assert list(parse_sections(WIKITEXT)) == [
        ("埼玉県新座市", "done", "2025-05-07", True),       # done + validation note
        ("東京都奥多摩町", "in_progress", None, False),     # has body, no completion date
        ("高知県高知市", "done", "2024-12-01", False),       # done, no validation
        # 空セクション (empty body) is skipped entirely
    ]


def test_parse_sections_empty_input():
    assert list(parse_sections("")) == []
