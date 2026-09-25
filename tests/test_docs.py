"""Tests for in-app documentation portal and documentation APIs."""

import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_docs_list_endpoint():
    """Verify /api/docs/list returns valid sections catalog."""
    res = client.get("/api/docs/list")
    assert res.status_code == 200
    data = res.json()
    assert "sections" in data
    assert len(data["sections"]) >= 5

    ids = [s["id"] for s in data["sections"]]
    assert "app" in ids
    assert "manager" in ids
    assert "projects" in ids
    assert "admin" in ids
    assert "deployment" in ids


def test_docs_get_single_topic():
    """Verify /api/docs/{doc_id} returns content and metadata for known articles."""
    res = client.get("/api/docs/app")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "app"
    assert "MP3MetaFix" in data["title"]
    assert "content" in data
    assert len(data["content"]) > 50

    # Also test admin docs
    res_admin = client.get("/api/docs/admin")
    assert res_admin.status_code == 200
    assert "Admin" in res_admin.json()["title"]


def test_docs_404_and_traversal_protection():
    """Verify unknown topics or path traversal attempts return 404."""
    res_unknown = client.get("/api/docs/some_fake_nonexistent_doc")
    assert res_unknown.status_code == 404

    res_traversal = client.get("/api/docs/..%2F..%2Fconfig.py")
    assert res_traversal.status_code in (404, 422)


def test_docs_portal_static_page():
    """Verify /docs serves the documentation portal index.html."""
    res = client.get("/docs/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "Documentation Portal" in res.text
