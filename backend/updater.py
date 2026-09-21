"""Update manager and GitHub release checker for MP3MetaFix."""

import os
import time
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any, AsyncGenerator
import httpx

from backend.config import (
    BASE_DIR,
    VERSION,
    GITHUB_REPO,
    GITHUB_REPO_URL,
    get_git_commit,
    get_git_branch,
)

logger = logging.getLogger("mp3metafix.updater")

# In-memory cache for GitHub update checks (TTL: 10 minutes)
_update_cache: Dict[str, Any] = {
    "timestamp": 0,
    "data": None,
}
UPDATE_CACHE_TTL_SECONDS = 600  # 10 minutes


def parse_semver(v_str: str) -> tuple:
    """Parse version string like 'v0.2.0' or '0.1.1' into comparable tuple."""
    clean = v_str.strip().lstrip("vV")
    parts = []
    for chunk in clean.split("."):
        # Extract numeric prefix
        num_str = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num_str) if num_str else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_version_newer(latest: str, current: str) -> bool:
    """Return True if latest version is strictly greater than current version."""
    try:
        return parse_semver(latest) > parse_semver(current)
    except Exception:
        return False


def get_system_version_info() -> Dict[str, Any]:
    """Return comprehensive version and environment metadata."""
    is_git_repo = (BASE_DIR / ".git").is_dir()
    git_commit = get_git_commit()
    git_branch = get_git_branch()
    
    # Check if systemd unit is present
    systemd_service_path = Path("/etc/systemd/system/mp3metafix.service")
    is_systemd_service = systemd_service_path.exists()
    
    return {
        "version": VERSION,
        "git_commit": git_commit,
        "git_branch": git_branch,
        "is_git_repo": is_git_repo,
        "is_systemd_service": is_systemd_service,
        "github_repo": GITHUB_REPO,
        "github_repo_url": GITHUB_REPO_URL,
    }


async def check_github_updates(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Check the GitHub Releases API for the latest published release.
    Falls back to Tags API if no formal Release is found.
    Results are cached in memory for 10 minutes to respect GitHub rate limits.
    """
    global _update_cache
    now = time.time()
    
    if not force_refresh and _update_cache["data"] and (now - _update_cache["timestamp"] < UPDATE_CACHE_TTL_SECONDS):
        cached_data = dict(_update_cache["data"])
        cached_data["cached"] = True
        return cached_data
    
    current_version = VERSION
    result = {
        "current_version": current_version,
        "latest_version": current_version,
        "update_available": False,
        "release_name": f"v{current_version}",
        "release_notes": "You are currently running the latest version of MP3MetaFix.",
        "published_at": None,
        "html_url": f"{GITHUB_REPO_URL}/releases",
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "cached": False,
        "error": None,
    }
    
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"MP3MetaFix-Updater/{current_version}",
    }
    
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        try:
            # 1. Try latest GitHub Release
            releases_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            resp = await client.get(releases_url, headers=headers)
            
            if resp.status_code == 200:
                rel = resp.json()
                tag_name = rel.get("tag_name", "").strip().lstrip("vV")
                if tag_name:
                    result["latest_version"] = tag_name
                    result["release_name"] = rel.get("name") or f"v{tag_name}"
                    result["release_notes"] = rel.get("body") or "No release notes provided."
                    result["published_at"] = rel.get("published_at")
                    result["html_url"] = rel.get("html_url", f"{GITHUB_REPO_URL}/releases")
                    result["update_available"] = is_version_newer(tag_name, current_version)
            elif resp.status_code == 404:
                # 2. Fallback to Tags API if no formal Release object is published yet
                tags_url = f"https://api.github.com/repos/{GITHUB_REPO}/tags"
                tags_resp = await client.get(tags_url, headers=headers)
                if tags_resp.status_code == 200:
                    tags = tags_resp.json()
                    if tags and isinstance(tags, list) and len(tags) > 0:
                        sorted_tags = sorted(
                            tags,
                            key=lambda t: parse_semver(t.get("name", "")),
                            reverse=True
                        )
                        latest_tag = sorted_tags[0].get("name", "").strip().lstrip("vV")
                        if latest_tag:
                            result["latest_version"] = latest_tag
                            result["release_name"] = f"v{latest_tag}"
                            result["release_notes"] = f"Release tag v{latest_tag} is available on GitHub."
                            result["html_url"] = f"{GITHUB_REPO_URL}/releases/tag/v{latest_tag}"
                            result["update_available"] = is_version_newer(latest_tag, current_version)
            elif resp.status_code == 403:
                # Rate limit exceeded
                result["error"] = "GitHub API rate limit exceeded. Please try again in a few minutes."
            else:
                result["error"] = f"GitHub API responded with status {resp.status_code}."

            # 3. If no newer version detected via Releases/Tags, check raw VERSION on main branch
            if not result["update_available"]:
                try:
                    raw_version_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
                    raw_resp = await client.get(raw_version_url)
                    if raw_resp.status_code == 200:
                        raw_ver = raw_resp.text.strip()
                        if raw_ver and is_version_newer(raw_ver, current_version):
                            result["latest_version"] = raw_ver
                            result["release_name"] = f"v{raw_ver}"
                            result["release_notes"] = f"New version v{raw_ver} is available on GitHub."
                            result["html_url"] = f"{GITHUB_REPO_URL}"
                            result["update_available"] = True
                except Exception as exc:
                    logger.debug(f"Raw branch VERSION fallback check: {exc}")
        except httpx.RequestError as exc:
            logger.warning(f"Could not reach GitHub API for update check: {exc}")
            result["error"] = "Could not connect to GitHub to check for updates. Check internet connection."
        except Exception as exc:
            logger.exception("Unexpected error checking GitHub updates")
            result["error"] = "An error occurred while checking for updates."

    # Cache response
    _update_cache["timestamp"] = now
    _update_cache["data"] = result
    return result


_update_jobs = set()


def _event(kind, message, **fields):
    return 'data: ' + json.dumps({'type': kind, 'message': message, **fields}) + '\n\n'


async def stream_install_update(lock_fd=None):
    """Drain installer output, exposing only fixed status text, never raw log content."""
    install_script = BASE_DIR / 'install.sh'
    if not install_script.is_file():
        yield _event('error', 'Update installer is unavailable.', success=False)
        return
    yield _event('step', 'Starting update installation.', step='init')
    try:
        process = await asyncio.create_subprocess_exec(
            'bash', str(install_script), '--update', '--headless',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR), env={**os.environ, 'MP3METAFIX_WEB_UPDATE': '1'}, pass_fds=(() if lock_fd is None else (lock_fd,)),
        )
        # Fixed-size reads handle arbitrary lines without unbounded buffering.
        notified = False
        while await process.stdout.read(4096):
            if not notified:
                yield _event('log', 'Installer is running; detailed output is withheld for security.')
                notified = True
        code = await process.wait()
        if code == 0:
            yield _event('complete', 'Update files installed. Restart the backend service from the local terminal to activate them.', success=True, restart_required=True)
        else:
            yield _event('error', 'Update did not complete successfully. Check service health before retrying.', success=False)
    except Exception as exc:
        logger.error('Update execution failed: %s', type(exc).__name__)
        yield _event('error', 'Update execution failed. Check service health before retrying.', success=False)


def start_install_update():
    """Acquire the process-shared lease before responding, then run independently of SSE."""
    from backend.config import DATA_DIR
    from backend.locking import file_lock
    from fastapi import HTTPException
    lease = file_lock(DATA_DIR / '.update.lock', blocking=False)
    try:
        fd = lease.__enter__()
    except BlockingIOError:
        raise HTTPException(409, 'An update is already in progress.') from None
    queue = asyncio.Queue(maxsize=16)
    def publish(item):
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(item)
    async def run():
        try:
            async for event in stream_install_update(fd):
                publish(event)
        finally:
            # The installer inherits this FD; a worker exit cannot release its lease early.
            lease.__exit__(None, None, None)
            publish(None)
    task = asyncio.create_task(run())
    _update_jobs.add(task)
    task.add_done_callback(_update_jobs.discard)
    async def events():
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
    return events()
