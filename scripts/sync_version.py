#!/usr/bin/env python3
"""
MP3MetaFix - Single Source of Truth Version Synchronization Engine

Synchronizes application version from root `VERSION` across:
- Frontend asset cache-busting query tags (`<script src="/js/app.js?v=X.Y.Z">`, `<link href="/css/styles.css?v=X.Y.Z">`)
- Static HTML version badges (`#versionBadge`, `#hubVersionBadge`, `#adminVersionBadge`, `#updateCardVerBadge`)
- Mobile navigation drawer version labels (`.drawer-version`)

Usage:
  python scripts/sync_version.py               # Sync all files to match root VERSION
  python scripts/sync_version.py --check       # Verify all files match root VERSION (exit 0 if ok, 1 if out of sync)
  python scripts/sync_version.py --set 0.6.0   # Update VERSION to 0.6.0 and sync all files
  python scripts/sync_version.py --bump minor  # Bump version (e.g. 0.5.4-dev -> 0.6.0) and sync
"""

import sys
import re
import argparse
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE_DIR / "VERSION"
FRONTEND_DIR = BASE_DIR / "frontend"


def read_current_version() -> str:
    """Read version string from authoritative root VERSION file."""
    if not VERSION_FILE.exists():
        raise FileNotFoundError(f"Authoritative VERSION file missing at {VERSION_FILE}")
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def write_version(new_version: str) -> None:
    """Write updated version string to root VERSION file."""
    VERSION_FILE.write_text(new_version.strip() + "\n", encoding="utf-8")


def bump_version_string(current: str, part: str) -> str:
    """Compute bumped version string for major, minor, patch, or dev."""
    # Match standard semver e.g. 0.5.3 or 0.5.4-dev
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.]+))?$", current)
    if not m:
        raise ValueError(f"Cannot automatically bump non-semver version: '{current}'")
    
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    prerelease = m.group(4)

    if part == "major":
        return f"{major + 1}.0.0"
    elif part == "minor":
        return f"{major}.{minor + 1}.0"
    elif part == "patch":
        if prerelease:
            return f"{major}.{minor}.{patch}"
        return f"{major}.{minor}.{patch + 1}"
    elif part == "dev":
        if prerelease:
            return current
        return f"{major}.{minor}.{patch + 1}-dev"
    else:
        raise ValueError(f"Unknown bump target: {part}. Supported: major, minor, patch, dev")


def get_html_files() -> List[Path]:
    """Retrieve all HTML template files in the frontend tree."""
    return sorted(list(FRONTEND_DIR.glob("**/*.html")))


def sync_html_content(content: str, target_ver: str) -> Tuple[str, int]:
    """
    Apply version synchronization transformations to an HTML string.
    Returns (updated_content, changes_count).
    """
    changes = 0
    updated = content

    # 1. Asset Cache-Busting Query Tags (e.g. /css/styles.css?v=X.Y.Z or /js/auth.js?v=X.Y.Z)
    asset_regex = re.compile(r'((?:src|href)=["\']/[^"\']+\.(?:js|css))\?v=[^"\']+(["\'])')
    
    def replace_asset(m):
        nonlocal changes
        old_tag = m.group(0)
        new_tag = f"{m.group(1)}?v={target_ver}{m.group(2)}"
        if old_tag != new_tag:
            changes += 1
        return new_tag

    updated = asset_regex.sub(replace_asset, updated)

    # 2. Version Badges (e.g. <span class="badge badge-version" id="versionBadge">vX.Y.Z</span>)
    badge_regex = re.compile(
        r'(<span[^>]*class=["\'][^"\']*badge-version[^"\']*["\'][^>]*id=["\'](?:versionBadge|hubVersionBadge|adminVersionBadge|updateCardVerBadge)["\'][^>]*>)[^<]*(</span>)'
    )

    def replace_badge(m):
        nonlocal changes
        old_tag = m.group(0)
        new_tag = f"{m.group(1)}v{target_ver}{m.group(2)}"
        if old_tag != new_tag:
            changes += 1
        return new_tag

    updated = badge_regex.sub(replace_badge, updated)

    # 3. Mobile Navigation Drawer Version Footer (e.g. <span class="drawer-version">MP3MetaFix vX.Y.Z</span>)
    drawer_regex = re.compile(
        r'(<span[^>]*class=["\'][^"\']*drawer-version[^"\']*["\'][^>]*>MP3MetaFix\s+)[^<]*(</span>)'
    )

    def replace_drawer(m):
        nonlocal changes
        old_tag = m.group(0)
        new_tag = f"{m.group(1)}v{target_ver}{m.group(2)}"
        if old_tag != new_tag:
            changes += 1
        return new_tag

    updated = drawer_regex.sub(replace_drawer, updated)

    return updated, changes


def check_version_sync() -> Tuple[bool, List[str]]:
    """
    Check whether all frontend files match the authoritative VERSION.
    Returns (is_synced, list_of_mismatch_messages).
    """
    target_ver = read_current_version()
    mismatches = []
    html_files = get_html_files()

    if not html_files:
        return False, ["No HTML template files found in frontend/ directory."]

    for file_path in html_files:
        rel_path = file_path.relative_to(BASE_DIR)
        content = file_path.read_text(encoding="utf-8")
        _, count = sync_html_content(content, target_ver)
        if count > 0:
            mismatches.append(f"{rel_path}: contains {count} outdated version references (target: {target_ver})")

    return (len(mismatches) == 0), mismatches


def perform_sync(target_ver: Optional[str] = None) -> int:
    """
    Synchronize all frontend files to target_ver (or current VERSION).
    Returns total count of modified references.
    """
    if target_ver:
        write_version(target_ver)
    else:
        target_ver = read_current_version()

    total_changes = 0
    html_files = get_html_files()

    for file_path in html_files:
        rel_path = file_path.relative_to(BASE_DIR)
        content = file_path.read_text(encoding="utf-8")
        updated, count = sync_html_content(content, target_ver)
        if count > 0:
            file_path.write_text(updated, encoding="utf-8")
            print(f"  [✓] Updated {count} reference(s) in {rel_path}")
            total_changes += count
        else:
            print(f"  [-] In sync: {rel_path}")

    return total_changes


def main():
    parser = argparse.ArgumentParser(
        description="MP3MetaFix Single Source of Truth Version Synchronization Engine"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Check if all frontend assets and templates match VERSION (exit 0 if ok, 1 if out of sync)",
    )
    group.add_argument(
        "--set",
        metavar="VERSION",
        dest="set_version",
        help="Set new version string in VERSION and sync across all frontend assets",
    )
    group.add_argument(
        "--bump",
        choices=["major", "minor", "patch", "dev"],
        help="Bump current version by part and sync across all frontend assets",
    )

    args = parser.parse_args()

    try:
        current_ver = read_current_version()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        print(f"Checking version synchronization against VERSION ({current_ver})...")
        is_synced, mismatches = check_version_sync()
        if is_synced:
            print(f"✓ All frontend assets and templates are fully synchronized to v{current_ver}.")
            sys.exit(0)
        else:
            print(f"✗ Version synchronization mismatch detected:", file=sys.stderr)
            for msg in mismatches:
                print(f"  - {msg}", file=sys.stderr)
            print("\nRun `python scripts/sync_version.py` to synchronize all assets.", file=sys.stderr)
            sys.exit(1)

    target_ver = current_ver
    if args.set_version:
        target_ver = args.set_version.strip()
        print(f"Setting authoritative version to: {target_ver}")
    elif args.bump:
        target_ver = bump_version_string(current_ver, args.bump)
        print(f"Bumping version from {current_ver} to: {target_ver}")
    else:
        print(f"Synchronizing all frontend assets to authoritative VERSION: {target_ver}")

    changes = perform_sync(target_ver)
    print(f"\n✓ Synchronization complete. {changes} total reference(s) updated to v{target_ver}.")


if __name__ == "__main__":
    main()
