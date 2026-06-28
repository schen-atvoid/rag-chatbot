# Frontend Changes — Testing Framework Enhancement

## Overview

Enhanced the existing testing framework for the RAG system by adding API endpoint tests, pytest configuration, and shared test fixtures. The test app avoids the production app's static-file mount issue by defining API endpoints inline with a mock RAG system.

## Files Changed

### `backend/tests/conftest.py` (NEW)

Shared pytest fixtures for the entire test suite:

- **Pydantic models** — `QueryRequest`, `Source`, `QueryResponse`, `CourseStats` are redefined here (mirrored from `app.py`) to avoid importing the real `app` module, which has module-level side effects (startup event, static file mount).
- **`mock_rag_system` fixture** — Returns a `MagicMock` standing in for `RAGSystem`. Default behavior: `query()` returns a test answer + sources tuple, `get_course_analytics()` returns 3 fake courses, `session_manager.create_session()` returns `"test-session-123"`. Tests can override any return value or side effect.
- **`test_app` fixture** — Builds a FastAPI instance with the same middleware (CORS, TrustedHost) and routes (`POST /api/query`, `DELETE /api/session/{session_id}`, `GET /api/courses`, `GET /`) as the production app, but uses the mock RAG system and skips the `StaticFiles` mount and `startup` event.
- **`client` fixture** — Returns a `starlette.testclient.TestClient` bound to `test_app`.
- **`sample_query` / `sample_query_with_session` fixtures** — Reusable request bodies for query tests.

### `backend/tests/test_api_endpoints.py` (NEW)

12 new tests across 4 test classes:

| Class | Tests | Coverage |
|---|---|---|
| `TestQueryEndpoint` | 7 | 200 with auto session, provided session, 500 on error, 422 on missing field, empty string accepted, schema shape, session_id type |
| `TestDeleteSessionEndpoint` | 1 | 200 + `{"status": "ok"}`, forwards to `clear_session()` |
| `TestCoursesEndpoint` | 3 | 200 + correct body, schema shape, 500 on error |
| `TestRootEndpoint` | 1 | 200 + health-check JSON |

### `pyproject.toml` (MODIFIED)

- Added `httpx>=0.28.0` to `[dependency-groups].dev` (required by FastAPI's `TestClient`).
- Added `[tool.pytest.ini_options]` section:
  - `testpaths = ["backend/tests"]` — pytest discovers tests automatically.
  - `asyncio_mode = "auto"` — enables `async def` test functions without decorators.
  - `markers` — `unit`, `integration`, `api`, `slow` markers for selective test runs.

## Design Decisions

- **Inline endpoint definitions**: Rather than patching `app.py`'s module-level `import`, the test app redefines the routes. This avoids the `StaticFiles(directory="../frontend")` mount that would fail (the `frontend/` directory doesn't exist relative to the test working directory) and the startup event that tries to load documents from `../docs`.
- **Sync `TestClient`**: Used the synchronous `TestClient` from `fastapi.testclient` instead of `httpx.AsyncClient` + `pytest-asyncio` for simplicity. The `asyncio_mode = "auto"` setting in pytest handles any async fixtures.
- **Mirrored Pydantic models**: The models are duplicated in `conftest.py` rather than imported from `app.py`. This keeps the test harness decoupled from the production app's module-level initialization.

## Test Results

```
backend/tests/test_api_endpoints.py - 12 passed
All pre-existing unit/integration tests continue to pass
(2 pre-existing failures unrelated to this change: Bug 1 model name, Bug 2 IndexError)
```
