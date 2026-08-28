"""
Валідує tests/fixtures/search_corpus_expectations.json — вручну зафіксовані
факти про дев'ять PDF корпусу PLAN_SEARCH.md §20.2 (крок 2 §22). Жодної
евристики search/* ще не існує; ці тести перевіряють лише:

  1. форма фікстури коректна та внутрішньо несуперечлива;
  2. розділи очікувань (structure / zones / bibliography / calques)
     валідуються НЕЗАЛЕЖНО один від одного — зламані дані одного розділу
     не валять тести інших розділів (§22 крок 2, шлюз "розділи ожиданий
     незалежні");
  3. "спостережувані" факти досі відповідають реальним файлам у examples/ —
     sha256 і кількість сторінок дешево перерахувати заново, це ловить
     застарілу фікстуру чи підмінений приклад;
  4. КОЖЕН хешований фрагмент (zone_examples, author_text_samples,
     calques.examples, citation_example.*) справді присутній у вказаному
     PDF на вказаній сторінці — тест сам відкриває examples/<file> через
     fitz, зводить текст сторінки до канонічної форми (див.
     `fragment_canonical_form` у самій фікстурі) і шукає вікно слів, чия
     sha256[:16] дорівнює записаному хешу. Це не евристика розпізнавання і
     не підміна search/* — лише перевірка присутності підрядка;
  5. жоден продуктовий модуль не відкриває і не перезаписує фікстуру
     (§22 крок 2, шлюз "алгоритм не може перезаписати фікстуру").

Ці тести свідомо НЕ реалізують і не схвалюють жодну евристику розпізнавання —
це робота search/* на наступних кроках.

Прийняті рішення, яких немає в PLAN_SEARCH.md дослівно (див. звіт кроку 2):

- `author_text_min_words` тепер є сумою `word_count` РІВНО ТРЬОХ вручну
  відмічених авторських фрагментів (мінімум 3 — число задав оркестратор,
  у плані його немає), кожен ≥15 слів, з різних сторінок, усередині тіла
  роботи (`first_chapter_page` .. `conclusions_page`). Це чесна нижня межа
  в десятки слів, а НЕ знімок виводу `parser.extractor` (§20.2 explicitly
  забороняє фікстурі бути знімком алгоритму).
- `PAGE_TOLERANCE = 1` — допуск ±1 сторінка для звірки сторінок заголовків
  (§20.2 вимагає допуск, але не називає число; воно зафіксоване тут і в полі
  схеми `page_tolerance`, щоб крок 5 брав його звідси, а не вигадував заново).
- Канонічна форма фрагмента (рішення оркестратора після ревʼю кроку 2):
  Unicode NFKC, видалення м'якого переносу U+00AD, склеювання переносу слова
  через дефіс на кінці рядка (`-\\n` -> ''), схлопування решти пробілів і
  переводів рядків в один ASCII-пробіл, РЕГІСТР ЗБЕРІГАЄТЬСЯ як у PDF (текст
  НЕ приводиться до нижнього регістру). Правило продубльоване явним полем
  схеми `fragment_canonical_form`, щоб крок 5 брав його звідти, а не
  вигадував заново. Кожен фрагмент отримав `word_count`, включно з
  `calques.examples` і `citation_example` (`bracket_word_count`,
  `bibliography_entry_word_count`) — це дозволяє шукати РІВНО одне вікно
  потрібної довжини (лінійний прохід по сторінці) замість перебору всіх
  довжин 1..45; верхня межа 45 слів лишається захисною стелею формату
  фікстури (жоден word_count у ній не перевищує 45).

Другий (і останній) прохід ревʼю кроку 2 знайшов два блокуючі дефекти
ЗМІСТУ фікстури — не форми. Рішення, прийняті під час їх виправлення:

- `bibliography_entry_word_count` БІЛЬШЕ НЕ механічна квота "рівно 12
  слів" (так було в усіх дев'яти документах — ознака того, що межу запису
  ніхто вручну не перевіряв). Тепер для кожного документа межа запису
  зафіксована вручну по реальному номеру запису на сторінці бібліографії:
  від початку запису `source_ordinal` до початку запису `source_ordinal+1`
  (або до кінця сторінки, якщо запис останній на ній і не обривається).
  Довжини вийшли різні (10..44 слів) — це і є ознака чесної ручної роботи.
  Жоден запис не довший за стелю 45 слів, тому поле `entry_fragment_is_prefix`
  не знадобилося (лишається зарезервованим для кроку 6, якщо колись
  трапиться довший запис).
- Два документи (DISSERTAZIYA.doc.pdf, diser.pdf) мають в екстракції PDF
  номер запису, ЗЛИТИЙ без пробілу з першим словом ("38.Козлов",
  "24.Бачун") — так відформатовано саме джерело, а не помилка екстракції.
  Оскільки тестова токенізація ділить текст лише по пробілах, розділити
  "38." і "Козлов" на два токени неможливо: фрагмент чесно починається
  зі злитого токена. Це відрізняється від документів, де перед текстом
  запису стоїть пробіл (там номер — окремий токен і просто не входить у
  вікно) — обидва варіанти чесно відбивають те, що реально є в PDF.
- Новий гейт-тест
  `test_bibliography_entry_fragment_does_not_swallow_the_next_numbered_entry`
  ловить саме клас дефекту "вікно перетнуло межу запису": воно не повинно
  містити (після першого токена) "голий" маркер `<1-3 цифри>.` — так
  виглядає початок сусіднього запису. Поріг 1–3 цифри (не 4) — свідоме
  рішення: роки видань завжди 4-значні й не повинні хибно спрацьовувати,
  а жодна з дев'яти бібліографій не має 1000+ джерел. Перевірено, що цей
  тест падає на старих (зіпсованих) даних кроку 2 — див. звіт.
- Усі дев'ять негативних прикладів `danyi` виявилися фіктивними: жоден не
  містив слова, яке матчить регексп правила (перевірено прогоном
  `re.search` по всіх дев'яти) — "негатив", на якому правилу нема на чому
  спрацювати, нічого не доводить. Причина системна, а не по документах:
  правило `danyi` (tier 3, лише щільність) ловить закінчення
  `(ий|а|е|ої|ому|им|их|ими|і)`, а українське іменник "дані" (data) і
  прикметник "даний" (this/given) — омографи в усіх відмінках множини
  ("дані", "даних", "даним", "даними"), тому спровокований фрагмент без
  спрацювання регекспу для цього правила структурно неможливий. Негативи
  замінені на реальні: `yavlyaietsya` (у восьми документах — складені
  слова "виявляється"/"проявляється"/"з'являється" містять підрядок
  "являється", але lookbehind правила `(?<![а-яіїєґ'])` не дає йому
  спрацювати — перевірено регекспом) і `zadacha` (Работа май-docx-2.pdf —
  називний відмінок "задача" не входить у список закінчень
  `чі|чу|чами|чах|ч`, теж перевірено регекспом). Обидва підтверджені
  прогоном `re.search` з реальним правилом: matched=False, і додатково
  перевірені `NEGATIVE_TRIGGER_PATTERNS` (тест-only "розхитаний" варіант
  того ж правила без винятку) — тригерне слово справді присутнє.
- Стара вимога "якщо є хоча б один приклад — серед них має бути хоча б
  один негативний" (тест `test_at_least_one_negative_example_when_any_
  example_present`) СКАСОВАНА — вона суперечила умовній формулюванню §20.2
  "позитивні й негативні приклади, ЯКЩО ВОНИ Є" і саме вона штовхнула до
  фіктивних `danyi`-негативів у першому проході. Ця вимога сама по собі
  теж не була задокументована тут раніше — ревʼю відзначило це окремо.
  Замість неї: якщо негативу серед прикладів документа немає, розділ
  повинен нести явне поле `negative_absent_reason` з непорожньою причиною
  (CLAUDE.md правило №3 — нічого не подавляється мовчки); у поточній
  фікстурі реальний негатив знайшовся для всіх дев'яти документів, тож це
  поле ніде фактично не використовується, але механізм перевіряється
  тестом на обидві гілки (є негатив -> поля бути не повинно; немає -> поле
  обов'язкове).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import unittest
from pathlib import Path

import fitz

from search.calques import CALQUES, find_calques
from search.normalization import normalize_text, tokenize
from search.types import Confidence, SearchBlock, TextZone, ZoneSpan

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "search_corpus_expectations.json"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
REPO_ROOT = Path(__file__).parent.parent

# §20.2: сторінки заголовків звіряються з допуском ±1 сторінка. Число не
# назване в плані дослівно — прийняте оркестратором рішення, зафіксоване тут
# і продубльоване в самій фікстурі (`page_tolerance`), щоб крок 5 читав його
# з фікстури, а не підбирав заново.
PAGE_TOLERANCE = 1

# Захисна стеля довжини вікна пошуку фрагмента (слів). Кожен фрагмент несе
# власний `word_count`, тому пошук завжди лінійний по одній довжині; ця
# константа лише підтверджує, що жоден word_count у фікстурі її не перевищує.
MAX_FRAGMENT_WORDS = 45

# Розділи очікувань §20.2, які мають валідуватись незалежно один від одного.
SECTION_NAMES = {"structure", "zones", "bibliography", "calques"}

# Продуктові модулі, які теж скануються на заборонену згадку фікстури.
_SCANNED_ROOTS = ("search", "parser", "tools", "compare")
_SCANNED_FILES = ("app.py", "ui_helpers.py")
_SKIP_DIR_NAMES = {".venv", ".venv312", "__pycache__", "examples", "tmp", "tests"}

_KNOWN_CALQUE_RULE_IDS = {rule.rule_id for rule in CALQUES}

# Тест-only "розхитані" тригер-регулярки: та сама лексична основа правила
# з CALQUES, але БЕЗ виключення (lookahead/lookbehind чи обмеження списку
# суфіксів), яке й робить приклад негативом. Потрібні, щоб довести: у
# негативному прикладі тригерне слово справді Є (інакше "правило не
# спрацювало" нічого не доводить — воно могло просто не мати на чому
# спрацьовувати, саме така фіктивна ситуація й була знайдена в першому
# проході кроку 2 для "danyi"). Ключ — лише ті rule_id, що фактично
# використані як negative у фікстурі; якщо зʼявиться новий — KeyError тут
# зупинить тест, а не мовчки пропустить перевірку (CLAUDE.md правило №3).
NEGATIVE_TRIGGER_PATTERNS: dict[str, str] = {
    # "yavlyaietsya": (?<!...)явля[єе]ться\b — прибрано lookbehind, що
    # відсіює складені слова ("проявляється", "виявляється", "з'являється").
    "yavlyaietsya": r"явля[єе]ться\b",
    # "zadacha": \bзада(?:чі|чу|чами|чах|ч)\b — прибрано обмеження на
    # відмінкові закінчення, щоб ловити називний відмінок "задача" теж.
    "zadacha": r"\bзада\w+\b",
}

EXPECTED_FILES = {
    "Работа май-docx-2.pdf",
    "Гончарова-Парфьонова_дисертація.pdf",
    "DISSERTAZIYA.doc.pdf",
    "diss-doc.pdf",
    "diskor-корецька.pdf",
    "diser.pdf",
    "dis2005_bayar_kandidat.PDF",
    "dis.doc-КОЦЮБА.pdf",
    "Dis-doc-марченко.pdf",
}


def _load():
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _iter_product_py_files():
    """Усі .py файли продуктового коду (не tests/), незалежно від кодування."""
    for root_name in _SCANNED_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            yield path
    for fname in _SCANNED_FILES:
        path = REPO_ROOT / fname
        if path.is_file():
            yield path


class TestFixtureShape(unittest.TestCase):
    """Базова форма файла: він існує, версія схеми та повний список PDF."""

    def test_file_exists(self):
        self.assertTrue(FIXTURE_PATH.is_file())

    def test_schema_version_is_two(self):
        self.assertEqual(_load()["schema_version"], 2)

    def test_page_tolerance_field_matches_test_constant(self):
        # §20.2 вимагає допуск ±1 сторінку для заголовків; число зафіксоване
        # тут і продубльоване в самій фікстурі, щоб крок 5 не вигадував його
        # заново.
        self.assertEqual(_load()["page_tolerance"], PAGE_TOLERANCE)

    def test_covers_exactly_the_nine_plan_pdfs(self):
        payload = _load()
        names = {doc["file"] for doc in payload["documents"]}
        self.assertEqual(names, EXPECTED_FILES)
        self.assertEqual(len(payload["documents"]), 9)

    def test_no_duplicate_files(self):
        payload = _load()
        names = [doc["file"] for doc in payload["documents"]]
        self.assertEqual(len(names), len(set(names)))

    def test_every_document_has_the_four_named_sections(self):
        # Кожен розділ присутній явно (навіть якщо статус not_filled) —
        # CLAUDE.md правило №3: "ничего не подавляется молча".
        for doc in _load()["documents"]:
            with self.subTest(doc=doc["file"]):
                self.assertEqual(set(doc["sections"].keys()), SECTION_NAMES)

    def test_sha256_is_64_hex_chars(self):
        for doc in _load()["documents"]:
            with self.subTest(doc=doc["file"]):
                self.assertRegex(doc["sha256"], r"^[0-9a-f]{64}$")


class SectionStatusMixin:
    """Спільна перевірка статусу для будь-якого розділу: filled | not_filled,
    а not_filled завжди супроводжується явною причиною (reason)."""

    section_name: str

    def _sections(self):
        for doc in _load()["documents"]:
            yield doc["file"], doc["sections"][self.section_name]

    def test_status_is_filled_or_not_filled_with_reason(self):
        for fname, section in self._sections():
            with self.subTest(doc=fname):
                self.assertIn(section["status"], {"filled", "not_filled"})
                if section["status"] == "not_filled":
                    self.assertIn("reason", section)
                    self.assertTrue(section["reason"])


class TestStructureSection(SectionStatusMixin, unittest.TestCase):
    """Розділ 'structure': сторінки заголовків §6.1. Незалежний від
    zones/bibliography/calques — зламані дані тут не впливають на їхні тести.
    """

    section_name = "structure"

    def test_headings_are_ordered_and_within_document(self):
        for doc in _load()["documents"]:
            section = doc["sections"]["structure"]
            if section["status"] != "filled":
                continue
            h = section["headings"]
            with self.subTest(doc=doc["file"]):
                self.assertLess(h["intro_page"], h["first_chapter_page"])
                self.assertLess(h["first_chapter_page"], h["conclusions_page"])
                self.assertLess(h["conclusions_page"], h["bibliography_page"])
                self.assertLessEqual(h["bibliography_page"], doc["expected_pages"])
                self.assertGreaterEqual(h["intro_page"], 1)


class TestZonesSection(SectionStatusMixin, unittest.TestCase):
    """Розділ 'zones': §6.2 (QUOTED_TEXT/AUTHOR_TEXT) + вручну відмічені
    авторські фрагменти, з яких рахується `author_text_min_words`.
    """

    section_name = "zones"

    def _filled_zones(self):
        for doc in _load()["documents"]:
            section = doc["sections"]["zones"]
            if section["status"] == "filled":
                yield doc, section

    def test_zone_examples_have_both_keys(self):
        for doc, section in self._filled_zones():
            with self.subTest(doc=doc["file"]):
                self.assertIn("quoted_text", section["zone_examples"])
                self.assertIn("author_text", section["zone_examples"])

    def test_present_zone_example_fragments_store_a_hash_not_raw_text(self):
        # PLAN_SEARCH.md §20.2: "Фикстура хранит только короткие хешированные
        # фрагменты" — ніколи не сирі речення дисертації.
        for doc, section in self._filled_zones():
            for key, example in section["zone_examples"].items():
                if example is None:
                    continue
                with self.subTest(doc=doc["file"], zone=key):
                    self.assertRegex(example["fragment_sha256"], r"^[0-9a-f]{16}$")
                    self.assertGreater(example["page"], 0)

    def test_at_least_three_author_text_samples_on_distinct_pages(self):
        # Оркестраторське рішення (немає в плані дослівно): мінімум 3 вручну
        # відмічені авторські фрагменти з різних сторінок.
        for doc, section in self._filled_zones():
            with self.subTest(doc=doc["file"]):
                samples = section["author_text_samples"]
                self.assertGreaterEqual(len(samples), 3)
                pages = {s["page"] for s in samples}
                self.assertEqual(len(pages), len(samples), "сторінки мають бути різними")

    def test_each_author_text_sample_has_at_least_fifteen_words(self):
        # Оркестраторське рішення: кожен фрагмент — зв'язна авторська проза
        # не менше 15 слів (не заголовок, не підпис, не елемент списку).
        for doc, section in self._filled_zones():
            for sample in section["author_text_samples"]:
                with self.subTest(doc=doc["file"], page=sample["page"]):
                    self.assertGreaterEqual(sample["word_count"], 15)
                    self.assertRegex(sample["fragment_sha256"], r"^[0-9a-f]{16}$")
                    self.assertGreater(sample["page"], 0)

    def test_author_text_samples_lie_within_body_when_structure_is_filled(self):
        # Тіло роботи = between first_chapter_page і conclusions_page
        # (§6.2, AUTHOR_TEXT). Перевіряється тільки якщо розділ structure
        # цього ж документа заповнений — розділи незалежні, тому zones не
        # повинен падати через відсутність чужих даних.
        for doc in _load()["documents"]:
            structure = doc["sections"]["structure"]
            zones = doc["sections"]["zones"]
            if structure["status"] != "filled" or zones["status"] != "filled":
                continue
            h = structure["headings"]
            with self.subTest(doc=doc["file"]):
                for sample in zones["author_text_samples"]:
                    self.assertGreaterEqual(sample["page"], h["first_chapter_page"])
                    self.assertLessEqual(sample["page"], h["conclusions_page"])

    def test_author_text_min_words_equals_sum_of_sample_word_counts(self):
        # §20.2: нижня межа — сума слів у вручну відмічених фрагментах, а не
        # знімок алгоритму. Перевіряємо внутрішню узгодженість фікстури.
        for doc, section in self._filled_zones():
            with self.subTest(doc=doc["file"]):
                expected = sum(s["word_count"] for s in section["author_text_samples"])
                self.assertEqual(section["author_text_min_words"], expected)
                self.assertGreater(section["author_text_min_words"], 0)


class TestBibliographySection(SectionStatusMixin, unittest.TestCase):
    """Розділ 'bibliography': спостережувана кількість записів і приклад
    зв'язку цитати `[N]` з бібліографічним записом (§12.5-12.6, без
    похідного `entry_id`)."""

    section_name = "bibliography"

    def _filled(self):
        for doc in _load()["documents"]:
            section = doc["sections"]["bibliography"]
            if section["status"] == "filled":
                yield doc, section

    def test_bibliography_entry_count_observed_is_plausible(self):
        # Дисертація має від десятків до кількасот джерел — це орієнтир
        # правдоподібності, а не вимога точності (§20.2).
        for doc, section in self._filled():
            with self.subTest(doc=doc["file"]):
                self.assertGreater(section["bibliography_entry_count_observed"], 20)
                self.assertLess(section["bibliography_entry_count_observed"], 1000)

    def test_citation_example_links_a_real_bibliography_ordinal_without_derived_id(self):
        for doc, section in self._filled():
            example = section["citation_example"]
            if example is None:
                continue
            with self.subTest(doc=doc["file"]):
                self.assertGreater(example["source_ordinal"], 0)
                self.assertRegex(example["bracket_text_sha256"], r"^[0-9a-f]{16}$")
                self.assertRegex(example["bibliography_entry_sha256"], r"^[0-9a-f]{16}$")
                # §22 крок 2 / §20.2: жодного похідного entry_id.
                self.assertNotIn("entry_id", example)


class TestCalquesSection(SectionStatusMixin, unittest.TestCase):
    """Розділ 'calques': позитивні й негативні вручну відмічені приклади
    §20.2, прив'язані до конкретного правила tools/measure_calques.py."""

    section_name = "calques"

    def _filled(self):
        for doc in _load()["documents"]:
            section = doc["sections"]["calques"]
            if section["status"] == "filled":
                yield doc, section

    def test_examples_reference_a_known_rule_id_from_measure_calques(self):
        # tools/measure_calques.py переписується лише на кроці 8 — тут ми
        # лише читаємо його поточний список id, не змінюючи файл.
        for doc, section in self._filled():
            for example in section["examples"]:
                with self.subTest(doc=doc["file"], page=example["page"]):
                    self.assertIn(example["rule_id"], _KNOWN_CALQUE_RULE_IDS)

    def test_examples_have_a_positive_or_negative_label(self):
        for doc, section in self._filled():
            for example in section["examples"]:
                with self.subTest(doc=doc["file"], page=example["page"]):
                    self.assertIn(example["label"], {"positive", "negative"})
                    self.assertRegex(example["fragment_sha256"], r"^[0-9a-f]{16}$")
                    self.assertGreater(example["page"], 0)

    def test_negative_absence_is_documented_with_a_reason(self):
        # §20.2 каже "позитивні Й негативні приклади, ЯКЩО ВОНИ Є" — умовно,
        # а не "мінімум один негатив завжди". Друге ревʼю кроку 2 показало,
        # що жорстка вимога хоча б одного негативу штовхає до фіктивних
        # прикладів (усі дев'ять "danyi"-негативів не матчили навіть тригерне
        # слово). Тому: якщо негативу серед прикладів немає — розділ мусить
        # явно нести причину (`negative_absent_reason`), а не мовчати
        # (CLAUDE.md правило №3). Якщо негатив є — стороннього поля-причини
        # бути не повинно (не лишати суперечливих слідів у фікстурі).
        for doc, section in self._filled():
            examples = section["examples"]
            has_negative = any(e["label"] == "negative" for e in examples)
            with self.subTest(doc=doc["file"]):
                if has_negative:
                    self.assertNotIn("negative_absent_reason", section)
                else:
                    self.assertIn("negative_absent_reason", section)
                    self.assertTrue(section["negative_absent_reason"])


class TestSectionInventory(unittest.TestCase):
    """Інвентар статусів по кожному документу і розділу — щоб пропуск
    (not_filled) було видно явно, а не розчинявся серед 9 файлів."""

    def test_inventory_lists_a_known_status_for_every_document_and_section(self):
        inventory: dict[str, dict[str, str]] = {}
        for doc in _load()["documents"]:
            inventory[doc["file"]] = {
                name: doc["sections"][name]["status"] for name in SECTION_NAMES
            }
        self.assertEqual(len(inventory), 9)
        for fname, statuses in inventory.items():
            with self.subTest(doc=fname):
                self.assertEqual(set(statuses.keys()), SECTION_NAMES)
                for status in statuses.values():
                    self.assertIn(status, {"filled", "not_filled"})


class TestFixtureMatchesActualFiles(unittest.TestCase):
    """
    Перераховує два найдешевші факти (хеш, кількість сторінок) прямо з
    examples/, щоб підмінений чи відредагований файл корпусу ловився
    одразу, не чекаючи на parser.searchdoc.
    """

    def setUp(self):
        self.payload = _load()

    def test_sha256_matches_file_on_disk(self):
        for doc in self.payload["documents"]:
            path = EXAMPLES_DIR / doc["file"]
            with self.subTest(doc=doc["file"]):
                self.assertTrue(path.is_file(), f"missing corpus file: {path}")
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, doc["sha256"])

    def test_page_count_matches_file_on_disk(self):
        for doc in self.payload["documents"]:
            path = EXAMPLES_DIR / doc["file"]
            with self.subTest(doc=doc["file"]):
                pdf = fitz.open(str(path))
                try:
                    self.assertEqual(pdf.page_count, doc["expected_pages"])
                finally:
                    pdf.close()


def _canonicalize(text: str) -> str:
    """Канонічна форма фрагмента — те саме правило, що записане в самій
    фікстурі (`fragment_canonical_form`): NFKC, видалений м'який перенос,
    склеєний перенос слова через дефіс на кінці рядка, схлопнуті пробіли,
    регістр ЗБЕРІГАЄТЬСЯ як у PDF (без .lower())."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fragment_hash(words: list[str]) -> str:
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()[:16]


class _PdfPageWordsCache:
    """Кеш токенізованих (канонічною формою) сторінок одного PDF, щоб не
    відкривати той самий файл повторно для кожного фрагмента документа."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._pages: dict[int, list[str]] = {}
        doc = fitz.open(str(path))
        try:
            self._page_count = doc.page_count
        finally:
            doc.close()

    def words(self, page_1based: int) -> list[str]:
        if page_1based not in self._pages:
            doc = fitz.open(str(self._path))
            try:
                raw = doc[page_1based - 1].get_text("text")
            finally:
                doc.close()
            self._pages[page_1based] = _canonicalize(raw).split()
        return self._pages[page_1based]


def _window_hash_found_on_page(cache: _PdfPageWordsCache, page: int, word_count: int, expect_hash: str) -> bool:
    """Лінійний перебір вікон довжини word_count на сторінці page: чи є
    вікно, чия sha256[:16] дорівнює expect_hash."""
    words = cache.words(page)
    n = len(words)
    if word_count <= 0 or word_count > n:
        return False
    for i in range(0, n - word_count + 1):
        if _fragment_hash(words[i:i + word_count]) == expect_hash:
            return True
    return False


class TestFragmentsAreRealSubstringsOfThePdf(unittest.TestCase):
    """Шлюз кроку 2 (доопрацювання після ревʼю): кожен хешований фрагмент
    фікстури дійсно є підрядком реального тексту вказаної сторінки
    вказаного PDF. Тест не реалізує жодної евристики розпізнавання (не
    підміняє search/*) — лише перевіряє присутність підрядка тим самим
    детермінованим способом (канонізація + вікно слів + sha256[:16]), яким
    ці хеші й були порахованu при побудові фікстури. Якщо хтось випадково
    чи навмисно впише в фікстуру фрагмент, якого немає в PDF (як сталося в
    першому проході кроку 2), цей тест впаде.
    """

    @classmethod
    def setUpClass(cls) -> None:
        with FIXTURE_PATH.open(encoding="utf-8") as f:
            cls.payload = json.load(f)
        cls._caches: dict[str, _PdfPageWordsCache] = {}

    def _cache_for(self, fname: str) -> _PdfPageWordsCache:
        if fname not in self._caches:
            self._caches[fname] = _PdfPageWordsCache(EXAMPLES_DIR / fname)
        return self._caches[fname]

    def test_every_word_count_is_within_the_45_word_ceiling(self):
        # Формальна перевірка самої стелі — гарантує, що жоден фрагмент не
        # вимагає перебору довше MAX_FRAGMENT_WORDS (§22 крок 2 doopr.).
        for doc in self.payload["documents"]:
            for wc in self._iter_all_word_counts(doc):
                with self.subTest(doc=doc["file"], word_count=wc):
                    self.assertLessEqual(wc, MAX_FRAGMENT_WORDS)

    @staticmethod
    def _iter_all_word_counts(doc):
        zones = doc["sections"]["zones"]
        if zones["status"] == "filled":
            for example in zones["zone_examples"].values():
                if example is not None:
                    yield example["word_count"]
            for sample in zones["author_text_samples"]:
                yield sample["word_count"]
        calques = doc["sections"]["calques"]
        if calques["status"] == "filled":
            for example in calques["examples"]:
                yield example["word_count"]
        bib = doc["sections"]["bibliography"]
        if bib["status"] == "filled" and bib.get("citation_example"):
            ce = bib["citation_example"]
            yield ce["bracket_word_count"]
            yield ce["bibliography_entry_word_count"]

    def test_zone_examples_are_real_substrings(self):
        for doc in self.payload["documents"]:
            zones = doc["sections"]["zones"]
            if zones["status"] != "filled":
                continue
            cache = self._cache_for(doc["file"])
            for zone_name, example in zones["zone_examples"].items():
                if example is None:
                    continue
                with self.subTest(doc=doc["file"], zone=zone_name):
                    found = _window_hash_found_on_page(
                        cache, example["page"], example["word_count"], example["fragment_sha256"]
                    )
                    self.assertTrue(
                        found,
                        f"{doc['file']}: zone_examples.{zone_name} "
                        f"(page={example['page']}, word_count={example['word_count']}) "
                        f"не знайдено на цій сторінці PDF",
                    )

    def test_author_text_samples_are_real_substrings(self):
        for doc in self.payload["documents"]:
            zones = doc["sections"]["zones"]
            if zones["status"] != "filled":
                continue
            cache = self._cache_for(doc["file"])
            for i, sample in enumerate(zones["author_text_samples"]):
                with self.subTest(doc=doc["file"], sample=i):
                    found = _window_hash_found_on_page(
                        cache, sample["page"], sample["word_count"], sample["fragment_sha256"]
                    )
                    self.assertTrue(
                        found,
                        f"{doc['file']}: author_text_samples[{i}] "
                        f"(page={sample['page']}, word_count={sample['word_count']}) "
                        f"не знайдено на цій сторінці PDF",
                    )

    def test_calque_examples_are_real_substrings(self):
        for doc in self.payload["documents"]:
            calques = doc["sections"]["calques"]
            if calques["status"] != "filled":
                continue
            cache = self._cache_for(doc["file"])
            for i, example in enumerate(calques["examples"]):
                with self.subTest(doc=doc["file"], example=i):
                    found = _window_hash_found_on_page(
                        cache, example["page"], example["word_count"], example["fragment_sha256"]
                    )
                    self.assertTrue(
                        found,
                        f"{doc['file']}: calques.examples[{i}] "
                        f"(rule_id={example['rule_id']}, page={example['page']}, "
                        f"word_count={example['word_count']}) не знайдено на цій сторінці PDF",
                    )

    def test_citation_example_bracket_and_entry_are_real_substrings(self):
        for doc in self.payload["documents"]:
            bib = doc["sections"]["bibliography"]
            if bib["status"] != "filled":
                continue
            ce = bib.get("citation_example")
            if ce is None or ce.get("page") is None:
                continue
            cache = self._cache_for(doc["file"])
            with self.subTest(doc=doc["file"], part="bracket_text"):
                found = _window_hash_found_on_page(
                    cache, ce["page"], ce["bracket_word_count"], ce["bracket_text_sha256"]
                )
                self.assertTrue(
                    found,
                    f"{doc['file']}: citation_example.bracket_text_sha256 "
                    f"(page={ce['page']}) не знайдено на цій сторінці PDF",
                )
            with self.subTest(doc=doc["file"], part="bibliography_entry"):
                found = _window_hash_found_on_page(
                    cache, ce["bibliography_page"], ce["bibliography_entry_word_count"],
                    ce["bibliography_entry_sha256"],
                )
                self.assertTrue(
                    found,
                    f"{doc['file']}: citation_example.bibliography_entry_sha256 "
                    f"(bibliography_page={ce['bibliography_page']}) не знайдено на цій сторінці PDF",
                )

    def test_calque_rule_actually_fires_on_positive_and_not_on_negative(self):
        # §22 крок 2 доопр.: rule_id має реально збігатись на positive і
        # реально НЕ збігатись на negative — прогін спільного виконуваного
        # словника search.calques по реальному вікну слів, а не "на очі".
        for doc in self.payload["documents"]:
            calques = doc["sections"]["calques"]
            if calques["status"] != "filled":
                continue
            cache = self._cache_for(doc["file"])
            for i, example in enumerate(calques["examples"]):
                words = cache.words(example["page"])
                n = example["word_count"]
                match_text = None
                for j in range(0, len(words) - n + 1):
                    if _fragment_hash(words[j:j + n]) == example["fragment_sha256"]:
                        match_text = " ".join(words[j:j + n])
                        break
                with self.subTest(doc=doc["file"], example=i):
                    self.assertIsNotNone(match_text)
                    normalized_text = normalize_text(match_text)
                    block = SearchBlock(
                        block_id="corpus-calque-example",
                        raw_text=match_text,
                        normalized=normalized_text,
                        tokens=tokenize(match_text, normalized_text),
                        section_id="corpus",
                        heading_path=(),
                        physical_page=example["page"],
                        block_index=0,
                        zone_spans=(ZoneSpan(
                            0, len(match_text), TextZone.AUTHOR_TEXT,
                            Confidence.HIGH, "manual-corpus-example",
                        ),),
                    )
                    matched = any(
                        hit.rule_id == example["rule_id"] for hit in find_calques(block)
                    )
                    if example["label"] == "positive":
                        self.assertTrue(matched, f"{doc['file']} calques[{i}]: правило не спрацювало на positive")
                    else:
                        self.assertFalse(matched, f"{doc['file']} calques[{i}]: правило хибно спрацювало на negative")
                        # Мало того, що правило не спрацювало — тригерне
                        # слово мусить бути в тексті, інакше "негатив" ні
                        # про що не свідчить (§22 крок 2 доопр., див.
                        # NEGATIVE_TRIGGER_PATTERNS).
                        trigger = NEGATIVE_TRIGGER_PATTERNS[example["rule_id"]]
                        provoked = bool(re.search(trigger, normalized_text.text.casefold()))
                        self.assertTrue(
                            provoked,
                            f"{doc['file']} calques[{i}]: негативний приклад не містить "
                            f"тригерного слова правила {example['rule_id']} — він нічого не доводить",
                        )

    def test_bibliography_entry_fragment_does_not_swallow_the_next_numbered_entry(self):
        # §22 крок 2 доопр. (БЛОКУЮЧЕ 1): у першому проході bibliography_entry
        # був штучно дотягнутий до квоти в 12 слів і захоплював початок
        # НАСТУПНОГО пронумерованого запису. Ловимо це так: у відновленому
        # вікні (окрім самого першого токена — де може легально стояти
        # власний номер запису, якщо він у PDF злитий з першим словом без
        # пробілу) не повинно траплятися "голого" маркера виду "<1-3 цифри>."
        # — саме так виглядає початок СУСІДНЬОГО запису
        # ("... 3. Human" у зіпсованому прикладі). 1–3 цифри (не 4) свідомо:
        # роки видань завжди 4-значні ("1989.", "2010.") і не повинні
        # хибно спрацьовувати — жодна з дев'яти бібліографій цього корпусу
        # не має 1000+ джерел (bibliography_entry_count_observed ≤ 505).
        bare_next_entry_marker = re.compile(r"^\d{1,3}\.$")
        for doc in self.payload["documents"]:
            bib = doc["sections"]["bibliography"]
            if bib["status"] != "filled":
                continue
            ce = bib.get("citation_example")
            if ce is None or ce.get("page") is None:
                continue
            cache = self._cache_for(doc["file"])
            words = cache.words(ce["bibliography_page"])
            n = ce["bibliography_entry_word_count"]
            match_words = None
            for j in range(0, len(words) - n + 1):
                if _fragment_hash(words[j:j + n]) == ce["bibliography_entry_sha256"]:
                    match_words = words[j:j + n]
                    break
            with self.subTest(doc=doc["file"]):
                self.assertIsNotNone(match_words)
                offenders = [w for w in match_words[1:] if bare_next_entry_marker.match(w)]
                self.assertEqual(
                    offenders, [],
                    f"{doc['file']}: bibliography_entry_sha256 захоплює маркер "
                    f"наступного запису {offenders} — межа запису переїхала",
                )


class TestFixtureCannotBeOverwrittenByProductCode(unittest.TestCase):
    """Шлюз §22 крок 2: 'алгоритм не может перезаписать фикстуру'. Жоден
    продуктовий модуль (search/, parser/, tools/, compare/, app.py,
    ui_helpers.py) не згадує ім'я файлу фікстури — отже, не може її ані
    прочитати як джерело істини, ані відкрити на запис."""

    def test_no_product_module_mentions_the_fixture_file_name(self):
        offenders = []
        for path in _iter_product_py_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")
            if "search_corpus_expectations" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders, [],
            f"продуктовий код згадує фікстуру (заборонено §22 крок 2): {offenders}",
        )

    def test_scan_actually_covers_the_expected_product_roots(self):
        # Захист від тихого "порожнього" скану: якщо хтось перейменує
        # search/ чи видалить app.py, цей тест впаде раніше, ніж шлюз
        # почне мовчки нічого не перевіряти.
        scanned = list(_iter_product_py_files())
        self.assertGreater(len(scanned), 0)
        scanned_names = {p.name for p in scanned}
        self.assertIn("app.py", scanned_names)


if __name__ == "__main__":
    unittest.main()
