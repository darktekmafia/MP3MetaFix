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
                    if tags and isinstance(tags, list):
                        latest_tag = tags[0].get("name", "").strip().lstrip("vV")
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


async def stream_install_update() -> AsyncGenerator[str, None]:
    """
    Execute `install.sh --update --headless` in an async subprocess
    and stream stdout/stderr line-by-line as Server-Sent Events (SSE).
    """
    install_script = BASE_DIR / "install.sh"
    
    if not install_script.exists():
        payload = json.dumps({"type": "error", "message": "install.sh not found on server."})
        yield f"data: {payload}\n\n"
        return

    # Ensure executable permission
    try:
        os.chmod(install_script, 0o755)
    except Exception:
        pass

    # Send initial event
    yield f"data: {json.dumps({'type': 'step', 'step': 'init', 'message': 'Starting MP3MetaFix in-app updater...'})}\n\n"
    await asyncio.sleep(0.1)

    cmd = ["bash", str(install_script), "--update", "--headless"]
    logger.info(f"Executing in-app updater: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
        )

        # Stream output lines
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                # Strip excessive ANSI codes if needed or pass cleanly
                payload = json.dumps({"type": "log", "message": text})
                yield f"data: {payload}\n\n"
                await asyncio.sleep(0.01)

        await process.wait()
        logger.info(f"Update process finished with returncode: {process.returncode}")

        # Returncode 0 is normal exit; -15 or 143 is SIGTERM from systemd service restart
        if process.returncode in (0, -15, 143):
            logger.info("In-app update completed successfully.")
            payload = json.dumps({
                "type": "complete",
                "success": True,
                "message": "Update installed successfully! Server is restarting...",
            })
            yield f"data: {payload}\n\n"
        else:
            logger.error(f"In-app update failed with exit code {process.returncode}")
            payload = json.dumps({
                "type": "error",
                "success": False,
                "message": f"Update script exited with error code {process.returncode}.",
            })
            yield f"data: {payload}\n\n"

    except Exception as exc:
        logger.exception("Error executing in-app update subprocess")
        payload = json.dumps({"type": "error", "message": f"Execution error: {str(exc)}"})
        yield f"data: {payload}\n\n"
