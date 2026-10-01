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
    assert "Documentation & Engineering Portal" in res.text
    assert "SwaggerUIBundle" not in res.text

    # Also test without trailing slash
    res2 = client.get("/docs")
    assert res2.status_code in (200, 307)


def test_asset_tags_and_version_synchronization():
    """Verify that all frontend HTML script and link asset tags match VERSION."""
    import re
    from pathlib import Path
    from backend.config import BASE_DIR, VERSION

    version_file = BASE_DIR / "VERSION"
    assert version_file.exists(), "VERSION file missing"
    current_ver = version_file.read_text().strip()
    assert current_ver == VERSION

    frontend_dir = BASE_DIR / "frontend"
    html_files = list(frontend_dir.glob("**/*.html"))
    assert len(html_files) >= 5, "Frontend HTML templates missing"

    asset_tag_pattern = re.compile(r'(?:src|href)=["\'](/[^"\']+\.(?:js|css))\?v=([^"\']+)["\']')

    for html_file in html_files:
        content = html_file.read_text()
        matches = asset_tag_pattern.findall(content)
        assert len(matches) > 0, f"No versioned assets found in {html_file.name}"
        for asset_path, asset_ver in matches:
            assert asset_ver == current_ver, (
                f"Asset version mismatch in {html_file.relative_to(BASE_DIR)}: "
                f"{asset_path}?v={asset_ver} (expected ?v={current_ver})"
            )


def test_public_and_guest_access_to_all_docs():
    """Verify that unauthenticated visitors and guests can access all registered docs without credentials."""
    unauthed_client = TestClient(app)

    # 1. Fetch catalog without any cookies or auth headers
    res = unauthed_client.get("/api/docs/list")
    assert res.status_code == 200
    catalog = res.json().get("sections", [])
    assert len(catalog) >= 5

    # 2. Fetch every registered document without credentials
    for section in catalog:
        doc_id = section["id"]
        doc_res = unauthed_client.get(f"/api/docs/{doc_id}")
        assert doc_res.status_code == 200, f"Failed to fetch public doc {doc_id}"
        doc_data = doc_res.json()
        assert doc_data["id"] == doc_id
        assert "content" in doc_data
        assert len(doc_data["content"]) > 0

    # 3. Access the portal UI
    portal_res = unauthed_client.get("/docs/")
    assert portal_res.status_code == 200


def test_recursive_documentation_links_integrity():
    """Verify that 100% of markdown links across all docs/*.md files resolve cleanly to registered docs or valid repository paths."""
    import re
    from pathlib import Path
    from backend.main import DOCS_REGISTRY
    from backend.config import BASE_DIR

    docs_dir = BASE_DIR / "docs"
    all_doc_ids = set(DOCS_REGISTRY.keys())
    assert len(all_doc_ids) >= 10, "DOCS_REGISTRY missing core articles"

    def resolve_link(link_url: str) -> str:
        clean_url = link_url.split("#")[0]
        if "docs/app" in clean_url or clean_url.endswith("/app.md") or clean_url == "app": return "#app"
        if "docs/manager" in clean_url or clean_url.endswith("/manager.md") or clean_url == "manager": return "#manager"
        if "docs/projects" in clean_url or clean_url.endswith("/projects.md") or clean_url == "projects": return "#projects"
        if "docs/admin" in clean_url or clean_url.endswith("/admin.md") or clean_url == "admin": return "#admin"
        if "DEPLOYMENT.md" in clean_url or clean_url == "deployment": return "#deployment"
        if "ACCOUNT_MIGRATION.md" in clean_url or clean_url == "account_migration": return "#account_migration"
        if "ARCHITECTURE.md" in clean_url or clean_url == "architecture": return "#architecture"
        if "SECURITY_HARDENING.md" in clean_url or clean_url == "security": return "#security"
        if "SECURITY_REMEDIATION" in clean_url or clean_url == "security_remediation": return "#security_remediation"
        if "SECURITY_AUDIT" in clean_url or clean_url == "security_audit": return "#security_audit"
        if "SUNO_TOS_COMPLIANCE.md" in clean_url or clean_url == "suno_tos": return "#suno_tos"
        if "development_workflow.md" in clean_url or clean_url == "workflow": return "#workflow"
        if "ROADMAP.md" in clean_url or clean_url == "roadmap": return "#roadmap"
        if "CHANGELOG.md" in clean_url or clean_url == "changelog": return "#changelog"
        if "VISION.md" in clean_url or clean_url == "vision": return "#vision"
        if "GOVERNANCE.md" in clean_url or clean_url == "governance": return "#governance"
        if clean_url.endswith("README.md") or clean_url in ("README.md", "../README.md", "readme"): return "#readme"
        if any(k in clean_url for k in ("backend/", "frontend/", "install.sh", "VERSION")):
            stripped = clean_url.replace("../", "").replace("./", "")
            return f"GITHUB:{stripped}"
        return link_url

    errors = []
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    for p in sorted(docs_dir.glob("**/*.md")):
        content = p.read_text()
        for m in link_pattern.finditer(content):
            text, url = m.groups()
            target = resolve_link(url)
            rel_p = p.relative_to(BASE_DIR)
            if target.startswith("#"):
                doc_id = target[1:]
                if doc_id not in all_doc_ids:
                    errors.append(f"{rel_p}: [{text}]({url}) -> target doc {doc_id} not in registry")
            elif target.startswith("GITHUB:"):
                f_path = BASE_DIR / target.split(":", 1)[1]
                if not f_path.exists():
                    errors.append(f"{rel_p}: [{text}]({url}) -> file {f_path} does not exist")
            elif target.startswith(("http://", "https://", "/")):
                pass
            else:
                errors.append(f"{rel_p}: [{text}]({url}) -> unresolved relative link {target}")

    assert not errors, f"Broken markdown links detected in docs/:\n" + "\n".join(errors)



