"""
Дублікати у списку літератури за полем назви.

Чисті функції над словниками — PyMuPDF і python-docx тут не потрібні.
"""

from difflib import SequenceMatcher

from parser.duplicates import extract_title, find_duplicates
from parser.types import Severity


# ---------------------------------------------------------------------------
# extract_title — чотири форми ДСТУ
# ---------------------------------------------------------------------------

def test_title_author_first_with_colon_separator():
    title, method = extract_title(
        "Іванов І. І. Цифрова трансформація публічного управління : монографія. "
        "Київ : Наукова думка, 2019. 320 с."
    )
    assert method == "dstu"
    assert title == "цифрова трансформація публічного управління"


def test_title_first_with_slash_separator():
    title, method = extract_title(
        "Правове регулювання цифрових платформ / О. П. Петренко. Львів, 2020. 210 с."
    )
    assert method == "dstu"
    assert title == "правове регулювання цифрових платформ"


def test_article_with_double_slash():
    title, method = extract_title(
        "Коваль С. М. Регулювання цифрових ринків // Вісник права. 2019. № 3. С. 12-18."
    )
    assert method == "dstu"
    assert title == "регулювання цифрових ринків"


def test_article_without_double_slash_cuts_at_sentence_break():
    title, method = extract_title(
        "Коваль С. М. Регулювання цифрових ринків. Вісник права. 2019. № 3. С. 12-18."
    )
    assert method == "dstu"
    assert title == "регулювання цифрових ринків"


def test_uppercase_surname_is_stripped():
    title, method = extract_title(
        "ПЕТРЕНКО О. П. Адміністративне судочинство України. Київ, 2018. 400 с."
    )
    assert method == "dstu"
    assert title == "адміністративне судочинство україни"


def test_latin_author_is_stripped():
    title, method = extract_title(
        "Smith J. A. Digital markets regulation in Europe. London, 2021. 180 p."
    )
    assert method == "dstu"
    assert title.startswith("digital markets regulation")


def test_garbage_entry_falls_back_to_full_method():
    title, method = extract_title("12345 --- ???")
    assert method == "full"
    assert title == "12345"


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------

def test_exact_duplicate_is_found():
    biblio = {
        1: "Іванов І. І. Цифрова економіка України : монографія. Київ, 2019. 300 с.",
        2: "Іванов І. І. Цифрова економіка України : підручник. Львів, 2019. 300 с.",
    }
    groups = find_duplicates(biblio, {1: 2019, 2: 2019})
    assert len(groups) == 1
    assert groups[0].numbers == [1, 2]
    assert groups[0].kind == "exact"
    assert groups[0].severity is Severity.SUSPECT
    assert groups[0].similarity == 1.0


def test_same_title_different_year_is_flagged_separately():
    biblio = {
        1: "Іванов І. І. Цифрова економіка України : монографія. Київ, 2019. 300 с.",
        2: "Іванов І. І. Цифрова економіка України : монографія. Київ, 2021. 300 с.",
    }
    groups = find_duplicates(biblio, {1: 2019, 2: 2021})
    assert len(groups) == 1
    assert groups[0].kind == "same_title_diff_year"


def test_two_close_articles_by_same_author_are_not_duplicates():
    """
    Регресійний кейс 88/90: «цифрових платформ» проти «цифрових ринків».
    Обидві назви проходять передфільтр і доходять до SequenceMatcher —
    саме там вони й мають розійтись, із запасом до порогу 0.90.
    """
    entry_a = "Коваль С. М. Регулювання цифрових ринків // Вісник права. 2019. № 3. С. 12-18."
    entry_b = "Коваль С. М. Регулювання цифрових платформ // Вісник права. 2019. № 4. С. 20-28."

    ratio = SequenceMatcher(
        None, extract_title(entry_a)[0], extract_title(entry_b)[0]
    ).ratio()
    assert 0.70 <= ratio < 0.85, f"схожість зсунулась: {ratio:.3f}"

    assert find_duplicates({88: entry_a, 90: entry_b}, {88: 2019, 90: 2019}) == []


def test_near_duplicate_is_found():
    """Той самий запис із однією зміненою літерою в назві."""
    biblio = {
        1: "Іванов І. І. Цифрова трансформація публічного управління // Вісник. 2019. № 3.",
        2: "Іванов І. І. Цифрова трансформация публічного управління // Вісник. 2019. № 3.",
    }
    groups = find_duplicates(biblio, {1: 2019, 2: 2019})
    assert len(groups) == 1
    assert groups[0].kind == "near"
    assert 0.90 <= groups[0].similarity < 1.0


def test_transitive_group_of_three():
    """12≈187 і 187≈45 → одна група [12, 45, 187], а не три пари."""
    biblio = {
        12: "Іванов І. І. Цифрова економіка України : монографія. Київ, 2019. 300 с.",
        45: "Іванов І. І. Цифрова економіка України / за ред. О. Петренка. Київ, 2019.",
        187: "Цифрова економіка України : навч. посіб. Львів, 2019. 250 с.",
    }
    groups = find_duplicates(biblio, {12: 2019, 45: 2019, 187: 2019})
    assert len(groups) == 1
    assert groups[0].numbers == [12, 45, 187]


def test_empty_bibliography_gives_no_groups():
    assert find_duplicates({}, {}) == []


def test_single_entry_gives_no_groups():
    assert find_duplicates({1: "Іванов І. І. Право. Київ, 2019."}, {1: 2019}) == []


def test_prefilter_keeps_real_duplicate_with_different_entry_length():
    """Записи різної довжини, але однакова назва — передфільтр не заважає."""
    biblio = {
        1: "Цифрова економіка України : монографія. Київ, 2019.",
        2: (
            "Цифрова економіка України : монографія / за заг. ред. І. І. Іванова ; "
            "НАН України, Інститут економіки. 2-ге вид., перероб. і доп. "
            "Київ : Наукова думка, 2019. 480 с. : іл., табл. Бібліогр.: с. 460-478."
        ),
    }
    groups = find_duplicates(biblio, {1: 2019, 2: 2019})
    assert len(groups) == 1
    assert groups[0].numbers == [1, 2]


def test_two_garbage_entries_are_not_near_duplicates():
    """
    Обидва записи — метод 'full'. У повному записі левову частку рядка
    займають службові поля ДСТУ, однакові в усіх статтях одного журналу,
    тож near-порівняння для них вимкнено попри високий ratio.
    """
    entry_a = "??? // Вісник Київського університету. 2019. № 33. С. 12-18."
    entry_b = "!!! // Вісник Київського університету. 2019. № 44. С. 20-28."

    assert extract_title(entry_a)[1] == "full"
    assert extract_title(entry_b)[1] == "full"
    ratio = SequenceMatcher(
        None, extract_title(entry_a)[0], extract_title(entry_b)[0]
    ).ratio()
    assert ratio >= 0.90, f"кейс втратив сенс: ratio={ratio:.3f}"

    assert find_duplicates({1: entry_a, 2: entry_b}, {1: 2019, 2: 2019}) == []


def test_two_identical_garbage_entries_are_still_found():
    """Метод 'full' бере участь у ТОЧНОМУ порівнянні — і там дубль видно."""
    entry = "??? // Вісник Київського університету. 2019. № 33. С. 12-18."
    groups = find_duplicates({1: entry, 2: entry}, {1: 2019, 2: 2019})
    assert len(groups) == 1
    assert groups[0].kind == "exact"


def test_unrelated_entries_give_no_groups():
    biblio = {
        1: "Іванов І. І. Цифрова економіка України. Київ, 2019. 300 с.",
        2: "Петренко О. П. Адміністративне судочинство. Львів, 2018. 400 с.",
        3: "Коваль С. М. Кримінальне право: загальна частина. Одеса, 2020. 500 с.",
    }
    assert find_duplicates(biblio, {1: 2019, 2: 2018, 3: 2020}) == []


# ---------------------------------------------------------------------------
# Крайні випадки та внутрішні інваріанти
# ---------------------------------------------------------------------------

def test_entry_without_meaningful_tokens_is_skipped():
    """Запис, від якого після нормалізації нічого не лишилось, не порівнюється."""
    biblio = {
        1: "—",
        2: "Іванов І. І. Цифрова економіка України. Київ, 2019. 300 с.",
        3: "Іванов І. І. Цифрова економіка України. Львів, 2019. 300 с.",
        4: "-",
    }
    groups = find_duplicates(biblio, {1: None, 2: 2019, 3: 2019, 4: None})
    assert len(groups) == 1
    assert groups[0].numbers == [2, 3]


def test_length_prefilter_rejects_very_different_titles():
    """Назва вчетверо довша — до SequenceMatcher справа не доходить."""
    biblio = {
        1: "Цифрова економіка держави",
        2: (
            "Цифрова економіка держави як предмет наукового пізнання в умовах "
            "глобальної трансформації національних господарських систем"
        ),
    }
    assert find_duplicates(biblio, {1: 2019, 2: 2019}) == []


def test_jaccard_prefilter_rejects_titles_without_common_tokens():
    biblio = {
        1: "Адміністративне судочинство держави",
        2: "Кримінальна відповідальність посадовця",
    }
    assert find_duplicates(biblio, {1: 2019, 2: 2019}) == []


def test_union_find_keeps_transitivity_through_deep_chains():
    """Регресія на стиснення шляхів: злиття не має губити членів групи."""
    from parser.duplicates import _UnionFind

    union = _UnionFind()
    union.union(1, 3)   # 3 → 1
    union.union(2, 3)   # 1 → 2, тобто ланцюг 3 → 1 → 2
    assert union.find(3) == union.find(2) == union.find(1)
