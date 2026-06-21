# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
uv sync                                    # Install dependencies
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env # Required for AI responses
./run.sh                                   # Start server (or: cd backend && uv run uvicorn app:app --reload --port 8000)
```

The app serves at `http://localhost:8000` (frontend + API). API docs at `/docs`.

There are no tests, no linter, and no build step.

## Architecture

This is a full-stack RAG application: users query course transcripts through a chat UI, and Claude answers using semantic search over vectorized course content.

**Startup sequence**: `app.py` startup event → `rag_system.add_course_folder("../docs")` → for each `.txt` file: parse → chunk → embed → store in ChromaDB. Course title is the dedup key — re-processing the same title on restart is skipped unless `clear_existing=True`.

**Query flow** (the critical multi-step path):
1. User submits question → `POST /api/query` → `RAGSystem.query()`
2. `SessionManager.get_conversation_history()` prepends prior messages to the system prompt (formatted as `"User: ...\nAssistant: ..."`, not a proper messages array)
3. `AIGenerator.generate_response()` sends the first API call to Claude with the `search_course_content` tool definition
4. If Claude returns `stop_reason == "tool_use"`, `_handle_tool_execution()` calls `ToolManager.execute_tool()` and sends a **second** API call with the tool results for synthesis. If Claude answers directly, only one call is made.
5. Sources are extracted from `CourseSearchTool.last_sources` (populated during `_format_results()`), not from Claude's answer
6. `SessionManager.add_exchange()` stores the Q&A pair for conversation context

**ChromaDB has two collections**:
- `course_catalog` — one document per course (title as text, metadata with instructor/link/lessons as JSON). Used only for `_resolve_course_name()` to match partial course names to full titles via semantic search.
- `course_content` — one document per text chunk, with metadata `{course_title, lesson_number, chunk_index}`. Used for actual content search, optionally filtered by course and/or lesson.

**Vector search** (`vector_store.py:61-100`) is a three-step pipeline: resolve course name → build metadata filter → query `course_content` with the filter. Course name resolution uses semantic search against `course_catalog`, so partial/fuzzy names like "MCP" match the full title.

**Session management is in-memory only** — all sessions are lost on server restart. `MAX_HISTORY * 2` messages are kept per session (default: 4 messages, i.e., 2 exchanges).

## Document Format

Documents in `docs/` must be `.txt` files following this exact structure:

```
Course Title: <full title>
Course Link: <url>
Course Instructor: <name>

Lesson 0: <lesson title>
Lesson Link: <url>
<content paragraphs...>

Lesson 1: <another lesson>
<more content...>
```

`DocumentProcessor.process_course_document()` parses this with regex. Course Title, Course Link, and Course Instructor are matched case-insensitively from the first 4 lines. Lesson markers are matched with `^Lesson\s+(\d+):\s*(.+)$`. A `Lesson Link:` line immediately after a lesson marker is captured and excluded from content. If no lesson markers are found, the entire content is treated as one undifferentiated document.

Chunking is sentence-based (800 chars per chunk, 100 char overlap). The **last lesson** prefixes every chunk with `"Course {title} Lesson {num} content: {chunk}"`. All **earlier lessons** only prefix the first chunk, and use the shorter form `"Lesson {num} content: {chunk}"` (no course title). This asymmetry is in `document_processor.py:183-243`.

## Key Configuration (`backend/config.py`)

- `ANTHROPIC_MODEL`: `claude-sonnet-4-20250514`
- `EMBEDDING_MODEL`: `all-MiniLM-L6-v2` (384-dim embeddings, local)
- `CHUNK_SIZE`: 800, `CHUNK_OVERLAP`: 100 (characters)
- `MAX_RESULTS`: 5 (top chunks returned per search)
- `MAX_HISTORY`: 2 (conversation exchanges remembered)
- `CHROMA_PATH`: `./chroma_db` (persisted vector store)

## Dependency Management

Always use `uv` to manage dependencies — never use `pip` directly. Add packages with `uv add <package>`, remove with `uv remove <package>`, and run scripts/tools with `uv run <command>`.

## Notes

- `main.py` at the project root is a placeholder — it is not used. The real entry point is `backend/app.py`.
- The system prompt in `ai_generator.py` instructs Claude to make at most one search per query and to provide direct answers with no meta-commentary about its reasoning process.
- `ToolManager.reset_sources()` is called after every query — sources do not persist across queries.
