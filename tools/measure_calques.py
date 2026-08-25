#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_calques.py — воспроизводимое измерение калек с русского.

Один словарь, одно правило подсчёта, одна зона текста. Все числа в
PLAN_SEARCH.md получены этим скриптом; без него они невоспроизводимы.

Считается ТОЛЬКО авторский текст: от заголовка ВСТУП до заголовка списка
литературы. Титул, анотація, ЗМІСТ, список публикаций и библиография
исключаются — именно так будет работать приложение, поэтому и пороги
калибруются на той же зоне.

Использование:
    python tools/measure_calques.py file1.pdf file2.pdf ...
    python tools/measure_calques.py --txt dump1.txt dump2.txt

Требует pdftotext (poppler-utils) либо готовые .txt.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

DICT_VERSION = "calques-2026-08-25"

# --------------------------------------------------------------------------
# Зоны
# --------------------------------------------------------------------------

_BODY_START = re.compile(r"^\s*ВСТУП\s*$", re.M)
_BIBLIO_START = re.compile(
    r"^\s*СПИСОК\s+(ВИКОРИСТАН|ЛІТЕРАТУР)|^\s*СПИСОК\s*$|^\s*ЛІТЕРАТУРА\s*$"
    r"|^\s*БІБЛІОГРАФІЯ\s*$",
    re.M,
)
_PUBLICATIONS = re.compile(
    r"^\s*СПИСОК\s+(ОПУБЛІКОВАНИХ|ПУБЛІКАЦІЙ)", re.M
)


def split_body_biblio(text: str) -> tuple[str, str]:
    """(авторский текст, список литературы). Обе зоны могут быть пустыми."""
    starts = [m.start() for m in _BODY_START.finditer(text)]
    # первый ВСТУП — в ЗМІСТі, берём последний из первых двух вхождений
    body_start = starts[-1] if len(starts) == 1 else (starts[1] if len(starts) > 1 else 0)
    ends = [m.start() for m in _BIBLIO_START.finditer(text) if m.start() > body_start]
    body_end = ends[0] if ends else len(text)
    biblio_end = len(text)
    pubs = [m.start() for m in _PUBLICATIONS.finditer(text) if m.start() > body_end]
    if pubs:
        biblio_end = pubs[0]
    return text[body_start:body_end], text[body_end:biblio_end]


# --------------------------------------------------------------------------
# Нормализация
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "").replace("’", "'").replace("ʼ", "'")
    text = re.sub(r"-\s*\n\s*", "", text)          # перенос через дефис
    text = re.sub(r"\s+", " ", text)
    return text.lower()


# --------------------------------------------------------------------------
# Словарь
# --------------------------------------------------------------------------
# tier 1 — ненормативные формы, сильный признак перевода
# tier 2 — распространённые канцеляризмы, считаются отдельно
# tier 3 — только плотность, в сумму уровней 1–2 не входят
#
# Прилагательные окончания: формы на -ючи/-уючи (деепричастия) НЕ ловятся.

ADJ = r"(?:ий|ій|а|я|е|є|і|ого|ої|ому|им|ими|их|ім|у|ю)"

CALQUES: list[tuple[str, str, str, str, int]] = [
    # (id, регулярка, рус. оригинал, норма, tier)
    ("yavlyaietsya", r"(?<![а-яіїєґ'])явля[єе]ться\b", "является", "є", 1),
    ("u_vidpovidnosti", r"\b[ву]\s+відповідност[іи]\s+(?:до|з|із|зі)\b",
     "в соответствии с", "відповідно до", 1),
    ("na_protyazi", r"\bна\s+протязі\b", "в течение", "протягом", 1),
    ("pryimaty_uchast", r"\bприйма\w{0,3}\s+участь\b|\bприйня\w{0,4}\s+участь\b",
     "принимать участие", "брати участь", 1),
    ("zakliuchaietsya", r"(?<![а-яіїєґ'])заключа[єе]ться\b", "заключается", "полягає", 1),
    ("spivpadaty", r"\bспівпада\w+|\bспівпад[іi]нн\w+", "совпадать", "збігатися", 1),
    ("sliduiuchyi", r"\bслідуюч" + ADJ + r"\b", "следующий", "наступний", 1),
    ("u_yakosti", r"\bу\s+якост[іи]\s+(?!особи|доказ|свідк|потерпіл|підозрюв|обвинувач)",
     "в качестве", "як", 1),
    ("po_vidnoshenniu", r"\bпо\s+відношенню\s+до\b", "по отношению к", "щодо", 1),
    ("ne_dyvlyachys", r"\bне\s+дивлячись\s+на\b", "несмотря на", "незважаючи на", 1),
    ("pryishly_vysnovku", r"\bприйш\w+\s+до\s+висновк\w+", "пришли к выводу",
     "дійшли висновку", 1),
    ("pryimaty_miry", r"\bприйня\w+\s+мір\w+|\bприйма\w+\s+мір\w+",
     "принимать меры", "вживати заходів", 1),
    ("vytikaie", r"\bвитіка[єе]\s+(?:з|із)\b", "вытекает из", "випливає з", 1),
    ("spivstavlennia", r"\bспівставл\w+", "сопоставление", "зіставлення", 1),
    ("uchbovyi", r"\bучбов" + ADJ + r"\b", "учебный", "навчальний", 1),
    ("vidzyv", r"\bвідзив\w*\b", "отзыв", "відгук", 1),
    ("miropryiemstvo", r"\bміроприємств\w+", "мероприятие", "захід", 1),
    # лексемы-кальки в форме прилагательного. НЕ правило по суффиксу:
    # проверенный список конкретных слов, см. примечание ниже.
    ("diiuchyi", r"\bдіюч" + ADJ + r"\b", "действующий", "чинний", 1),
    ("isnuiuchyi", r"\bіснуюч" + ADJ + r"\b", "существующий", "наявний", 1),
    ("otochuiuchyi", r"\bоточуюч" + ADJ + r"\b", "окружающий", "навколишній", 1),

    # tier 2 — канцеляризмы
    ("v_zalezhnosti", r"\bв\s+залежност[іи]\s+від\b", "в зависимости от", "залежно від", 2),
    ("v_yakosti", r"\bв\s+якост[іи]\b", "в качестве", "як", 2),
    ("pry_naiavnosti", r"\bпри\s+наявност[іи]\b", "при наличии", "за наявності", 2),
    ("pry_umovi", r"\bпри\s+умов[іи]\b", "при условии", "за умови", 2),
    ("z_ciliu", r"\bз\s+ціллю\b", "с целью", "з метою", 2),
    ("za_vykliuchenniam", r"\bза\s+виключенн\w+", "за исключением", "за винятком", 2),
    ("nosyt_kharakter", r"\bносит[ья]\s+\w+\s+характер", "носит характер", "має характер", 2),
    ("yavlyaie_soboiu", r"\bявля[єе]\s+собою\b", "представляет собой", "є", 2),
    ("vidnosytsya_do", r"\bвіднос[ия]т[ья]?ся\s+до\b", "относится к", "належить до", 2),
    ("maie_mistse", r"\bма[єю]т[ья]?\s+місце\b|\bмає\s+місце\b|\bмало\s+місце\b",
     "иметь место", "траплятися", 2),
    ("u_pershu_chergu", r"\b[ву]\s+першу\s+чергу\b", "в первую очередь", "насамперед", 2),
    ("na_sohodnishnii", r"\bна\s+сьогоднішній\s+день\b", "на сегодняшний день", "нині", 2),
    ("u_porivnyanni", r"\b[ву]\s+порівнянн[іи]\s+з", "по сравнению с", "порівняно з", 2),
    ("z_tochky_zoru", r"\bз\s+точки\s+зору\b", "с точки зрения", "з погляду", 2),
    ("v_ostannii_chas", r"\bв\s+останн[іи]й\s+час\b", "в последнее время", "останнім часом", 2),
    ("na_danyi_moment", r"\bна\s+даний\s+момент\b", "на данный момент", "нині", 2),
    ("mova_ide", r"\bмова\s+йде\s+про\b", "речь идёт о", "ідеться про", 2),
    ("po_svoii_suti", r"\bпо\s+своїй\s+сут[іи]\b", "по своей сути", "за своєю суттю", 2),
    ("tym_ne_menshe", r"\bтим\s+не\s+менше\b", "тем не менее", "проте", 2),
    ("v_tsilomu", r"\bв\s+цілому\b", "в целом", "загалом", 2),
    ("zadacha", r"\bзада(?:чі|чу|чами|чах|ч)\b", "задача", "завдання", 2),
    ("vstupaie_v_sylu", r"\bвступа\w+\s+в\s+(?:законну\s+)?силу\b",
     "вступает в силу", "набирає чинності", 2),
    # спорные лексемы — понижены до 2 и требуют вычитки филолога
    ("dominuiuchyi", r"\bдомінуюч" + ADJ + r"\b", "доминирующий", "панівний", 2),
    ("panuiuchyi", r"\bпануюч" + ADJ + r"\b", "господствующий", "панівний", 2),
    ("zrostaiuchyi", r"\bзростаюч" + ADJ + r"\b", "растущий", "дедалі більший", 2),
    ("uzahalniuiuchyi", r"\bузагальнююч" + ADJ + r"\b", "обобщающий", "узагальнювальний", 2),
    ("utochniuiuchyi", r"\bуточнююч" + ADJ + r"\b", "уточняющий", "уточнювальний", 2),
    ("vyrishuiuchyi", r"\bвирішуюч" + ADJ + r"\b", "решающий", "вирішальний", 2),
    ("vyznachaiuchyi", r"\bвизначаюч" + ADJ + r"\b", "определяющий", "визначальний", 2),

    # tier 3 — только плотность
    ("danyi", r"\bдан(?:ий|а|е|ої|ому|им|их|ими|і)\b", "данный", "цей", 3),
    ("razom_z_tym", r"\bразом\s+з\s+тим\b", "вместе с тем", "водночас", 3),
    ("pry_tsiomu", r"\bпри\s+цьому\b", "при этом", "водночас", 3),
    ("takym_chynom", r"\bтаким\s+чином\b", "таким образом", "отже", 3),
    ("vykhodyachy", r"\bвиходячи\s+(?:з|із)\b", "исходя из", "з огляду на", 3),
    ("persh_za_vse", r"\bперш\s+за\s+все\b", "прежде всего", "насамперед", 3),
]

# ИСКЛЮЧЕНО СОЗНАТЕЛЬНО (нормативный украинский, не кальки):
#   приймати закон    — Конституція України, ст. 91: «Верховна Рада приймає закони»
#   приймати рішення  — стандартная формула украинских нормативных актов
#   нести відповідальність — используется в законодательстве
#   головуючий, працюючий, виконуючий обов'язки, керуючий — термины КПК и КЗпП
#   формы на -ючи/-уючи — деепричастия, норма языка

STOP_PARTICIPLES = re.compile(
    r"\b(?:головуюч|працююч|виконуюч|керуюч|правляч|захищаюч|обвинувачуюч)" + ADJ + r"\b"
)


def count(text: str) -> tuple[Counter, dict[int, int]]:
    hits: Counter = Counter()
    by_tier = {1: 0, 2: 0, 3: 0}
    for cid, pattern, _ru, _norm, tier in CALQUES:
        c = len(re.findall(pattern, text))
        if c:
            hits[cid] = c
            by_tier[tier] += c
    return hits, by_tier


# --------------------------------------------------------------------------
# Язык библиографической записи
# --------------------------------------------------------------------------
# Украинская запись почти всегда содержит і/ї/є/ґ. Русская — никогда.
# Это даёт куда выше полноту, чем поиск букв ы/э/ъ/ё, которых в «Теория
# права» просто нет.

_UA_LETTERS = re.compile(r"[іїєґ]")
_RU_LETTERS = re.compile(r"[ыэъё]")
_CYR_WORD = re.compile(r"[а-яіїєґё]{3,}")
# Номер записи может стоять на отдельной строке — pdftotext часто так и делает.
_ENTRY = re.compile(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})[.)])\s*(.*)$")


def entry_language(entry: str) -> str:
    """'ru' | 'ua' | 'other' — по буквам, без словарей."""
    cyr = _CYR_WORD.findall(entry.lower())
    if len(cyr) < 3:
        return "other"                     # латиница, слишком короткая запись
    if _RU_LETTERS.search(entry.lower()):
        return "ru"
    if _UA_LETTERS.search(entry.lower()):
        return "ua"
    return "ru"                            # кириллица без і/ї/є и без ы/э/ъ/ё


def parse_entries(biblio: str) -> list[str]:
    entries: list[str] = []
    current: list[str] = []
    expected = 1
    for raw in biblio.split("\n"):
        line = raw.strip()
        m = _ENTRY.match(line)
        num = int(m.group(1) or m.group(2)) if m else None
        # продолжение записи, начавшееся с числа («2019. – С. 12–18»), номером
        # не считается: принимаем только ожидаемый следующий номер
        if num is not None and (num == expected or expected < num <= expected + 3):
            if current:
                entries.append(" ".join(x for x in current if x))
            expected = num
            current = [m.group(3)]
        elif current:
            current.append(line)
    if current:
        entries.append(" ".join(current))
    return [e for e in entries if len(e) > 20]


# --------------------------------------------------------------------------

# Заголовок раздела: «РОЗДІЛ 2» может стоять один или с названием на той же
# строке — в PDF встречаются оба варианта. Строка из точек (ЗМІСТ) отсекается
# тем, что зона содержания в тело не попадает.
_SECTION = re.compile(
    r"^\s*(ВСТУП|ВИСНОВКИ|РОЗДІЛ\s*[IVX\d]+)\b.*$", re.M
)


def split_sections(body_raw: str) -> list[tuple[str, str]]:
    """[(название раздела, текст)] по заголовкам ВСТУП / РОЗДІЛ n / ВИСНОВКИ."""
    marks = [(m.start(), m.group(1).strip()) for m in _SECTION.finditer(body_raw)]
    if not marks:
        return [("УВЕСЬ ТЕКСТ", body_raw)]
    out = []
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body_raw)
        out.append((name, body_raw[pos:end]))
    return out


def to_text(path: Path) -> str:
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    out = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(path), "-"],
        capture_output=True, check=True,
    )
    return out.stdout.decode("utf-8", errors="ignore")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print(f"словник: {DICT_VERSION}\n")
    header = f"{'файл':<28}{'слів':>8}{'рів.1':>7}{'рів.2':>7}{'рів.3':>7}" \
             f"{'щільн.1':>9}{'записів':>9}{'рос.%':>7}"
    print(header)
    print("-" * len(header))

    for path in args.files:
        raw = to_text(path)
        body_raw, biblio_raw = split_body_biblio(raw)
        body = normalize(body_raw)
        words = len(body.split())
        hits, tiers = count(body)
        density = 1000 * tiers[1] / words if words else 0.0

        entries = parse_entries(biblio_raw)
        langs = Counter(entry_language(e) for e in entries)
        ru_share = 100 * langs["ru"] / len(entries) if entries else 0.0

        print(f"{path.stem[:27]:<28}{words:>8}{tiers[1]:>7}{tiers[2]:>7}"
              f"{tiers[3]:>7}{density:>9.2f}{len(entries):>9}{ru_share:>6.1f}%")

        if args.verbose:
            stop = len(STOP_PARTICIPLES.findall(body))
            print(f"    нормативні дієприкметники (не рахуються): {stop}")
            for cid, c in hits.most_common(12):
                spec = next(x for x in CALQUES if x[0] == cid)
                print(f"    {cid:<22}{c:>5}   {spec[2]} → {spec[3]}  (рів. {spec[4]})")
            print(f"    записи: ru={langs['ru']} ua={langs['ua']} інші={langs['other']}")
            print("    за розділами (рівень 1):")
            for name, chunk in split_sections(body_raw):
                sec = normalize(chunk)
                _, st = count(sec)
                w = len(sec.split())
                if w > 500:
                    print(f"      {name:<16}{st[1]:>4}  ({1000*st[1]/w:.2f}/1000, {w} слів)")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
