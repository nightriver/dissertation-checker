"""
Гомогліфи: підміна кириличних літер візуально однаковими латинськими.

Чисті функції над рядками — PyMuPDF і python-docx тут не потрібні.
Сім кейсів із розділу «перевірка правил на пастках» перенесено дослівно.
"""

from parser.text_forensics import restore_word, scan_text_forensics
from parser.types import Severity


def _pdf_lines(*texts, page=1):
    return [{"line": text, "page": page} for text in texts]


def _docx_lines(*texts):
    return [{"line": text, "page": None} for text in texts]


# ---------------------------------------------------------------------------
# Пастки: сім кейсів прототипу
# ---------------------------------------------------------------------------

def test_clean_text_gives_no_hits():
    result = scan_text_forensics(_pdf_lines(
        "Адміністративна відповідальність юридичних осіб у сфері екології",
        "Дослідження проведено на матеріалах судової практики за 2019 рік.",
    ))
    assert result.hits == []
    assert result.affected_pct == 0.0


def test_substitution_inside_words_is_found():
    """Латинська «i» всередині кириличних слів."""
    result = scan_text_forensics(_pdf_lines(
        "Адмiнiстративна вiдповiдальнiсть юридичних осіб"
    ))
    words = [hit.word for hit in result.hits]
    assert words == ["Адмiнiстративна", "вiдповiдальнiсть"]
    assert all(hit.rule == "mixed" for hit in result.hits)
    assert all(hit.severity is Severity.PROOF for hit in result.hits)
    assert result.hits[0].restored == "Адміністративна"


def test_lone_latin_i_is_found():
    result = scan_text_forensics(_pdf_lines("держава i право становлять систему"))
    assert [(h.word, h.restored, h.rule) for h in result.hits] == [("i", "і", "lone")]


def test_hyphenated_latin_prefixes_are_clean():
    """Розбиття по дефісу: IT-технології, e-mail, COVID-19, Web-сайт."""
    result = scan_text_forensics(_pdf_lines(
        "Розвиток IT-технологій та Web-сайтів установи",
        "Контакти надсилайте на e-mail протягом доби",
        "Наслідки COVID-19 для економіки регіону",
    ))
    assert result.hits == []


def test_enumeration_with_latin_letters_is_clean():
    """Запобіжник на дужку відсікає перелік «a) b)»."""
    result = scan_text_forensics(_pdf_lines(
        "Умови поділяються на групи: a) правові умови; b) організаційні умови."
    ))
    assert result.hits == []


def test_latin_quotation_is_clean():
    """Суцільна латиниця не змішана з кирилицею."""
    result = scan_text_forensics(_pdf_lines(
        "Принцип pacta sunt servanda є основою міжнародного права"
    ))
    assert result.hits == []


def test_uppercase_heading_substitution_is_found():
    """Латинська «I» у заголовку великими літерами."""
    result = scan_text_forensics(_pdf_lines(
        "РОЗДIЛ 1. ТЕОРЕТИЧНI ЗАСАДИ ДЕРЖАВНОГО УПРАВЛIННЯ"
    ))
    assert [h.word for h in result.hits] == ["РОЗДIЛ", "ТЕОРЕТИЧНI", "УПРАВЛIННЯ"]
    assert result.hits[0].restored == "РОЗДІЛ"


def test_lone_x_never_fires():
    """x, c, p виключені повністю — це надто поширені математичні змінні."""
    result = scan_text_forensics(_pdf_lines(
        "Нехай x дорівнює 5, тоді змінна c більша за змінну p відповідно"
    ))
    assert result.hits == []


# ---------------------------------------------------------------------------
# Додаткові правила
# ---------------------------------------------------------------------------

def test_latin_i_with_diaeresis_is_found():
    result = scan_text_forensics(_pdf_lines("Розвиток украïнської державності"))
    assert [h.word for h in result.hits] == ["украïнської"]
    assert result.hits[0].restored == "української"


def test_greek_homoglyph_is_found():
    result = scan_text_forensics(_pdf_lines("Дослідження прοблеми регіону"))
    assert [h.restored for h in result.hits] == ["проблеми"]


def test_contextual_lone_letter_is_silent_below_threshold():
    """y/o/a/e мовчать, поки H1 дало менше 3 знахідок."""
    result = scan_text_forensics(_pdf_lines(
        "Адмiнiстративна норма a також інша норма діють разом"
    ))
    assert [h.word for h in result.hits] == ["Адмiнiстративна"]


def test_contextual_lone_letter_fires_above_threshold():
    """Три знахідки H1 доводять підміну — вмикаються й контекстні літери."""
    result = scan_text_forensics(_pdf_lines(
        "Адмiнiстративна вiдповiдальнiсть посадовоï особи",
        "норма a також діє",
    ))
    words = [h.word for h in result.hits]
    assert len([w for w in words if len(w) > 1]) == 3
    assert "a" in words


def test_restore_word_replaces_every_homoglyph():
    assert restore_word("Адмiнiстративна") == "Адміністративна"
    assert restore_word("чистий") == "чистий"


# ---------------------------------------------------------------------------
# Атака проти поламаного шрифту
# ---------------------------------------------------------------------------

def _uniform_page(page):
    return [
        {"line": "Адмiнiстративна вiдповiдальнiсть органу влади", "page": page},
        {"line": "Правовi засади дiяльностi мiсцевого самоврядування", "page": page},
    ]


def test_uniform_substitution_across_all_pages_is_encoding_issue():
    lines = [item for page in range(1, 11) for item in _uniform_page(page)]
    result = scan_text_forensics(lines)
    assert result.likely_encoding_issue is True
    assert result.affected_pct > 5.0
    assert result.pages_affected == list(range(1, 11))


def test_pointwise_substitution_on_two_pages_is_not_encoding_issue():
    lines = []
    for page in range(1, 11):
        if page in (3, 7):
            lines += _uniform_page(page)
        else:
            lines += [
                {"line": "Правові засади діяльності місцевого самоврядування", "page": page},
                {"line": "Адміністративна відповідальність органу влади", "page": page},
            ]
    result = scan_text_forensics(lines)
    assert result.likely_encoding_issue is False
    assert result.pages_affected == [3, 7]


def test_docx_uniform_substitution_is_encoding_issue_without_pages():
    """У DOCX номерів сторінок немає — критерій лише за часткою слів."""
    lines = _docx_lines(*[
        "Адмiнiстративна вiдповiдальнiсть органу влади",
        "Правовi засади дiяльностi мiсцевого самоврядування",
    ] * 10)
    result = scan_text_forensics(lines)
    assert result.likely_encoding_issue is True
    assert result.pages_affected == []


def test_docx_pointwise_substitution_is_not_encoding_issue():
    clean = ["Правові засади діяльності місцевого самоврядування у громадах"] * 20
    dirty = ["Адмiнiстративна відповідальність органу влади"]
    result = scan_text_forensics(_docx_lines(*(clean + dirty)))
    assert result.affected_pct < 5.0
    assert result.likely_encoding_issue is False
    assert result.pages_affected == []


def test_empty_document_does_not_divide_by_zero():
    result = scan_text_forensics([])
    assert result.total_words == 0
    assert result.affected_pct == 0.0
    assert result.hits == []
    assert result.likely_encoding_issue is False


def test_blank_lines_do_not_crash():
    result = scan_text_forensics([{"line": "", "page": 1}, {"line": "   ", "page": 1}])
    assert result.total_words == 0
    assert result.hits == []


# ---------------------------------------------------------------------------
# Запобіжники правил
# ---------------------------------------------------------------------------

def test_mostly_latin_word_is_not_a_mixed_hit():
    """Кирилиці менше половини — це латинське слово з кириличною вставкою."""
    result = scan_text_forensics(_pdf_lines("Термін blockchaіn у сучасній науці"))
    assert result.hits == []


def test_mixed_word_with_non_homoglyph_latin_is_ignored():
    """Серед латинських літер є не-гомогліф — це не підміна, а гібридне слово."""
    result = scan_text_forensics(_pdf_lines("Формат dжерела не розпізнано системою"))
    assert result.hits == []


def test_uppercase_lone_letter_does_not_fire():
    result = scan_text_forensics(_pdf_lines("Пункт I розділу другого документа"))
    assert result.hits == []


def test_lone_letter_needs_cyrillic_neighbours_on_both_sides():
    result = scan_text_forensics(_pdf_lines(
        "Value i value залишились без перекладу",
        "i початок рядка також не рахується",
    ))
    assert result.hits == []


def test_lone_letter_before_equals_sign_is_ignored():
    result = scan_text_forensics(_pdf_lines("Нехай величина i = 5 буде сталою"))
    assert result.hits == []
