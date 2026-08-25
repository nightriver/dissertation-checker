from compare.matcher import compare_documents, compare_tokens
from compare.normalize import tokenize_lines
from compare.prepare import prepare_document, prepare_document_for_comparison


def _lines(*values):
    return [{"line": value, "page": index + 1} for index, value in enumerate(values)]


def test_dissertation_title_and_toc_are_excluded_explicitly():
    prepared = prepare_document(_lines(
        "Міністерство освіти і науки України",
        "ЗМІСТ",
        "ВСТУП ..... 5",
        "РОЗДІЛ 1 ..... 10",
        "ВСТУП",
        "РОЗДІЛ 1 Теоретичні засади",
        "ВИСНОВКИ",
    ))
    assert prepared.resembles_dissertation
    assert [item.reason for item in prepared.excluded] == ["title_page", "toc"]
    assert prepared.tokens[0].normalized == "вступ"


def test_toc_heading_with_page_number_on_next_line_is_not_content_boundary():
    prepared = prepare_document(_lines(
        "Титульний аркуш",
        "ЗМІСТ",
        "ВСТУП",
        "5",
        "РОЗДІЛ 1 Теоретичні засади",
        "15",
        "ВИСНОВКИ",
        "180",
        "ВСТУП",
        "Авторський текст вступу",
        "РОЗДІЛ 1 Теоретичні засади",
        "Основний текст",
        "ВИСНОВКИ",
        "Підсумковий текст",
    ))
    assert prepared.tokens[0].normalized == "вступ"
    assert prepared.tokens[1].normalized == "авторський"
    assert [item.reason for item in prepared.excluded] == ["title_page", "toc"]


def test_multiline_toc_entry_without_adjacent_page_number_is_not_content_boundary():
    prepared = prepare_document(_lines(
        "Титульний аркуш",
        "ЗМІСТ",
        "ВСТУП",
        "Актуальність, мета та завдання дослідження",
        "5",
        "РОЗДІЛ 1. Поняття адміністративно-правового статусу",
        "громадянина та його зміст",
        "15",
        "ВИСНОВКИ",
        "180",
        "ВСТУП",
        "Справжній авторський вступ",
        "РОЗДІЛ 1 Теоретичні засади",
        "Основний текст",
        "ВИСНОВКИ",
        "Підсумковий текст",
    ))
    assert prepared.resembles_dissertation
    assert prepared.tokens[0].normalized == "вступ"
    assert prepared.tokens[1].normalized == "справжній"


def test_word_contents_inside_main_text_is_not_mistaken_for_toc_header():
    prepared = prepare_document(_lines(
        "Титульний аркуш",
        "ВСТУП……5",
        "РОЗДІЛ 1. Теоретичні засади……15",
        "ВСТУП",
        "Справжній авторський вступ",
        "зміст",
        "РОЗДІЛ 1 Теоретичні засади",
        "Основний текст",
        "ВИСНОВКИ",
        "Підсумковий текст",
    ))
    assert prepared.resembles_dissertation
    assert prepared.tokens[0].normalized == "вступ"
    assert prepared.tokens[1].normalized == "справжній"


def test_non_dissertation_is_not_trimmed():
    lines = _lines("Міністерство освіти і науки України", "Текст статті")
    prepared = prepare_document(lines)
    assert not prepared.resembles_dissertation
    assert prepared.excluded == ()
    assert len(prepared.tokens) == len(prepared.all_tokens)


def test_shared_title_declaration_does_not_create_dissertation_match():
    declaration = " ".join([
        "дисертація", "містить", "результати", "власних", "досліджень",
        "використання", "ідей", "результатів", "і", "текстів", "інших",
        "авторів", "мають", "посилання", "на", "відповідне", "джерело",
    ])
    a = _lines(declaration, "ВСТУП", "унікальний текст першої праці", "РОЗДІЛ 1", "ВИСНОВКИ")
    b = _lines(declaration, "ВСТУП", "інший текст другої праці", "РОЗДІЛ 1", "ВИСНОВКИ")
    result = compare_documents(a, b)
    assert result.segments == []
    assert result.excluded_a and result.excluded_b


def test_boilerplate_is_marked_when_structure_detection_does_not_apply():
    phrase = "дисертація містить результати власних досліджень " + " ".join(
        f"типове{i}" for i in range(12)
    )
    result = compare_tokens(tokenize_lines(_lines(phrase)), tokenize_lines(_lines(phrase)))
    assert result.segments[0].possibly_boilerplate


def test_uncertain_bibliography_stays_in_text_with_warning():
    lines = _lines(
        "Основний текст документа",
        "СПИСОК ЛІТЕРАТУРИ",
        "1. Іванов І. І. Єдине джерело. Київ, 2019.",
    )
    prepared, entries = prepare_document_for_comparison(lines)
    assert len(entries) == 1
    assert prepared.bibliography_warning
    assert all(item.reason != "bibliography" for item in prepared.excluded)
    assert any(token.normalized == "іванов" for token in prepared.tokens)
