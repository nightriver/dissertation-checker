from compare.biblio_match import compare_bibliographies
from compare.matcher import compare_documents


def _entry(author, number, title=None):
    title = title or f"Унікальна наукова праця номер {number}"
    return f"{author} А. А. {title}. Київ, 2019."


def test_exact_and_near_bibliographic_matches_are_separate():
    a = {1: _entry("Іванов", 1), 2: _entry("Петренко", 2, "Цифрова трансформація управління")}
    b = {1: _entry("Іванов", 1), 2: _entry("Петренко", 2, "Цифрова трансформація управліня")}
    result = compare_bibliographies(a, b)
    assert result.common_exact == 1
    assert result.common_near == 1


def test_three_shared_consecutive_entries_signal_order_for_unsorted_lists():
    authors = ["Яремчук", "Коваль", "Абрамов", "Петренко"]
    a = {index: _entry(author, index) for index, author in enumerate(authors, 1)}
    b = dict(a)
    result = compare_bibliographies(a, b)
    assert result.order_signal_applicable
    assert result.order_runs == (4,)


def test_order_signal_is_disabled_when_both_lists_are_alphabetical():
    authors = ["Абрамов", "Коваль", "Петренко", "Яремчук"]
    entries = {index: _entry(author, index) for index, author in enumerate(authors, 1)}
    result = compare_bibliographies(entries, entries)
    assert not result.order_signal_applicable
    assert result.order_runs == ()


def test_unparsed_bibliography_is_reported_not_silently_empty():
    result = compare_documents(
        [{"line": "Текст без списку", "page": None}],
        [{"line": "Інший текст", "page": None}],
    )
    assert result.biblio is not None
    assert not result.biblio.parsed_a
    assert not result.biblio.parsed_b


def test_twenty_shared_entries_are_compared_even_without_text_matches():
    entries = [_entry(f"Автор{index}", index) for index in range(1, 21)]
    lines_a = [{"line": "Лише лівий основний текст", "page": None}, {"line": "СПИСОК ЛІТЕРАТУРИ", "page": None}]
    lines_b = [{"line": "Лише правий основний текст", "page": None}, {"line": "СПИСОК ЛІТЕРАТУРИ", "page": None}]
    for index, entry in enumerate(entries, 1):
        lines_a.append({"line": f"{index}. {entry}", "page": None})
        lines_b.append({"line": f"{index}. {entry}", "page": None})
    result = compare_documents(lines_a, lines_b)
    assert result.segments == []
    assert result.biblio and result.biblio.common_exact == 20
    assert any(item.reason == "bibliography" for item in result.excluded_a)
