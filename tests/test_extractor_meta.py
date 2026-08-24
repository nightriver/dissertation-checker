"""
Unit tests for author/year extraction from the title page.
Run: pytest tests/test_extractor_meta.py
"""
import datetime
import unittest

from parser.extractor import (
    _title_ua,
    _looks_like_person_name,
    extract_dissertation_author,
    extract_dissertation_year,
)


def L(texts, page=1):
    return [{"line": t, "page": page} for t in texts]


class TestTitleUa(unittest.TestCase):
    """Bug: str.title() capitalises after an apostrophe."""

    def test_modifier_letter_apostrophe(self):
        # U+02BC — the apostrophe Ukrainian typography actually uses
        self.assertEqual(_title_ua("ПЕТРЕНКО ВʼЯЧЕСЛАВ ІВАНОВИЧ"),
                         "Петренко Вʼячеслав Іванович")

    def test_right_single_quote_apostrophe(self):
        # U+2019 — what Word autocorrect produces
        self.assertEqual(_title_ua("КОВАЛЬ ДАР’Я ОЛЕГІВНА"),
                         "Коваль Дар’я Олегівна")

    def test_ascii_apostrophe(self):
        self.assertEqual(_title_ua("ЛУК'ЯНЕНКО ІВАН"), "Лук'яненко Іван")

    def test_hyphen_still_capitalises(self):
        # Double surnames must keep the capital after the hyphen
        self.assertEqual(_title_ua("ІВАНОВ-ПЕТРЕНКО ІВАН ІВАНОВИЧ"),
                         "Іванов-Петренко Іван Іванович")

    def test_already_title_case(self):
        self.assertEqual(_title_ua("Петренко Іван Іванович"),
                         "Петренко Іван Іванович")

    def test_stdlib_title_would_fail(self):
        # Guards the regression: str.title() is not an acceptable substitute
        self.assertNotEqual(_title_ua("ДАР’Я"), "ДАР’Я".title())


class TestLooksLikePersonName(unittest.TestCase):
    def test_accepts_plain_name(self):
        self.assertTrue(_looks_like_person_name("ПЕТРЕНКО ІВАН ІВАНОВИЧ"))

    def test_rejects_institution(self):
        self.assertFalse(_looks_like_person_name("Міністерство освіти України"))
        self.assertFalse(_looks_like_person_name("НАЦІОНАЛЬНИЙ ПЕДАГОГІЧНИЙ УНІВЕРСИТЕТ"))

    def test_rejects_service_phrase(self):
        self.assertFalse(_looks_like_person_name("На правах рукопису"))
        self.assertFalse(_looks_like_person_name("Кваліфікаційна наукова праця"))

    def test_rejects_lowercase_word(self):
        self.assertFalse(_looks_like_person_name("аналіз стану галузі"))


class TestExtractAuthor(unittest.TestCase):
    def test_variant_a_full_name_before_udk(self):
        doc = L(["ПЕТРЕНКО ІВАН ІВАНОВИЧ", "УДК 004.9"])
        self.assertEqual(extract_dissertation_author(doc), "Петренко Іван Іванович")

    def test_variant_b_split_name_before_udk(self):
        doc = L(["СЛУЦЬКА", "ТЕТЯНА ІВАНІВНА", "УДК 343.2"])
        self.assertEqual(extract_dissertation_author(doc), "Слуцька Тетяна Іванівна")

    # ── regressions: any 3-word line before УДК used to be returned ─────────
    def test_institution_before_udk_rejected(self):
        doc = L(["Міністерство освіти України", "УДК 351.9"])
        self.assertIsNone(extract_dissertation_author(doc))

    def test_manuscript_boilerplate_before_udk_rejected(self):
        doc = L(["На правах рукопису", "УДК 351.9"])
        self.assertIsNone(extract_dissertation_author(doc))

    def test_falls_through_to_pass_two(self):
        # Junk directly above УДК must not stop the search: the real name
        # further down is still found by the fallback pass.
        doc = L(["Міністерство освіти України", "УДК 351.9",
                 "ПЕТРЕНКО ІВАН ІВАНОВИЧ"])
        self.assertEqual(extract_dissertation_author(doc), "Петренко Іван Іванович")

    def test_apostrophe_name_before_udk(self):
        doc = L(["ПЕТРЕНКО ВʼЯЧЕСЛАВ ІВАНОВИЧ", "УДК 004.9"])
        self.assertEqual(extract_dissertation_author(doc),
                         "Петренко Вʼячеслав Іванович")

    def test_udk_on_first_line_no_crash(self):
        self.assertIsNone(extract_dissertation_author(L(["УДК 004.9"])))

    def test_empty(self):
        self.assertIsNone(extract_dissertation_author([]))

    def test_respects_max_lines(self):
        doc = L(["порожньо"] * 90 + ["ПЕТРЕНКО ІВАН ІВАНОВИЧ"])
        self.assertIsNone(extract_dissertation_author(doc, max_lines=80))


class TestExtractYear(unittest.TestCase):
    def test_city_dash_year(self):
        self.assertEqual(extract_dissertation_year(L(["Київ – 2023"])), 2023)

    def test_rik_anchor(self):
        self.assertEqual(extract_dissertation_year(L(["Захищено 2019 р."])), 2019)

    def test_parens_at_end_of_line(self):
        self.assertEqual(extract_dissertation_year(L(["Дисертація (2021)"])), 2021)

    def test_city_anchor_wins_over_rik(self):
        doc = L(["Подано 2019 р.", "Харків — 2022"])
        self.assertEqual(extract_dissertation_year(doc), 2022)

    def test_fallback_takes_max(self):
        doc = L(["Огляд 1998", "Праці 2005", "Збірник 2011"])
        self.assertEqual(extract_dissertation_year(doc), 2011)

    def test_fallback_excludes_future_years(self):
        future = datetime.datetime.now().year + 3
        doc = L([f"Планується {future}", "Видано 2016"])
        self.assertEqual(extract_dissertation_year(doc), 2016)

    def test_no_year(self):
        self.assertIsNone(extract_dissertation_year(L(["Без дати"])))

    def test_empty(self):
        self.assertIsNone(extract_dissertation_year([]))

    def test_respects_max_lines(self):
        doc = L(["текст"] * 70 + ["Київ – 2023"])
        self.assertIsNone(extract_dissertation_year(doc, max_lines=60))
