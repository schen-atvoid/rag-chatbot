"""
Shared pytest fixtures for the RAG system test suite.

Provides mock RAG system components, a test FastAPI app (without static file
mounting that would fail in the test environment), and sample request data.
"""

import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from typing import List, Optional


# ── Pydantic models (mirrored from app.py to avoid import of module-level side effects) ──

class QueryRequest(BaseModel):
    """Request model for course queries."""
    query: str
    session_id: Optional[str] = None


class Source(BaseModel):
    """A single source citation with optional link."""
    text: str
    url: Optional[str] = None


class QueryResponse(BaseModel):
    """Response model for course queries."""
    answer: str
    sources: List[Source]
    session_id: str


class CourseStats(BaseModel):
    """Response model for course statistics."""
    total_courses: int
    course_titles: List[str]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_rag_system():
    """
    Return a MagicMock standing in for the RAGSystem instance.

    Default behaviour:
    - create_session() → "test-session-123"
    - query() → ("Test answer.", [{"text": "Source 1", "url": "http://example.com"}])
    - get_course_analytics() → {"total_courses": 3, "course_titles": ["Course A", "Course B", "Course C"]}
    - clear_session() is a no-op.

    Tests can override any return_value / side_effect on the returned mock.
    """
    mock = MagicMock()
    mock.session_manager.create_session.return_value = "test-session-123"
    mock.query.return_value = (
        "Test answer.",
        [{"text": "Source 1", "url": "http://example.com"}],
    )
    mock.get_course_analytics.return_value = {
        "total_courses": 3,
        "course_titles": ["Course A", "Course B", "Course C"],
    }
    return mock


@pytest.fixture
def test_app(mock_rag_system):
    """
    Build a FastAPI app with the same routes as the real application but:

    - Uses ``mock_rag_system`` instead of a real RAGSystem.
    - Skips the ``StaticFiles`` mount on ``/`` (the ``../frontend`` directory
      doesn't exist in the test environment).
    - Omits the ``startup`` event that loads documents from ``../docs``.

    Returns the FastAPI *instance* (not a TestClient).
    """
    app = FastAPI(title="Test Course Materials RAG System")

    # Same middleware as the real app
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # ── mirrored endpoints ────────────────────────────────────────────────────

    @app.post("/api/query", response_model=QueryResponse)
    async def query_documents(request: QueryRequest):
        """Process a query and return response with sources."""
        try:
            session_id = request.session_id
            if not session_id:
                session_id = mock_rag_system.session_manager.create_session()

            answer, sources = mock_rag_system.query(request.query, session_id)

            return QueryResponse(
                answer=answer,
                sources=sources,
                session_id=session_id,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/session/{session_id}")
    async def delete_session(session_id: str):
        """Clear a session's conversation history."""
        mock_rag_system.session_manager.clear_session(session_id)
        return {"status": "ok"}

    @app.get("/api/courses", response_model=CourseStats)
    async def get_course_stats():
        """Get course analytics and statistics."""
        try:
            analytics = mock_rag_system.get_course_analytics()
            return CourseStats(
                total_courses=analytics["total_courses"],
                course_titles=analytics["course_titles"],
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/")
    async def root():
        """Health-check root endpoint (in production this serves static files)."""
        return {"status": "ok", "message": "RAG System API"}

    return app


@pytest.fixture
def client(test_app):
    """Return a ``starlette.testclient.TestClient`` bound to the test app."""
    from fastapi.testclient import TestClient

    return TestClient(test_app)


@pytest.fixture
def sample_query() -> dict:
    """A minimal valid query request body (no session_id)."""
    return {"query": "What is MCP?"}


@pytest.fixture
def sample_query_with_session() -> dict:
    """A query request body that includes a session_id."""
    return {"query": "What is Python?", "session_id": "existing-session"}
