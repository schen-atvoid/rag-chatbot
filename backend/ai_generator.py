import json
from openai import OpenAI
from typing import List, Optional, Dict, Any

class AIGenerator:
    """Handles interactions with DeepSeek's API for generating responses"""

    SYSTEM_PROMPT = """ You are an AI assistant specialized in course materials and educational content with access to a comprehensive search tool for course information.

Search Tool Usage:
- Use the search tool **only** for questions about specific course content or detailed educational materials
- **Maximum two sequential tool calls per query** — use a second call only when the first result informs the next query (e.g., look up a lesson title, then search by that title)
- Synthesize search results into accurate, fact-based responses
- If search yields no results, state this clearly without offering alternatives
- **Course outline queries**: Use the get_course_outline tool when asked for a course outline, syllabus, or lesson list — return the course title, course link, and the number and title of each lesson

Response Protocol:
- **General knowledge questions**: Answer using existing knowledge without searching
- **Course-specific questions**: Search first, then answer
- **No meta-commentary**:
 - Provide direct answers only — no reasoning process, search explanations, or question-type analysis
 - Do not mention "based on the search results"


All responses must be:
1. **Brief, Concise and focused** - Get to the point quickly
2. **Educational** - Maintain instructional value
3. **Clear** - Use accessible language
4. **Example-supported** - Include relevant examples when they aid understanding
Provide only the direct answer to what was asked.
"""

    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.model = model

        self.base_params = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 800
        }

    def generate_response(self, query: str,
                         conversation_history: Optional[str] = None,
                         tools: Optional[List] = None,
                         tool_manager=None) -> str:
        system_content = (
            f"{self.SYSTEM_PROMPT}\n\nPrevious conversation:\n{conversation_history}"
            if conversation_history
            else self.SYSTEM_PROMPT
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": query}
        ]

        api_params = {
            **self.base_params,
            "messages": messages
        }

        if tools:
            api_params["tools"] = tools
            api_params["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**api_params)

        if response.choices[0].finish_reason == "tool_calls" and tool_manager:
            return self._handle_tool_execution(response, api_params, tool_manager, tools)

        return response.choices[0].message.content

    def _handle_tool_execution(self, initial_response, base_params: Dict[str, Any], tool_manager, tools=None):
        messages = base_params["messages"].copy()
        current_response = initial_response

        for _ in range(2):
            assistant_message = current_response.choices[0].message
            messages.append(assistant_message)

            for tool_call in assistant_message.tool_calls:
                kwargs = json.loads(tool_call.function.arguments)
                result = tool_manager.execute_tool(tool_call.function.name, **kwargs)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

            next_params = {**self.base_params, "messages": messages}
            if tools:
                next_params["tools"] = tools
                next_params["tool_choice"] = "auto"

            current_response = self.client.chat.completions.create(**next_params)

            if current_response.choices[0].finish_reason != "tool_calls":
                break

        return current_response.choices[0].message.content
