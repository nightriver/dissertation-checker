"""
Unit tests for ui_helpers.py — the pure logic extracted out of app.py.
Run: pytest tests/test_ui_helpers.py
"""
import unittest

from ui_helpers import (
    FILE_SCOPED_KEYS,
    format_number_ranges,
    lines_to_tuple,
    tuple_to_lines,
    make_file_key,
    reset_file_scoped_state,
)


class TestFormatNumberRanges(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(format_number_ranges([]), "")

    def test_single(self):
        self.assertEqual(format_number_ranges([3]), "3")

    def test_mixed_runs_and_singles(self):
        self.assertEqual(format_number_ranges([1, 2, 3, 7, 9, 10, 11]), "1–3, 7, 9–11")

    def test_unsorted_input(self):
        self.assertEqual(format_number_ranges([5, 4, 1]), "1, 4–5")

    def test_pair_is_a_range(self):
        self.assertEqual(format_number_ranges([8, 9]), "8–9")

    def test_string_input_coerced(self):
        self.assertEqual(format_number_ranges(["2", "3"]), "2–3")

    def test_duplicates_do_not_break_run(self):
        # sorted() keeps duplicates; they must not emit a bogus range
        self.assertEqual(format_number_ranges([1, 1, 2]), "1, 1–2")

    def test_accepts_a_set(self):
        self.assertEqual(format_number_ranges({4, 2, 3}), "2–4")


class TestLinesTuple(unittest.TestCase):
    def test_round_trip(self):
        lines = [{"line": "a", "page": 1}, {"line": "b", "page": None}]
        self.assertEqual(tuple_to_lines(lines_to_tuple(lines)), lines)

    def test_result_is_hashable(self):
        # The whole reason this conversion exists: @st.cache_data needs a
        # hashable argument, and dicts are not hashable.
        t = lines_to_tuple([{"line": "a", "page": 1}, {"line": "b", "page": None}])
        hash(t)  # must not raise

    def test_missing_page_key_defaults_to_none(self):
        self.assertEqual(lines_to_tuple([{"line": "a"}]), (("a", None),))

    def test_empty(self):
        self.assertEqual(lines_to_tuple([]), ())


class TestFileScopedState(unittest.TestCase):
    def _populated(self):
        state = {k: f"value-of-{k}" for k in FILE_SCOPED_KEYS}
        state["unrelated"] = "keep me"
        return state

    def test_first_file_resets_and_records_key(self):
        state = {}
        self.assertTrue(reset_file_scoped_state(state, "a.pdf:10"))
        self.assertEqual(state["current_file_key"], "a.pdf:10")

    def test_same_file_is_a_noop(self):
        state = self._populated()
        reset_file_scoped_state(state, "a.pdf:10")
        state.update({k: f"value-of-{k}" for k in FILE_SCOPED_KEYS})

        self.assertFalse(reset_file_scoped_state(state, "a.pdf:10"))
        for k in FILE_SCOPED_KEYS:
            self.assertIn(k, state)

    def test_different_file_clears_stale_results(self):
        state = self._populated()
        reset_file_scoped_state(state, "a.pdf:10")
        state.update({k: f"value-of-{k}" for k in FILE_SCOPED_KEYS})

        self.assertTrue(reset_file_scoped_state(state, "b.pdf:20"))
        for k in FILE_SCOPED_KEYS:
            self.assertNotIn(k, state, f"{k} survived a file change")

    def test_unrelated_keys_survive(self):
        state = self._populated()
        reset_file_scoped_state(state, "a.pdf:10")
        reset_file_scoped_state(state, "b.pdf:20")
        self.assertEqual(state["unrelated"], "keep me")

    def test_missing_keys_do_not_raise(self):
        state = {"current_file_key": "a.pdf:10"}
        self.assertTrue(reset_file_scoped_state(state, "b.pdf:20"))


class TestMakeFileKey(unittest.TestCase):
    def test_prefers_file_id(self):
        self.assertEqual(make_file_key("a.pdf", 10, "abc-123"), "abc-123")

    def test_falls_back_to_name_and_size(self):
        self.assertEqual(make_file_key("a.pdf", 10, None), "a.pdf:10")

    def test_same_name_different_size_differs(self):
        self.assertNotEqual(make_file_key("a.pdf", 10), make_file_key("a.pdf", 11))
