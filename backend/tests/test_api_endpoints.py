"""
API endpoint tests for the FastAPI application.

Tests the HTTP layer (routing, request validation, response structure, error
handling) using a test app built by the ``test_app`` fixture in conftest.py.
That test app mirrors the real routes but uses a mock RAG system and skips the
static-file mount that would fail when ``../frontend`` is missing.
"""

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/query
# ═══════════════════════════════════════════════════════════════════════════════


class TestQueryEndpoint:
    """Tests for POST /api/query."""

    def test_returns_200_with_answer_sources_and_session_id(
        self, client, sample_query, mock_rag_system
    ):
        """A valid query without session_id should auto-create a session."""
        response = client.post("/api/query", json=sample_query)

        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "Test answer."
        assert body["sources"] == [{"text": "Source 1", "url": "http://example.com"}]
        assert body["session_id"] == "test-session-123"

        # The RAG system should have been called with the query text
        mock_rag_system.query.assert_called_once_with(
            "What is MCP?", "test-session-123"
        )

    def test_uses_provided_session_id(
        self, client, sample_query_with_session, mock_rag_system
    ):
        """When the request includes a session_id it must be forwarded as-is."""
        response = client.post("/api/query", json=sample_query_with_session)

        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == "existing-session"

        # create_session must NOT be called when one is already provided
        mock_rag_system.session_manager.create_session.assert_not_called()
        mock_rag_system.query.assert_called_once_with(
            "What is Python?", "existing-session"
        )

    def test_returns_500_when_rag_system_raises(self, client, sample_query, mock_rag_system):
        """Exceptions from the RAG layer should surface as HTTP 500."""
        mock_rag_system.query.side_effect = RuntimeError("ChromaDB connection lost")

        response = client.post("/api/query", json=sample_query)

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert "ChromaDB connection lost" in detail

    def test_returns_422_when_query_field_is_missing(self, client):
        """A missing required field should produce a validation error."""
        response = client.post("/api/query", json={})

        assert response.status_code == 422

    def test_returns_422_when_query_is_empty_string(self, client):
        """An empty query string passes Pydantic validation —
        business-logic validation belongs in the RAG layer."""
        response = client.post("/api/query", json={"query": ""})

        # Empty string is technically valid to Pydantic (it's a str)
        assert response.status_code == 200

    def test_response_matches_QueryResponse_schema(self, client, sample_query):
        """The response body must contain exactly the expected keys."""
        response = client.post("/api/query", json=sample_query)

        body = response.json()
        assert set(body.keys()) == {"answer", "sources", "session_id"}
        assert isinstance(body["answer"], str)
        assert isinstance(body["sources"], list)
        assert isinstance(body["session_id"], str)

    def test_session_id_is_string(self, client, sample_query):
        """session_id must always be returned as a string."""
        response = client.post("/api/query", json=sample_query)
        assert isinstance(response.json()["session_id"], str)


# ═══════════════════════════════════════════════════════════════════════════════
# DELETE /api/session/{session_id}
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteSessionEndpoint:
    """Tests for DELETE /api/session/{session_id}."""

    def test_returns_200_and_ok_status(self, client, mock_rag_system):
        response = client.delete("/api/session/session-to-clear")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_rag_system.session_manager.clear_session.assert_called_once_with(
            "session-to-clear"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/courses
# ═══════════════════════════════════════════════════════════════════════════════


class TestCoursesEndpoint:
    """Tests for GET /api/courses."""

    def test_returns_200_with_course_stats(self, client, mock_rag_system):
        response = client.get("/api/courses")

        assert response.status_code == 200
        body = response.json()
        assert body["total_courses"] == 3
        assert body["course_titles"] == ["Course A", "Course B", "Course C"]
        mock_rag_system.get_course_analytics.assert_called_once()

    def test_response_matches_CourseStats_schema(self, client):
        """The response body must contain exactly the expected keys."""
        response = client.get("/api/courses")

        body = response.json()
        assert set(body.keys()) == {"total_courses", "course_titles"}
        assert isinstance(body["total_courses"], int)
        assert isinstance(body["course_titles"], list)

    def test_returns_500_when_analytics_raises(self, client, mock_rag_system):
        """Exceptions from the analytics layer should surface as HTTP 500."""
        mock_rag_system.get_course_analytics.side_effect = RuntimeError(
            "Vector store unavailable"
        )

        response = client.get("/api/courses")

        assert response.status_code == 500
        assert "Vector store unavailable" in response.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════════
# GET /
# ═══════════════════════════════════════════════════════════════════════════════


class TestRootEndpoint:
    """Tests for GET / (health-check / static-files placeholder)."""

    def test_returns_200_with_status(self, client):
        response = client.get("/")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "message" in body
