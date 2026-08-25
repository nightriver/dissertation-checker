from compare.matcher import compare_tokens
from compare.normalize import tokenize_lines
from compare.normative import count_normative_refs, is_normative_token, is_possibly_normative


def _tokens(words):
    return tokenize_lines([{"line": " ".join(words), "page": None}])


def test_normative_regexes_are_case_insensitive_and_overlap_once():
    assert count_normative_refs("Ч. 2 СТ. 365") == 1
    assert count_normative_refs("СТ. 365 та № 1700-vii") == 2


def test_prefixes_are_normative_but_initials_are_not():
    assert is_normative_token("передбачені")
    assert is_normative_token("ВСТАНОВЛЕНО")
    assert not is_normative_token("п")
    assert not is_normative_token("ч")


def test_author_marker_overrides_normative_density():
    words = ["стаття", "закон", "чинного", "пропонуємо"]
    assert not is_possibly_normative(words, " ".join(words))


def test_fifteen_normative_words_remain_as_normative_only_outside_coverage():
    words = (["стаття", "закон", "чинного", "кодекс", "відповідно"] * 3)
    result = compare_tokens(_tokens(words), _tokens(words))
    assert len(result.segments) == 1
    assert result.segments[0].status == "normative_only"
    assert result.covered_tokens_a == 0
    assert result.covered_tokens_a_strict == 0


def test_thirty_normative_words_are_accepted_only_in_main_coverage():
    words = (["стаття", "закон", "чинного", "кодекс", "відповідно"] * 6)
    result = compare_tokens(_tokens(words), _tokens(words))
    assert result.segments[0].status == "accepted_normative"
    assert result.covered_tokens_a == 30
    assert result.covered_tokens_a_strict == 0


def test_normative_words_with_author_marker_are_regular_match():
    words = ["пропонуємо"] + (["стаття", "закон", "чинного", "кодекс"] * 4)
    result = compare_tokens(_tokens(words), _tokens(words))
    assert result.segments[0].status == "accepted"
    assert result.covered_tokens_a_strict == len(words)
