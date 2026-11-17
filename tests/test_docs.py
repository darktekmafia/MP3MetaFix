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
    assert len(ids) == len(set(ids))
    assert all(section["available"] for section in data["sections"])


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


def test_docs_missing_source_never_substitutes_another_readme(tmp_path, monkeypatch):
    from backend import main

    # Reproduce a separately installed service lacking docs/app/README.md.
    # Its root README must not be returned as the Editor guide.
    (tmp_path / "README.md").write_text("Unrelated project overview", encoding="utf-8")
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setitem(main.DOCS_REGISTRY, "app", {
        **main.DOCS_REGISTRY["app"], "path": tmp_path / "docs/app/README.md",
    })
    sections = client.get("/api/docs/list").json()["sections"]
    assert next(s for s in sections if s["id"] == "app")["available"] is False
    response = client.get("/api/docs/app")
    assert response.status_code == 404
    assert str(tmp_path) not in response.text
    assert "Unrelated project overview" not in response.text


def test_docs_component_mount_layout_and_unrelated_working_directory(tmp_path, monkeypatch):
    """Reproduce the service's component-only /opt layout, then expose its docs."""
    from backend import main

    source_root = main.BASE_DIR
    runtime_root = tmp_path / "opt/mp3metafix"
    runtime_root.mkdir(parents=True)
    for component in ("backend", "frontend", "assets"):
        (runtime_root / component).symlink_to(source_root / component, target_is_directory=True)
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    monkeypatch.setattr(main, "BASE_DIR", runtime_root)
    monkeypatch.setattr(main, "DOCS_DIR", runtime_root / "docs")
    monkeypatch.setattr(main, "DOCS_REGISTRY", {
        key: {**meta, "path": runtime_root / meta["path"].relative_to(source_root)}
        for key, meta in main.DOCS_REGISTRY.items()
    })

    catalog = client.get("/api/docs/list").json()["sections"]
    assert len(catalog) == 17
    assert not any(section["available"] for section in catalog)
    assert client.get("/api/docs/app").status_code == 404

    # Simulate only the already-supported read-only documentation mounts;
    # never run the service migration or change the real installation.
    for name in ("docs", "README.md", "ROADMAP.md", "CHANGELOG.md", "VISION.md", "GOVERNANCE.md"):
        source = source_root / name
        (runtime_root / name).symlink_to(source, target_is_directory=source.is_dir())
    catalog = client.get("/api/docs/list").json()["sections"]
    assert all(section["available"] for section in catalog)
    for section in catalog:
        response = client.get(f'/api/docs/{section["id"]}')
        assert response.status_code == 200
        relative = main.DOCS_REGISTRY[section["id"]]["path"].relative_to(runtime_root)
        assert response.json()["content"] == (source_root / relative).read_text(encoding="utf-8")


def test_docs_portal_renders_default_hash_and_all_sidebar_topics():
    """Execute the real portal controller with API-served Markdown, not string checks."""
    import json
    from pathlib import Path
    import shutil
    import subprocess

    node = shutil.which("node")
    assert node is not None
    sections = client.get("/api/docs/list").json()["sections"]
    articles = {}
    for section in sections:
        response = client.get(f'/api/docs/{section["id"]}')
        assert response.status_code == 200
        articles[section["id"]] = response.json()
    assert "Confirm & Refresh Copy" in articles["projects"]["content"]
    assert "Play/Pause" in articles["projects"]["content"]
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.listeners = {}; this.value = ''; this._text = ''; this._classes = new Set();
    this.classList = { add: c => this._classes.add(c), remove: c => this._classes.delete(c), toggle: (c, force) => { if (force === true || (force === undefined && !this._classes.has(c))) this._classes.add(c); else this._classes.delete(c); } }; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(' '); }
  set innerHTML(v) { this._text = ''; this.children = []; }
  appendChild(c) { this.children.push(c); return c; }
  replaceChildren(...cs) { this._text = ''; this.children = cs; }
  addEventListener(ev, fn) { this.listeners[ev] = fn; }
  focus() {}
}
const flush = () => new Promise(r => setImmediate(r));
async function run(hash) {
  const els = {};
  const get = id => els[id] || (els[id] = new Element());
  const events = {};
  const calls = [];
  const walk = el => [el, ...el.children.flatMap(walk)];
  const context = {
    console: {error() {}}, setTimeout, clearTimeout, navigator: {clipboard: {writeText: async () => {}}},
    window: {location: {hash}, scrollTo() {}, addEventListener: (ev, fn) => events[ev] = fn},
    document: {readyState: 'loading', getElementById: get, createElement: tag => new Element(tag), createTextNode: t => { const e = new Element('#text'); e.textContent = t; return e; },
      querySelectorAll: () => walk(get('docsNavContainer')).filter(e => e.dataset.id),
      addEventListener: (ev, fn) => events[ev] = fn},
    fetch: async url => { calls.push(url); if (url === '/api/docs/list') return {ok: true, json: async () => ({sections: data.sections})};
      const id = decodeURIComponent(url.slice('/api/docs/'.length));
      return {ok: !!data.articles[id], json: async () => data.articles[id]}; }
  };
  vm.runInNewContext(data.source, context);
  await events.DOMContentLoaded(); await flush();
  assert.strictEqual(get('docsArticleTitle').textContent, data.articles.app.title);
  assert(get('docsArticleBody').textContent.includes('MP3MetaFix Editor'));
  const links = walk(get('docsNavContainer')).filter(e => e.dataset.id);
  assert.deepStrictEqual(links.map(e => e.dataset.id), data.sections.map(s => s.id));
  for (const link of links) {
    link.listeners.click({preventDefault() {}}); await flush();
    assert.strictEqual(get('docsArticleTitle').textContent, data.articles[link.dataset.id].title);
    assert(get('docsArticleBody').children.length > 0);
    assert(!get('docsArticleBody').textContent.includes('Could not load documentation'));
  }
  context.window.location.hash = '#<img src=x onerror=alert(1)>';
  events.hashchange(); await flush();
  assert.strictEqual(get('docsArticleTitle').textContent, 'Document Not Found');
  assert(get('docsArticleBody').textContent.includes('was not found'));
  assert.strictEqual(get('docsArticleBody').children[0].children.length, 0);
  assert(calls.at(-1).includes('%3Cimg'));
}
(async () => { await run(''); await run('#app'); console.log('DOCS_PORTAL_OK'); })().catch(e => { console.error(e); process.exit(1); });
"""
    source = (Path(__file__).resolve().parents[1] / "frontend/docs/docs.js").read_text()
    result = subprocess.run([node, "-e", script], input=json.dumps({
        "sections": sections, "articles": articles, "source": source,
    }), text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "DOCS_PORTAL_OK" in result.stdout


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



