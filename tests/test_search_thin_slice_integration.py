"""
Шлюз кроку 3 §22 PLAN_SEARCH.md (перша половина): наскрізний тонкий зріз
від байтів синтетичного PDF до одного детермінованого запиту.

Рішення оркестратора, задокументоване тут: каналом тонкого зрізу обрано A
(§10.2) — найдетермінованіший зі змістовних каналів. Синтетичний PDF містить
речення з ДВОМА маркерами A (основа "пропон" і точна послідовність
"на нашу думку"), щоб набрати +4 і пройти поріг основного відбору §11.2:
одного маркера (+2) для порогу не вистачило б.

PDF будується через `page.insert_htmlbox` (не `insert_text` без шрифту, бо
базовий шрифт Helvetica не має кириличних гліфів і дає "�"; не
`fitz.TextWriter`, бо він підміняє звичайний пробіл на нерозривний U+00A0
під час позиціювання гліфів). `insert_htmlbox` коректно вбудовує кирилицю
без зовнішнього файлу шрифту і зберігає звичайні пробіли — придатний і
відтворюваний спосіб для тесту, без бінарного зразка в репозиторії.
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from parser.searchdoc import NoTextLayerError, parse_search_document
from search.query_builder import build_search_result
from search.types import Channel, SectionKind


HEADING_TEXT = "РОЗДІЛ 1"
BODY_TEXT = (
    "Ми пропонуємо, на нашу думку, важливе рішення для реформування "
    "вітчизняного законодавства."
)


def _build_single_section_pdf_bytes() -> bytes:
    """Односторінковий PDF з текстовим шаром і одним розділом (§22, крок 3)."""
    doc = fitz.open()
    page = doc.new_page()
    html = f"<p>{HEADING_TEXT}</p><p>{BODY_TEXT}</p>"
    page.insert_htmlbox(fitz.Rect(72, 72, 500, 300), html)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return _build_single_section_pdf_bytes()


def test_parses_single_chapter_section_with_one_terminated_sentence(pdf_bytes):
    document = parse_search_document(pdf_bytes)

    assert document.n_pages == 1
    assert len(document.sections) == 1
    section = document.sections[0]
    assert section.kind == SectionKind.CHAPTER
    assert section.ordinal == 1
    assert section.physical_pages == (1,)

    assert len(document.sentences) == 1
    donor = document.sentences[0]
    assert donor.raw_text.startswith("Ми пропонуємо")
    assert donor.raw_text.endswith(".")

    # §22, крок 3: бібліографія і цитати — валідні порожні колекції.
    assert document.bibliography == ()
    assert document.citations == ()


def test_produces_one_primary_a_query_with_merged_t_attribution(pdf_bytes):
    document = parse_search_document(pdf_bytes)
    result = build_search_result(document)

    assert len(result.queries) == 1
    query = result.queries[0]
    assert query.primary_channel == Channel.A
    # Кроки 9–11: той самий фрагмент також дає T-кандидата; §14.1
    # дедуплікує їх, але зберігає обидві атрибуції на переможці A.
    assert query.attributed_channels == (Channel.A, Channel.T)
    # §10.2: +2 за маркер основи "пропон", +2 за фразу "на нашу думку" = 4.
    assert query.score == 4.0
    assert query.selection_stage == 1
    assert query.physical_page == 1

    assert query.query_text.startswith("«")
    assert query.query_text.endswith("»")
    assert "пропонуємо" in query.query_text
    assert "на нашу думку" in query.query_text
    # §13, крок 7: обмеження довжини готового рядка.
    assert len(query.query_text) <= 220

    # §13: фінальна перевірка — query_text відтворюваний із parts.
    assert "".join(part.text for part in query.parts) == query.query_text

    # §15: якір — послідовні вихідні слова, символи raw_text не змінені.
    assert query.pdf_anchor in query.donor_text
    assert 1 <= query.pdf_anchor.count(" ") + 1  # непорожній

    # Два маркери A мають лишити точний слід; T-сигнали кроку 9 також
    # видимі й не повинні ламати перевірку каналу A.
    a_hits = tuple(hit for hit in result.signal_hits if hit.channel == Channel.A)
    assert len(a_hits) == 2
    assert {hit.rule_id for hit in a_hits} == {
        "A.stem.пропон",
        "A.phrase.0",
    }
    assert any(hit.channel == Channel.T for hit in result.signal_hits)


def test_rejected_and_zero_channel_counters_stay_visible_in_diagnostics(pdf_bytes):
    """CLAUDE.md, правило №3: нічого не подавляється мовчки, лічильники видно завжди."""
    document = parse_search_document(pdf_bytes)
    result = build_search_result(document)

    channels_generated = dict(result.candidate_metrics.generated_by_channel)
    # Крок 9 додає T для рідкісних форм. Решта нульових лічильників усе одно
    # мають бути присутні, а A/T об'єднуються вже під час дедуплікації.
    assert channels_generated[Channel.A] == 1
    assert channels_generated[Channel.T] == 1
    for channel in (Channel.N, Channel.B, Channel.K, Channel.L):
        assert channels_generated[channel] == 0

    assert result.candidate_metrics.rejected_by_reason == ()


def test_document_sha256_and_query_id_are_stable_hex_digests(pdf_bytes):
    document = parse_search_document(pdf_bytes)
    result = build_search_result(document)

    assert len(document.document_sha256) == 64
    int(document.document_sha256, 16)  # валідний hex
    query = result.queries[0]
    assert len(query.query_id) == 64
    int(query.query_id, 16)
    assert len(query.donor_id) == 64
    int(query.donor_id, 16)


def test_rerun_from_the_same_bytes_is_byte_for_byte_identical(pdf_bytes):
    """Шлюз §22: повторний прогін дає точно той самий результат."""
    document_1 = parse_search_document(pdf_bytes)
    result_1 = build_search_result(document_1)

    document_2 = parse_search_document(pdf_bytes)
    result_2 = build_search_result(document_2)

    assert document_1 == document_2
    assert result_1 == result_2
    assert result_1.queries[0].query_text == result_2.queries[0].query_text
    assert result_1.queries[0].query_id == result_2.queries[0].query_id


def test_no_text_layer_pdf_raises_a_dedicated_error():
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()

    with pytest.raises(NoTextLayerError):
        parse_search_document(data)


def test_sentence_shorter_than_the_window_minimum_is_rejected_as_no_valid_windows():
    """§13, крок 1: вікно 6–10 слів; коротший донор не дає жодного вікна."""
    doc = fitz.open()
    page = doc.new_page()
    short_body = "Пропонуємо, на нашу думку, рішення."  # 5 словесних токенів, менше мінімуму 6
    html = f"<p>{HEADING_TEXT}</p><p>{short_body}</p>"
    page.insert_htmlbox(fitz.Rect(72, 72, 500, 300), html)
    data = doc.tobytes()
    doc.close()

    document = parse_search_document(data)
    result = build_search_result(document)

    assert result.queries == ()
    assert dict(result.candidate_metrics.rejected_by_reason)["no_valid_windows"] == 1
    # Сигнали A все одно лишаються видимими; T може додати власні сигнали.
    a_hits = tuple(hit for hit in result.signal_hits if hit.channel == Channel.A)
    assert len(a_hits) == 2
    assert any(hit.channel == Channel.T for hit in result.signal_hits)


def test_sentence_outside_a_content_section_produces_signal_hits_but_no_queries():
    """
    §6.1: лише INTRO/CHAPTER/CONCLUSIONS дають запити (квоту); UNKNOWN — ні.
    Але сигнали й кандидат каналу A лишаються видимими в діагностиці
    (§6.1: "сигнали і кандидати UNKNOWN лишаються в діагностиці"; CLAUDE.md,
    правило №3 — нічого не подавляється мовчки).
    """
    doc = fitz.open()
    page = doc.new_page()
    # Без заголовка розділу вміст лишається в сегменті SectionKind.UNKNOWN.
    html = f"<p>{BODY_TEXT}</p>"
    page.insert_htmlbox(fitz.Rect(72, 72, 500, 300), html)
    data = doc.tobytes()
    doc.close()

    document = parse_search_document(data)
    assert document.sections[0].kind == SectionKind.UNKNOWN

    result = build_search_result(document)
    assert result.queries == ()
    # Обидва маркери A потрапляють у діагностику разом із незалежними
    # T-сигналами рідкісних форм.
    a_hits = tuple(hit for hit in result.signal_hits if hit.channel == Channel.A)
    assert len(a_hits) == 2
    assert {hit.rule_id for hit in a_hits} == {
        "A.stem.пропон",
        "A.phrase.0",
    }
    assert any(hit.channel == Channel.T for hit in result.signal_hits)
    # Кандидат згенеровано (є сигнали), але не перетворено на SearchQuery
    # через тип розділу — це видно як причина відсіву, а не мовчазне зникнення.
    assert dict(result.candidate_metrics.generated_by_channel)[Channel.A] == 1
    assert dict(result.candidate_metrics.rejected_by_reason)["section_unknown"] == 1
