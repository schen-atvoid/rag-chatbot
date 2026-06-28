import unittest
from unittest.mock import MagicMock

from search_tools import CourseSearchTool
from vector_store import SearchResults


def _make_results(docs=None, metadata=None, error=None):
    docs = docs or []
    metadata = metadata or []
    return SearchResults(
        documents=docs,
        metadata=metadata,
        distances=[0.1] * len(docs),
        error=error,
    )


class TestCourseSearchToolExecute(unittest.TestCase):
    def setUp(self):
        self.store = MagicMock()
        self.tool = CourseSearchTool(vector_store=self.store)

    # ── error path ────────────────────────────────────────────────────────────

    def test_execute_returns_error_string_when_search_errors(self):
        """Bug 3: ChromaDB raises when result count > index size; must surface as string."""
        err = "Search error: Number of requested results 5 is greater than number of elements in index 0"
        self.store.search.return_value = _make_results(error=err)

        result = self.tool.execute(query="what is MCP")

        self.assertEqual(result, err)
        self.assertEqual(self.tool.last_sources, [])

    # ── empty results path ────────────────────────────────────────────────────

    def test_execute_returns_no_content_message_when_results_empty(self):
        self.store.search.return_value = _make_results()

        result = self.tool.execute(
            query="explain recursion", course_name="Python Basics", lesson_number=3
        )

        self.assertIn("No relevant content found", result)
        self.assertIn("Python Basics", result)
        self.assertIn("3", result)

    def test_execute_no_content_message_without_filters(self):
        self.store.search.return_value = _make_results()
        result = self.tool.execute(query="what is a loop")
        self.assertIn("No relevant content found", result)

    # ── success path ──────────────────────────────────────────────────────────

    def test_execute_formats_result_with_lesson_header(self):
        self.store.search.return_value = _make_results(
            docs=["Loops allow repeated execution"],
            metadata=[{"course_title": "Python 101", "lesson_number": 2}],
        )
        self.store.get_lesson_link.return_value = "https://example.com/lesson/2"
        self.store.get_course_link.return_value = "https://example.com/course"

        result = self.tool.execute(query="what are loops")

        self.assertIn("[Python 101 - Lesson 2]", result)
        self.assertIn("Loops allow repeated execution", result)

    def test_execute_populates_last_sources_with_lesson_url(self):
        self.store.search.return_value = _make_results(
            docs=["Loops allow repeated execution"],
            metadata=[{"course_title": "Python 101", "lesson_number": 2}],
        )
        self.store.get_lesson_link.return_value = "https://example.com/lesson/2"

        self.tool.execute(query="what are loops")

        self.assertEqual(
            self.tool.last_sources,
            [{"text": "Python 101 - Lesson 2", "url": "https://example.com/lesson/2"}],
        )
        self.store.get_lesson_link.assert_called_once_with("Python 101", 2)
        self.store.get_course_link.assert_not_called()

    def test_execute_falls_back_to_course_link_when_no_lesson_link(self):
        self.store.search.return_value = _make_results(
            docs=["Some content"],
            metadata=[{"course_title": "Python 101", "lesson_number": 2}],
        )
        self.store.get_lesson_link.return_value = None
        self.store.get_course_link.return_value = "https://example.com/course"

        self.tool.execute(query="what are loops")

        self.assertEqual(self.tool.last_sources[0]["url"], "https://example.com/course")
        self.store.get_lesson_link.assert_called_once()
        self.store.get_course_link.assert_called_once()

    # ── filter pass-through ───────────────────────────────────────────────────

    def test_execute_passes_course_name_and_lesson_number_to_store(self):
        self.store.search.return_value = _make_results()

        self.tool.execute(
            query="what is inheritance", course_name="OOP Course", lesson_number=5
        )

        self.store.search.assert_called_once_with(
            query="what is inheritance",
            course_name="OOP Course",
            lesson_number=5,
        )

    # ── no-lesson-number variant ──────────────────────────────────────────────

    def test_format_results_with_no_lesson_number(self):
        self.store.search.return_value = _make_results(
            docs=["Some content"],
            metadata=[{"course_title": "General CS", "lesson_number": None}],
        )
        self.store.get_course_link.return_value = "https://example.com"

        result = self.tool.execute(query="some question")

        self.assertIn("[General CS]", result)
        self.assertNotIn("Lesson None", result)
        self.store.get_lesson_link.assert_not_called()
        self.store.get_course_link.assert_called_once_with("General CS")
        self.assertEqual(
            self.tool.last_sources[0],
            {"text": "General CS", "url": "https://example.com"},
        )
