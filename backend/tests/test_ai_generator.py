import json
import unittest
from unittest.mock import MagicMock, patch


class TestConfigModelName(unittest.TestCase):
    def test_model_name_is_valid_deepseek_model(self):
        """Bug 1: 'deepseek-v4-pro' is not a real model; only these two exist."""
        from config import config

        valid_models = {"deepseek-chat", "deepseek-reasoner"}
        self.assertIn(
            config.ANTHROPIC_MODEL,
            valid_models,
            f"'{config.ANTHROPIC_MODEL}' is not a valid DeepSeek model. "
            f"Valid models: {valid_models}",
        )


class TestAIGeneratorResponsePaths(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("ai_generator.OpenAI")
        MockOpenAI = self.patcher.start()
        self.client = MagicMock()
        MockOpenAI.return_value = self.client

        from ai_generator import AIGenerator

        self.AIGenerator = AIGenerator
        self.gen = AIGenerator(api_key="test-key", model="deepseek-chat")

    def tearDown(self):
        self.patcher.stop()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _stop_response(self, content="Test answer"):
        resp = MagicMock()
        resp.choices[0].finish_reason = "stop"
        resp.choices[0].message.content = content
        resp.choices[0].message.tool_calls = None
        return resp

    def _tool_call_response(self, name, args_dict, call_id="call_abc"):
        tc = MagicMock()
        tc.function.name = name
        tc.function.arguments = json.dumps(args_dict)
        tc.id = call_id

        resp = MagicMock()
        resp.choices[0].finish_reason = "tool_calls"
        resp.choices[0].message.tool_calls = [tc]
        return resp

    # ── direct response (no tool calls) ──────────────────────────────────────

    def test_returns_direct_content_when_finish_reason_is_stop(self):
        self.client.chat.completions.create.return_value = self._stop_response("Python is great.")

        result = self.gen.generate_response("What is Python?")

        self.assertEqual(result, "Python is great.")
        self.client.chat.completions.create.assert_called_once()

    def test_tools_key_absent_from_api_call_when_tools_is_none(self):
        self.client.chat.completions.create.return_value = self._stop_response()

        self.gen.generate_response("What is Python?", tools=None)

        kwargs = self.client.chat.completions.create.call_args[1]
        self.assertNotIn("tools", kwargs)
        self.assertNotIn("tool_choice", kwargs)

    # ── tool-call path ────────────────────────────────────────────────────────

    def test_calls_tool_manager_when_finish_reason_is_tool_calls(self):
        first = self._tool_call_response("search_course_content", {"query": "what is recursion"})
        second = self._stop_response("Recursion is a function calling itself.")
        self.client.chat.completions.create.side_effect = [first, second]

        tool_mgr = MagicMock()
        tool_mgr.execute_tool.return_value = "[Course A]\nRecursion content."

        tools = [{"type": "function"}]
        result = self.gen.generate_response(
            "what is recursion",
            tools=tools,
            tool_manager=tool_mgr,
        )

        tool_mgr.execute_tool.assert_called_once_with(
            "search_course_content", query="what is recursion"
        )
        self.assertEqual(self.client.chat.completions.create.call_count, 2)
        self.assertEqual(result, "Recursion is a function calling itself.")
        # tools must be present in the intermediate call so the model can make a second call
        second_call_kwargs = self.client.chat.completions.create.call_args_list[1][1]
        self.assertIn("tools", second_call_kwargs)

    def test_second_api_call_contains_tool_result_message(self):
        first = self._tool_call_response("search_course_content", {"query": "loops"}, call_id="call_xyz")
        second = self._stop_response("Loops repeat code.")
        self.client.chat.completions.create.side_effect = [first, second]

        tool_mgr = MagicMock()
        tool_mgr.execute_tool.return_value = "Loop content from vector store"

        self.gen.generate_response("what are loops", tools=[{}], tool_manager=tool_mgr)

        second_call_kwargs = self.client.chat.completions.create.call_args_list[1][1]
        messages = second_call_kwargs["messages"]
        tool_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_xyz")
        self.assertEqual(tool_msgs[0]["content"], "Loop content from vector store")

    # ── conversation history ──────────────────────────────────────────────────

    def test_history_prepended_to_system_prompt(self):
        self.client.chat.completions.create.return_value = self._stop_response()

        self.gen.generate_response(
            "What is Python?",
            conversation_history="User: hello\nAssistant: hi",
        )

        msgs = self.client.chat.completions.create.call_args[1]["messages"]
        sys_msg = next(m for m in msgs if m["role"] == "system")
        self.assertIn("Previous conversation:", sys_msg["content"])
        self.assertIn("User: hello\nAssistant: hi", sys_msg["content"])

    def test_no_history_section_when_history_is_none(self):
        self.client.chat.completions.create.return_value = self._stop_response()

        self.gen.generate_response("What is Python?", conversation_history=None)

        msgs = self.client.chat.completions.create.call_args[1]["messages"]
        sys_msg = next(m for m in msgs if m["role"] == "system")
        self.assertEqual(sys_msg["content"], self.AIGenerator.SYSTEM_PROMPT)

    # ── exception propagation (Bug 1 chain) ──────────────────────────────────

    def test_api_exception_propagates_to_caller(self):
        """Bug 1: invalid model causes API error; must bubble up, not be swallowed."""
        self.client.chat.completions.create.side_effect = Exception(
            "Invalid model: deepseek-v4-pro"
        )

        with self.assertRaises(Exception) as ctx:
            self.gen.generate_response("any question")

        self.assertIn("Invalid model", str(ctx.exception))

    # ── sequential tool calls (up to 2 rounds) ───────────────────────────────

    def test_two_sequential_tool_calls(self):
        first = self._tool_call_response("get_course_outline", {"course": "X"}, call_id="call_1")
        second = self._tool_call_response("search_course_content", {"query": "lesson topic"}, call_id="call_2")
        third = self._stop_response("Here is the course that covers that topic.")
        self.client.chat.completions.create.side_effect = [first, second, third]

        tool_mgr = MagicMock()
        tool_mgr.execute_tool.side_effect = ["Lesson 4: Recursion", "Course B covers recursion."]

        result = self.gen.generate_response(
            "find a course on the same topic as lesson 4 of course X",
            tools=[{"type": "function"}],
            tool_manager=tool_mgr,
        )

        self.assertEqual(self.client.chat.completions.create.call_count, 3)
        self.assertEqual(tool_mgr.execute_tool.call_count, 2)
        self.assertEqual(result, "Here is the course that covers that topic.")

    def test_max_two_rounds_enforced(self):
        always_tool = self._tool_call_response("search_course_content", {"query": "q"})
        self.client.chat.completions.create.side_effect = [always_tool] * 3

        tool_mgr = MagicMock()
        tool_mgr.execute_tool.return_value = "some result"

        self.gen.generate_response("q", tools=[{}], tool_manager=tool_mgr)

        self.assertEqual(self.client.chat.completions.create.call_count, 3)

    def test_early_termination_on_round_two_stop(self):
        first = self._tool_call_response("search_course_content", {"query": "loops"})
        second = self._stop_response("Loops repeat code.")
        self.client.chat.completions.create.side_effect = [first, second]

        tool_mgr = MagicMock()
        tool_mgr.execute_tool.return_value = "loop content"

        result = self.gen.generate_response("what are loops", tools=[{}], tool_manager=tool_mgr)

        self.assertEqual(self.client.chat.completions.create.call_count, 2)
        tool_mgr.execute_tool.assert_called_once()
        self.assertEqual(result, "Loops repeat code.")

    def test_tools_present_in_intermediate_api_calls(self):
        first = self._tool_call_response("search_course_content", {"query": "q"})
        second = self._stop_response("Answer.")
        self.client.chat.completions.create.side_effect = [first, second]

        tool_mgr = MagicMock()
        tool_mgr.execute_tool.return_value = "result"

        tools = [{"type": "function", "function": {"name": "search_course_content"}}]
        self.gen.generate_response("q", tools=tools, tool_manager=tool_mgr)

        intermediate_kwargs = self.client.chat.completions.create.call_args_list[1][1]
        self.assertIn("tools", intermediate_kwargs)
        self.assertEqual(intermediate_kwargs["tools"], tools)
