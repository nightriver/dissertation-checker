from compare import params
from compare.matcher import (
    Candidate,
    Seed,
    align_candidate,
    candidate_word,
    chain_seeds,
    compare_tokens,
    coverage_from_segments,
    deduplicate_segments,
    split_candidate,
    find_candidates,
    merge_candidates,
    Fingerprint,
    _add_frequent_seeds,
)
from compare.normalize import tokenize_lines


def _tokens(text):
    return tokenize_lines([{"line": text, "page": None}])


def test_exact_fifteen_word_fragment_is_verbatim():
    common = " ".join(f"слово{i}" for i in range(15))
    result = compare_tokens(_tokens("ліворуч " + common), _tokens("праворуч " + common))
    assert len(result.segments) == 1
    assert result.segments[0].kind == "verbatim"
    assert result.segments[0].longest_verbatim == 15


def test_eight_word_fragment_is_rejected():
    common = " ".join(f"слово{i}" for i in range(8))
    assert compare_tokens(_tokens(common), _tokens(common)).segments == []


def test_replace_and_insert_are_modified_and_colored():
    base = [f"термін{i}" for i in range(32)]
    changed = base[:12] + ["терміна12"] + base[13:20] + ["вставка"] + base[20:]
    result = compare_tokens(_tokens(" ".join(base)), _tokens(" ".join(changed)))
    segment = result.segments[0]
    assert segment.kind == "modified"
    assert segment.fuzzy_matched >= 1
    assert "fuzzy" in {span.operation for span in segment.a_spans}
    assert "insert" in {span.operation for span in segment.b_spans}
    assert segment.matched < segment.len_a


def test_multiple_replacements_still_form_one_modified_fragment():
    base = [f"поняття{index}" for index in range(45)]
    changed = list(base)
    changed[12] = "понятій12"
    changed[25] = "інший25"
    changed[34] = "понятій34"
    result = compare_tokens(_tokens(" ".join(base)), _tokens(" ".join(changed)))
    assert len(result.segments) == 1
    assert result.segments[0].kind == "modified"


def test_short_word_uses_seventy_percent_fuzzy_threshold():
    a = _tokens("море")
    b = _tokens("гора")
    candidate = Candidate(0, 1, 0, 1, 2)
    # Сам кандидат короткий і не приймається, але внутрішній fuzzy не може
    # самостійно підняти його вище нижнього порога.
    assert align_candidate(candidate, a, b) is None


def test_seed_gap_eighty_starts_another_chain():
    chains = chain_seeds([Seed(0, 0), Seed(10, 10), Seed(90, 90), Seed(100, 100)])
    assert chains == [[Seed(0, 0), Seed(10, 10)], [Seed(90, 90), Seed(100, 100)]]


def test_candidate_3200_is_split_with_200_overlap():
    chunks = split_candidate(Candidate(0, 3200, 0, 3200, 10))
    assert [(chunk.a_start, chunk.a_end) for chunk in chunks] == [(0, 3000), (2800, 3200)]


def test_overlapping_candidates_are_merged_before_alignment():
    merged = merge_candidates([
        Candidate(0, 30, 10, 40, 3),
        Candidate(25, 50, 35, 60, 4),
    ])
    assert merged == [Candidate(0, 50, 10, 60, 7)]


def test_frequent_fingerprint_enriches_but_does_not_create_chain():
    digest = b"x" * 32
    fingerprints = [Fingerprint(digest, 5)]
    postings = {digest: list(range(params.MAX_FINGERPRINT_POSTINGS))}
    assert _add_frequent_seeds([], fingerprints, postings) == []
    enriched = _add_frequent_seeds([[Seed(0, 0), Seed(10, 10)]], fingerprints, postings)
    assert enriched == [[Seed(0, 0), Seed(5, 5), Seed(10, 10)]]


def test_acceptance_thresholds_live_in_params():
    assert (
        params.MIN_MATCHED_LOW,
        params.MIN_SIMILARITY,
        params.MIN_VERBATIM_LOW,
        params.MIN_MATCHED_HIGH,
        params.MIN_VERBATIM_HIGH,
    ) == (25, 0.45, 15, 40, 30)


def test_truncated_stems_are_available_but_disabled():
    assert params.USE_TRUNCATED_STEMS is False
    assert candidate_word("відповідальність") != candidate_word("відповідальності")
    assert candidate_word("відповідальність", True) == candidate_word("відповідальності", True)


def test_nested_segments_are_deduplicated_and_coverage_is_union():
    tokens = _tokens(" ".join(f"слово{i}" for i in range(40)))
    outer = align_candidate(Candidate(0, 40, 0, 40, 5), tokens, tokens)
    inner = align_candidate(Candidate(5, 35, 5, 35, 4), tokens, tokens)
    assert outer and inner
    assert deduplicate_segments([inner, outer]) == [outer]
    assert coverage_from_segments([outer, inner], "a") == 40


def test_candidate_limit_is_reported_after_ranking(monkeypatch):
    first = [f"перший{index}" for index in range(20)]
    second = [f"другий{index}" for index in range(20)]
    filler_a = [f"лівий{index}" for index in range(100)]
    filler_b = [f"правий{index}" for index in range(100)]
    tokens_a = _tokens(" ".join(first + filler_a + second))
    tokens_b = _tokens(" ".join(first + filler_b + second))
    monkeypatch.setattr(params, "MAX_CHAINS", 1)
    candidates, total = find_candidates(tokens_a, tokens_b)
    assert total >= 2
    assert len(candidates) == 1
    result = compare_tokens(tokens_a, tokens_b)
    assert not result.analysis_complete
    assert result.candidates_processed == 1
    assert result.candidates_total == total
