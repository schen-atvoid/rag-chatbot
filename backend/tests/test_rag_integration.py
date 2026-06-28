import unittest
from unittest.mock import MagicMock, patch


class TestRAGSystemQuery(unittest.TestCase):
    def setUp(self):
        self.vs_patcher = patch("rag_system.VectorStore")
        self.ai_patcher = patch("rag_system.AIGenerator")
        self.dp_patcher = patch("rag_system.DocumentProcessor")

        self.vs_patcher.start()
        MockAI = self.ai_patcher.start()
        self.dp_patcher.start()

        from config import config
        from rag_system import RAGSystem

        self.rag = RAGSystem(config)
        # These are the mock instances created inside RAGSystem.__init__
        self.ai_mock = self.rag.ai_generator
        self.vs_mock = self.rag.vector_store

    def tearDown(self):
        self.vs_patcher.stop()
        self.ai_patcher.stop()
        self.dp_patcher.stop()

    # ── basic return contract ─────────────────────────────────────────────────

    def test_query_returns_response_and_sources_tuple(self):
        self.ai_mock.generate_response.return_value = "MCP stands for Model Context Protocol."
        self.rag.search_tool.last_sources = [
            {"text": "MCP Course - Lesson 1", "url": "https://example.com/1"}
        ]

        response, sources = self.rag.query("What is MCP?")

        self.assertEqual(response, "MCP stands for Model Context Protocol.")
        self.assertEqual(
            sources, [{"text": "MCP Course - Lesson 1", "url": "https://example.com/1"}]
        )

    def test_generate_response_called_with_wrapped_prompt_and_tools(self):
        self.ai_mock.generate_response.return_value = "Answer."

        self.rag.query("What is MCP?")

        call_kwargs = self.ai_mock.generate_response.call_args[1]
        self.assertIn("What is MCP?", call_kwargs["query"])
        tools = call_kwargs["tools"]
        self.assertIsInstance(tools, list)
        self.assertGreater(len(tools), 0)
        self.assertEqual(call_kwargs["tool_manager"], self.rag.tool_manager)

    # ── source lifecycle ──────────────────────────────────────────────────────

    def test_sources_reset_to_empty_after_each_query(self):
        self.ai_mock.generate_response.return_value = "Answer."
        self.rag.search_tool.last_sources = [{"text": "Course A", "url": None}]

        self.rag.query("question one")

        self.assertEqual(self.rag.search_tool.last_sources, [])

    def test_second_query_only_sees_its_own_sources(self):
        self.ai_mock.generate_response.return_value = "Answer."
        self.rag.search_tool.last_sources = [{"text": "Course A", "url": None}]
        self.rag.query("question one")

        self.rag.search_tool.last_sources = [{"text": "Course B", "url": None}]
        _, sources = self.rag.query("question two")

        self.assertEqual(sources, [{"text": "Course B", "url": None}])

    # ── session history ───────────────────────────────────────────────────────

    def test_query_stores_exchange_in_session(self):
        self.ai_mock.generate_response.return_value = "Python is a language."
        session_id = self.rag.session_manager.create_session()

        self.rag.query("What is Python?", session_id=session_id)

        history = self.rag.session_manager.get_conversation_history(session_id)
        self.assertIsNotNone(history)
        self.assertIn("What is Python?", history)
        self.assertIn("Python is a language.", history)

    def test_history_forwarded_to_generator_on_subsequent_calls(self):
        self.ai_mock.generate_response.return_value = "Answer."
        session_id = self.rag.session_manager.create_session()

        self.rag.query("first question", session_id=session_id)
        self.rag.query("second question", session_id=session_id)

        second_call_kwargs = self.ai_mock.generate_response.call_args_list[1][1]
        self.assertIsNotNone(second_call_kwargs["conversation_history"])

    def test_history_is_none_when_no_session_id(self):
        self.ai_mock.generate_response.return_value = "Answer."

        self.rag.query("any question")

        call_kwargs = self.ai_mock.generate_response.call_args[1]
        self.assertIsNone(call_kwargs["conversation_history"])

    # ── exception propagation (Bug 1 system level) ────────────────────────────

    def test_api_exception_propagates_out_of_query(self):
        """Bug 1: invalid model causes API error; RAGSystem.query must not swallow it."""
        self.ai_mock.generate_response.side_effect = Exception(
            "Model not found: deepseek-v4-pro"
        )

        with self.assertRaises(Exception) as ctx:
            self.rag.query("Tell me about Python.")

        self.assertIn("Model not found", str(ctx.exception))


class TestVectorStoreResolveCourseName(unittest.TestCase):
    def setUp(self):
        self.chroma_patcher = patch("chromadb.PersistentClient")
        self.embed_patcher = patch(
            "chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction"
        )
        self.chroma_patcher.start()
        self.embed_patcher.start()

        from vector_store import VectorStore

        self.store = VectorStore(
            chroma_path="./test_chroma_tmp",
            embedding_model="all-MiniLM-L6-v2",
        )
        self.catalog_mock = MagicMock()
        self.store.course_catalog = self.catalog_mock

    def tearDown(self):
        self.chroma_patcher.stop()
        self.embed_patcher.stop()

    def test_resolve_returns_none_cleanly_when_outer_list_is_empty(self):
        """Bug 2: chromadb returns {'documents': []} (empty outer list) when catalog
        is empty. The code does results['documents'][0] which raises IndexError.
        That IndexError is caught by the except block — confirmed by print being called.
        After the fix (guarding the outer list), no exception occurs and print is silent."""
        self.catalog_mock.query.return_value = {
            "documents": [],
            "metadatas": [],
            "distances": [],
        }

        with patch("builtins.print") as mock_print:
            result = self.store._resolve_course_name("Python Basics")

        self.assertIsNone(result)
        # Bug 2: With the buggy code, IndexError is raised and caught, causing print()
        # to be called. After the fix, no exception means print is NOT called.
        mock_print.assert_not_called()

    def test_resolve_returns_title_when_catalog_has_match(self):
        self.catalog_mock.query.return_value = {
            "documents": [["Python 101"]],
            "metadatas": [[{"title": "Python 101"}]],
            "distances": [[0.1]],
        }

        result = self.store._resolve_course_name("python")

        self.assertEqual(result, "Python 101")
