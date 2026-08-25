"""
Шлюз кроку 1 §22 PLAN_SEARCH.md: усі модулі, перелічені у §3, мають бути
імпортовними каркасами, а три версійні константи §4.1 — існувати як
непорожні рядки, що не залежать від Git.
"""
import importlib

import pytest


MODULE_NAMES = [
    "parser.searchdoc",
    "search",
    "search.types",
    "search.normalization",
    "search.sentences",
    "search.bibliography",
    "search.language",
    "search.calques",
    "search.markers",
    "search.query_builder",
    "search.engines",
    "search.state",
    "search.presentation",
    "search.ui_logic",
]


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_кожен_модуль_каркаса_імпортується(module_name):
    module = importlib.import_module(module_name)
    assert module is not None


def test_версійні_константи_є_непорожніми_рядками():
    import parser.searchdoc as searchdoc
    import search
    import search.calques as calques

    for value in (
        search.ALGO_VERSION,
        searchdoc.PARSER_VERSION,
        calques.DICT_VERSION,
    ):
        assert isinstance(value, str)
        assert value.strip() != ""
