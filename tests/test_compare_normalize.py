from compare.normalize import normalize_search_token, tokenize_lines
from compare.types import CompareToken, TokenPart
from parser.text_forensics import normalize_mixed_homoglyphs


def test_nfkc_case_invisible_and_mixed_homoglyphs():
    assert normalize_search_token("ＡДМ\u200bІНIСТРАТИВНА") == "адміністративна"


def test_plain_english_is_not_changed_by_homoglyph_normalizer():
    assert normalize_mixed_homoglyphs("pacta sunt servanda") == "pacta sunt servanda"
    assert normalize_mixed_homoglyphs("Адмiнiстративна") == "Адміністративна"


def test_token_keeps_original_coordinates_and_page():
    tokens = tokenize_lines([{"line": "  Право, закон!", "page": 7}])
    assert [(token.raw, token.normalized) for token in tokens] == [
        ("Право", "право"), ("закон", "закон")
    ]
    assert tokens[0].parts == (TokenPart(0, 2, 7, 7),)
    assert tokens[0].physical_pages == (7,)


def test_line_break_hyphen_is_joined_with_two_parts_and_pages():
    tokens = tokenize_lines([
        {"line": "кримінальна відпові-", "page": 1},
        {"line": "дальність настає", "page": 2},
    ])
    joined = tokens[1]
    assert joined.raw == "відповідальність"
    assert joined.normalized == "відповідальність"
    assert len(joined.parts) == 2
    assert joined.physical_pages == (1, 2)


def test_hyphen_inside_one_line_is_not_joined():
    tokens = tokenize_lines([{"line": "науково-дослідний метод", "page": None}])
    assert [token.normalized for token in tokens] == ["науково", "дослідний", "метод"]


def test_compare_token_deduplicates_repeated_page_numbers():
    token = CompareToken("слово", "слово", (
        TokenPart(0, 0, 2, 3), TokenPart(1, 0, 3, 3),
    ))
    assert token.physical_pages == (3,)
