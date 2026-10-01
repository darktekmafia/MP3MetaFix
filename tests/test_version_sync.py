"""Tests for single source of truth version synchronization engine."""

import pytest
from pathlib import Path
from scripts.sync_version import (
    read_current_version,
    bump_version_string,
    sync_html_content,
    check_version_sync,
    BASE_DIR,
    FRONTEND_DIR,
)


def test_authoritative_version_file_exists():
    """Verify that root VERSION exists and contains a valid semantic version."""
    version_file = BASE_DIR / "VERSION"
    assert version_file.exists(), "Root VERSION file missing"
    ver = read_current_version()
    assert len(ver) > 0
    assert ver.count(".") >= 2


def test_bump_version_string():
    """Verify semver bump calculations across major, minor, patch, and dev."""
    assert bump_version_string("0.5.4-dev", "patch") == "0.5.4"
    assert bump_version_string("0.5.4", "patch") == "0.5.5"
    assert bump_version_string("0.5.4-dev", "minor") == "0.6.0"
    assert bump_version_string("0.5.4", "minor") == "0.6.0"
    assert bump_version_string("0.5.4", "major") == "1.0.0"
    assert bump_version_string("0.5.4", "dev") == "0.5.5-dev"
    assert bump_version_string("0.5.4-dev", "dev") == "0.5.4-dev"

    with pytest.raises(ValueError):
        bump_version_string("invalid_version", "minor")


def test_sync_html_content_transformations():
    """Verify that asset tags, badges, and drawer version footers are replaced properly."""
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head>
      <link rel="stylesheet" href="/css/styles.css?v=0.1.0">
      <link rel="stylesheet" href="/docs/docs.css?v=0.1.0">
    </head>
    <body>
      <span class="badge badge-version" id="versionBadge">v0.1.0</span>
      <span class="badge badge-version" id="hubVersionBadge">v0.1.0</span>
      <span class="badge badge-version" id="adminVersionBadge">v0.1.0</span>
      <span class="badge badge-version" id="updateCardVerBadge">v0.1.0</span>
      <span class="drawer-version">MP3MetaFix v0.1.0</span>
      <script src="/js/auth.js?v=0.1.0"></script>
      <script src="/js/app.js?v=0.1.0"></script>
    </body>
    </html>
    """
    updated, count = sync_html_content(sample_html, "0.6.0")
    assert count == 9
    assert 'href="/css/styles.css?v=0.6.0"' in updated
    assert 'href="/docs/docs.css?v=0.6.0"' in updated
    assert 'id="versionBadge">v0.6.0</span>' in updated
    assert 'id="hubVersionBadge">v0.6.0</span>' in updated
    assert 'id="adminVersionBadge">v0.6.0</span>' in updated
    assert 'id="updateCardVerBadge">v0.6.0</span>' in updated
    assert 'class="drawer-version">MP3MetaFix v0.6.0</span>' in updated
    assert 'src="/js/auth.js?v=0.6.0"' in updated
    assert 'src="/js/app.js?v=0.6.0"' in updated


def test_live_repository_version_synchronization():
    """Verify that current codebase has zero version drift between VERSION and frontend HTML files."""
    is_synced, mismatches = check_version_sync()
    assert is_synced, f"Version drift detected: {mismatches}"
