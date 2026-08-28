"""
Механічні правила проєкту, перевірені кодом, а не очима ревʼюера.

Кожна перевірка тут — це пункт `CLAUDE.md`, який раніше ревʼюер виводив
заново на кожному кроці §22. Тепер їх дає звичайний прогін `pytest`, тому
`plan-reviewer` займається лише змістом кроку.

Правило: якщо перевірку можна виразити кодом — її місце тут, а не в
інструкції агента. Додаючи жорстке правило в `CLAUDE.md`, додай сюди тест.
"""

from __future__ import annotations

import ast
import io
import json
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIR = ROOT / "search"
TESTS_DIR = ROOT / "tests"
SELF = Path(__file__).resolve()

# Модулі, які пишуться українською (код, docstrings, коментарі).
UKRAINIAN_SOURCES = sorted(SEARCH_DIR.glob("*.py")) + [
    ROOT / "parser" / "searchdoc.py",
    ROOT / "ui_helpers.py",
]

RU_ONLY_LETTERS = set("ыэъёЫЭЪЁ")

# Маркер для рядків, де російські літери — це дані (алфавіт для визначення
# мови, нормативні скорочення), а не текст, написаний російською.
RU_DATA_MARKER = "# ru-data"


def _existing(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.is_file()]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _comments(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            out.append((tok.start[0], tok.string))
    return out


def _docstrings(path: Path) -> list[tuple[int, str]]:
    tree = _parse(path)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                line = getattr(node, "lineno", 1)
                out.append((line, doc))
    return out


def _imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


# --------------------------------------------------------------------------
# CLAUDE.md, правило №4 — абзаци PDF не будуються через get_text("blocks")
# --------------------------------------------------------------------------


def test_no_pdf_blocks_api_in_search_code() -> None:
    offenders: list[str] = []
    for path in _existing(sorted(SEARCH_DIR.glob("*.py")) + [ROOT / "parser" / "searchdoc.py"]):
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get_text":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "blocks":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "CLAUDE.md №4: абзаци беруться з get_text('dict') і відновлюються вручну. "
        f"Виклики get_text('blocks'): {offenders}"
    )


# --------------------------------------------------------------------------
# CLAUDE.md, правило №6 — запити будуються зі словоформ, без лематизації
# --------------------------------------------------------------------------


def test_no_lemmatizer_in_search() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _existing(sorted(SEARCH_DIR.glob("*.py")))
        if any(name.startswith("pymorphy") for name in _imported_names(path))
    ]
    assert not offenders, f"CLAUDE.md №6: pymorphy3 у режимі пошуку не використовується: {offenders}"


# --------------------------------------------------------------------------
# CLAUDE.md, правило №1 — детермінізм і відсутність LLM
# --------------------------------------------------------------------------

FORBIDDEN_CALLS = {
    ("datetime", "now"),
    ("date", "today"),
    ("time", "time"),
}
FORBIDDEN_IMPORTS = {"random", "uuid", "secrets", "openai", "anthropic", "transformers", "torch", "langchain"}


def test_search_has_no_hidden_nondeterminism() -> None:
    offenders: list[str] = []
    for path in _existing(sorted(SEARCH_DIR.glob("*.py"))):
        bad_imports = _imported_names(path) & FORBIDDEN_IMPORTS
        if bad_imports:
            offenders.append(f"{path.relative_to(ROOT)}: import {sorted(bad_imports)}")
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name) and (owner.id, node.func.attr) in FORBIDDEN_CALLS:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {owner.id}.{node.func.attr}()")
    assert not offenders, (
        "CLAUDE.md №1: результат має бути відтворюваним. Поточний час і випадковість "
        f"інʼєктуються аргументом, а не беруться всередині: {offenders}"
    )


# --------------------------------------------------------------------------
# CLAUDE.md, мова: код і коментарі українською
# --------------------------------------------------------------------------


def test_ukrainian_sources_have_no_russian_letters_in_prose() -> None:
    offenders: list[str] = []
    for path in _existing(UKRAINIAN_SOURCES):
        for lineno, text in _comments(path):
            if RU_DATA_MARKER in text:
                continue
            found = sorted(set(text) & RU_ONLY_LETTERS)
            if found:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} коментар {found}")
        for lineno, doc in _docstrings(path):
            if RU_DATA_MARKER in doc:
                continue
            found = sorted(set(doc) & RU_ONLY_LETTERS)
            if found:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} docstring {found}")
    assert not offenders, (
        "CLAUDE.md, мова: docstrings і коментарі — українською. "
        f"Російські літери: {offenders}"
    )


def test_search_modules_reference_the_plan() -> None:
    offenders: list[str] = []
    for path in _existing(sorted(SEARCH_DIR.glob("*.py"))):
        doc = ast.get_docstring(_parse(path)) or ""
        if "PLAN_SEARCH.md" not in doc:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "Кожен модуль search/ починається з docstring із посиланням на секцію "
        f"PLAN_SEARCH.md — інакше наступний агент не знає, що тут контракт: {offenders}"
    )


# --------------------------------------------------------------------------
# Тестовий шлюз не послаблюється
# --------------------------------------------------------------------------


def test_no_skip_or_xfail_markers() -> None:
    needles = ("mark.skip", "mark.xfail", "pytest.skip(")
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.resolve() == SELF:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(needle in line for needle in needles):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "Червоний тест — це знахідка, а не перешкода. skip/xfail у наборі: " f"{offenders}"
    )


def test_coverage_gate_is_not_lowered() -> None:
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "--cov-fail-under=90" in ini, "Поріг покриття 90 % знижувати не можна"
    for target in ("--cov=parser", "--cov=ui_helpers", "--cov=compare", "--cov=search"):
        assert target in ini, f"Втрачено {target} з pytest.ini"


# --------------------------------------------------------------------------
# Публічний інтерфейс parser/* не міняється мовчки
# --------------------------------------------------------------------------

PARSER_API_SNAPSHOT = ROOT / "tests" / "fixtures" / "parser_public_api.json"


def _collect_parser_api() -> dict[str, list[str]]:
    import importlib
    import pkgutil

    package = importlib.import_module("parser")
    api: dict[str, list[str]] = {}
    for info in pkgutil.iter_modules(package.__path__):
        module_name = f"parser.{info.name}"
        module = importlib.import_module(module_name)
        api[module_name] = sorted(
            name
            for name in vars(module)
            if not name.startswith("_") and getattr(vars(module)[name], "__module__", module_name) == module_name
        )
    return api


def test_parser_public_api_is_unchanged() -> None:
    """
    Approval-тест. Базовий знімок створюється один раз і комітиться;
    далі будь-яка зміна публічних імен parser/* робить тест червоним.
    Якщо зміна навмисна — оновити знімок у тому ж коміті, що й код.
    """
    current = _collect_parser_api()
    if not PARSER_API_SNAPSHOT.exists():
        PARSER_API_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        PARSER_API_SNAPSHOT.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.fail(
            "Створено базовий знімок tests/fixtures/parser_public_api.json. "
            "Перевір його очима і закоміть — далі тест буде зелений."
        )
    expected = json.loads(PARSER_API_SNAPSHOT.read_text(encoding="utf-8"))
    assert current == expected, (
        "Публічний інтерфейс parser/* змінився. Від нього залежать основний режим "
        "і ?mode=compare. Якщо зміна навмисна — онови знімок у цьому ж коміті."
    )
