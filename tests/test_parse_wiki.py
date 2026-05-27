"""Unit tests for parse_wiki_imports (DB-free): wiki parsing + name->code resolution."""
from parse_wiki_imports import parse_sections, resolve_city_code

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


def test_resolve_city_code():
    lookup = {"埼玉県新座市": "11230", "大阪府大阪市": "27100"}
    assert resolve_city_code("埼玉県新座市", lookup) == "11230"      # exact
    assert resolve_city_code("大阪府大阪市北区", lookup) == "27100"  # designated-city ward -> parent
    assert resolve_city_code("どこかの村", lookup) is None           # no match


def test_resolve_city_code_normalizes_variations():
    lookup = {"神奈川県茅ヶ崎市": "14207", "神奈川県横浜市": "14100"}
    assert resolve_city_code("神奈川県茅ケ崎市", lookup) == "14207"        # ヶ vs ケ
    assert resolve_city_code("神奈川県　茅ヶ崎市", lookup) == "14207"      # full-width space (NFKC + strip)
    assert resolve_city_code("神奈川県横浜市 西区", lookup) == "14100"     # ward fallback + whitespace
