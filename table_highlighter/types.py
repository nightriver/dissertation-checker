"""Типи даних модуля підсвічування таблиць DOCX."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HighlightOptions:
    """Явні налаштування одного запуску обробки."""

    threshold: int = 75
    font_name: str = "Calibri"
    font_size: int = 14
    table_index: int = 0
    first_row: int = 1
    last_row: int | None = None
    relax_short_words: bool = True


@dataclass(frozen=True)
class TableSummary:
    """Структура таблиці, доступної для вибору в інтерфейсі."""

    index: int
    row_count: int
    two_column_rows: int


@dataclass(frozen=True)
class RowWarning:
    """Причина, через яку рядок залишено без змін."""

    row_number: int
    message: str


@dataclass(frozen=True)
class HighlightStats:
    """Підсумок обробки без приховування пропущених рядків."""

    processed_rows: int = 0
    skipped_rows: int = 0
    exact_words: int = 0
    fuzzy_words: int = 0
    padding_paragraphs: int = 0


@dataclass(frozen=True)
class HighlightResult:
    """Готовий DOCX і повна діагностика одного запуску."""

    document_bytes: bytes
    stats: HighlightStats
    warnings: tuple[RowWarning, ...] = field(default_factory=tuple)


class DocumentValidationError(ValueError):
    """Завантажений файл не є придатним DOCX."""
