"""
duplicates.py
Пошук повторів у списку літератури за полем НАЗВИ.

Порівнювати цілі записи не можна: дві різні статті одного автора в одному
журналі за один рік дають дуже високу схожість рядків через службові поля
ДСТУ («// Вісник … . 2019. С. 12–18»). Тому спершу виділяємо назву.

Модуль — чисті функції над словниками й рядками.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, Optional

from parser.types import Severity
from parser.text_forensics import normalize_mixed_homoglyphs


# ---------------------------------------------------------------------------
# 1. Виділення назви
# ---------------------------------------------------------------------------

# Блок авторів на початку запису: «Прізвище І. І.», «ПРІЗВИЩЕ І. І.»,
# латинський «Surname A. B.». Ініціалів може бути один або два.
_AUTHOR_RE = re.compile(
    r'^\s*'
    r'(?:[А-ЯЁЇІЄҐA-Z][а-яёїієґa-z\'’ʼ-]+|[А-ЯЁЇІЄҐA-Z]{2,})'
    r'\s+[А-ЯЁЇІЄҐA-Z]\.\s*(?:[А-ЯЁЇІЄҐA-Z]\.)?'
    r'\s*[,;]?\s*',
    re.UNICODE,
)

# Розділювачі полів ДСТУ: усе після першого з них — уже не назва.
_DSTU_SEPARATORS = (' : ', ' // ', ' / ', ' ; ')

# Мінімальна довжина голови запису, після якої «. » перед великою літерою
# вважається кінцем назви, а не крапкою всередині скорочення.
_MIN_HEAD_LEN = 15

# Нижче цієї довжини сегментація вважається невдалою.
_MIN_TITLE_LEN = 10

_SENTENCE_BREAK_RE = re.compile(r'\.\s+(?=[А-ЯЁЇІЄҐA-Z])', re.UNICODE)

_PUNCT_RE = re.compile(r'[^\w\s]', re.UNICODE)
_SPACE_RE = re.compile(r'\s+')

_STOPWORDS = {
    "та", "і", "в", "у", "на", "для", "з", "до",
    "the", "of", "and",
}

TitleMethod = Literal["dstu", "full"]


@dataclass(frozen=True)
class BibliographicKey:
    author: str
    year: int | None
    title: str


def normalize_title(title: str) -> str:
    """Нижній регістр, без пунктуації, без стоп-слів і однолітерних токенів."""
    text = _PUNCT_RE.sub(" ", title.lower())
    tokens = [
        token for token in _SPACE_RE.split(text)
        if len(token) > 1 and token not in _STOPWORDS
    ]
    return " ".join(tokens)


def extract_title(entry: str) -> tuple[str, TitleMethod]:
    """
    Повертає (назва, метод), де метод:
      'dstu' — назву виділено за структурою ДСТУ 8302:2015;
      'full' — сегментація не вдалася, повернуто нормалізований повний запис.
    """
    head = entry.strip()

    # 1. Зняти блок авторів на початку — повторно, поки знімається
    #    (авторів може бути кілька через кому).
    while True:
        match = _AUTHOR_RE.match(head)
        if not match or match.end() == 0:
            break
        head = head[match.end():]

    # 2. Обрізати за першим розділювачем ДСТУ.
    cut = len(head)
    for sep in _DSTU_SEPARATORS:
        pos = head.find(sep)
        if pos != -1:
            cut = min(cut, pos)
    head = head[:cut]

    # 3. Обрізати за «. » перед великою літерою, якщо голова достатньо довга.
    #    Ініціали вже знято на кроці 1, тож ризику розрізати «І. І.» немає.
    if len(head) > _MIN_HEAD_LEN:
        match = _SENTENCE_BREAK_RE.search(head)
        if match and match.start() >= _MIN_HEAD_LEN:
            head = head[:match.start()]

    # 4. Сегментація не вдалася — від назви лишилось замало змістовного тексту.
    #    Довжину міряємо ПІСЛЯ нормалізації: саме нормалізований рядок і
    #    порівнюється далі, а «12345 --- ???» до нормалізації виглядає довгим.
    normalized = normalize_title(head)
    if len(normalized) < _MIN_TITLE_LEN:
        return normalize_title(entry), "full"

    return normalized, "dstu"


def extract_author(entry: str) -> str:
    """Окремий сумісний API автора; контракт ``extract_title`` не змінює."""
    head = normalize_mixed_homoglyphs(entry.strip())
    authors: list[str] = []
    while True:
        match = _AUTHOR_RE.match(head)
        if not match or match.end() == 0:
            break
        authors.append(match.group(0).strip(" ,;"))
        head = head[match.end():]
    return normalize_title(" ".join(authors))


def make_bibliographic_key(entry: str) -> BibliographicKey:
    """Окремий ключ «автор + рік + назва» для міждокументного зіставлення."""
    from parser.year_extractor import extract_year

    normalized_entry = normalize_mixed_homoglyphs(entry)
    title, _method = extract_title(normalized_entry)
    return BibliographicKey(
        author=extract_author(normalized_entry),
        year=extract_year(normalized_entry),
        title=title,
    )


# ---------------------------------------------------------------------------
# 2. Порівняння
# ---------------------------------------------------------------------------

DuplicateKind = Literal["exact", "near", "same_title_diff_year"]

# Поріг near-схожості. Перевірено на реальних записах ДСТУ: дві різні статті
# одного автора («цифрових платформ» / «цифрових ринків») дають ratio ≈ 0.83,
# тобто запас до 0.90 достатній.
DEFAULT_THRESHOLD = 0.90

# Передфільтри перед SequenceMatcher — інакше на 500 джерелах це 125 тис.
# викликів.
_MIN_LENGTH_RATIO = 0.7
_MIN_JACCARD = 0.4


@dataclass
class DuplicateGroup:
    numbers: list[int]
    kind: DuplicateKind
    similarity: float
    severity: Severity


def _jaccard(a: set[str], b: set[str]) -> float:
    """Обидві множини непорожні: порожні назви відсіюються до виклику."""
    return len(a & b) / len(a | b)


class _UnionFind:
    """Транзитивне групування: 12≈187 і 187≈45 → одна група [12, 45, 187]."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        self._parent.setdefault(item, item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_b] = root_a


# Пріоритет типів усередині групи: точний збіг важливіший за near.
_KIND_RANK: dict[str, int] = {
    "exact": 3,
    "same_title_diff_year": 2,
    "near": 1,
}


def find_duplicates(
    bibliography: dict[int, str],
    years: dict[int, Optional[int]],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[DuplicateGroup]:
    """
    Повертає групи записів-повторів.

    Усе на рівні SUSPECT: дубль може бути й помилкою оформлення (два видання
    однієї монографії), тож останнє слово за експертом.
    """
    titles: dict[int, tuple[str, str]] = {
        num: extract_title(entry) for num, entry in bibliography.items()
    }
    tokens: dict[int, set[str]] = {
        num: set(title.split()) for num, (title, _) in titles.items()
    }

    numbers = sorted(bibliography)
    union = _UnionFind()
    pair_kinds: dict[tuple[int, int], tuple[str, float]] = {}

    for i, left in enumerate(numbers):
        title_l, method_l = titles[left]
        if not title_l:
            continue
        for right in numbers[i + 1:]:
            title_r, method_r = titles[right]
            if not title_r:
                continue

            if title_l == title_r:
                kind = (
                    "exact" if years.get(left) == years.get(right)
                    else "same_title_diff_year"
                )
                pair_kinds[(left, right)] = (kind, 1.0)
                union.union(left, right)
                continue

            # Near-порівняння — лише для пар, де ОБИДВІ назви виділено за ДСТУ.
            # У методі 'full' левову частку рядка займають службові поля,
            # однакові в усіх статтях одного журналу: дві різні статті одного
            # автора дали б там ratio під 0.90 на самій лише службовій частині.
            if method_l != "dstu" or method_r != "dstu":
                continue

            shorter, longer = sorted((len(title_l), len(title_r)))
            if shorter / longer < _MIN_LENGTH_RATIO:
                continue
            if _jaccard(tokens[left], tokens[right]) < _MIN_JACCARD:
                continue

            ratio = SequenceMatcher(None, title_l, title_r).ratio()
            if ratio >= threshold:
                pair_kinds[(left, right)] = ("near", ratio)
                union.union(left, right)

    if not pair_kinds:
        return []

    grouped: dict[int, set[int]] = {}
    for left, right in pair_kinds:
        grouped.setdefault(union.find(left), set()).update((left, right))

    groups: list[DuplicateGroup] = []
    for members in grouped.values():
        pairs = [
            (kind, ratio)
            for (left, right), (kind, ratio) in pair_kinds.items()
            if left in members and right in members
        ]
        kind = max(pairs, key=lambda item: _KIND_RANK[item[0]])[0]
        similarity = min(ratio for _, ratio in pairs)
        groups.append(DuplicateGroup(
            numbers=sorted(members),
            kind=kind,
            similarity=similarity,
            severity=Severity.SUSPECT,
        ))

    groups.sort(key=lambda group: group.numbers[0])
    return groups
