"""Systemd service migration helper for MP3MetaFix.

Safely updates legacy ExecStart launch commands in existing systemd units to use
--no-proxy-headers and dynamic $MP3METAFIX_HOST / $MP3METAFIX_PORT variables while
strictly limiting parsing and modifications to the [Service] section and preserving
all administrator customizations, environment variables, and cgroup limits.
"""

import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple


class MigrationStatus:
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    FAILED = "failed"


def format_systemd_arg(token: str) -> str:
    """Format a command-line argument for systemd ExecStart.

    Ensures variable expressions like $MP3METAFIX_HOST remain unquoted so systemd
    can perform variable substitution, while quoting arguments that contain spaces.
    """
    if token in ("$MP3METAFIX_HOST", "$MP3METAFIX_PORT", "${MP3METAFIX_HOST}", "${MP3METAFIX_PORT}"):
        return token
    if " " in token or "\t" in token or '"' in token:
        escaped = token.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return token


def migrate_service_content(content: str) -> Tuple[str, str, Optional[str]]:
    """Migrate systemd service content to use modern launch parameters.

    Parsing and modifications are strictly restricted to the [Service] section.
    Unsafe, unparseable, or shell-wrapped ExecStart commands are explicitly rejected.

    Returns:
        (new_content, status, error_message):
            - status: MigrationStatus.CHANGED, MigrationStatus.UNCHANGED, or MigrationStatus.FAILED
            - error_message: None on CHANGED/UNCHANGED, or string description on FAILED
    """
    if not content or not content.strip():
        return content, MigrationStatus.UNCHANGED, None

    lines = content.splitlines()
    section_pattern = re.compile(r"^\s*\[([a-zA-Z0-9_\-]+)\]\s*$")

    current_section = None
    service_exec_indices = []
    has_host_env = False
    has_port_env = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        sec_match = section_pattern.match(stripped)
        if sec_match:
            current_section = sec_match.group(1)
            continue

        if current_section == "Service":
            if stripped.startswith("ExecStart="):
                service_exec_indices.append(i)
            elif stripped.startswith("Environment=") or stripped.startswith("EnvironmentFile="):
                if "MP3METAFIX_HOST=" in stripped:
                    has_host_env = True
                if "MP3METAFIX_PORT=" in stripped:
                    has_port_env = True

    # If no [Service] section or no ExecStart inside [Service], check if this is an MP3MetaFix unit
    if not service_exec_indices:
        return content, MigrationStatus.UNCHANGED, None

    # Systemd simple service shouldn't have multiple ExecStart lines
    if len(service_exec_indices) > 1:
        return (
            content,
            MigrationStatus.FAILED,
            "Multiple ExecStart directives detected in [Service] section; cannot migrate safely.",
        )

    exec_idx = service_exec_indices[0]
    exec_line = lines[exec_idx]
    exec_prefix, exec_cmd = exec_line.split("ExecStart=", 1)
    exec_cmd_stripped = exec_cmd.strip()

    # If it is not an MP3MetaFix uvicorn command, leave untouched as UNCHANGED
    if "uvicorn" not in exec_cmd_stripped or "backend.main:app" not in exec_cmd_stripped:
        return content, MigrationStatus.UNCHANGED, None

    # Check for dangerous shell operators or subshell wrappers that cannot be safely parsed
    dangerous_shell_chars = ["|", ";", "&&", "||", ">", "<", "$(", "`"]
    if any(char in exec_cmd_stripped for char in dangerous_shell_chars):
        return (
            content,
            MigrationStatus.FAILED,
            "ExecStart contains shell pipelines, redirection, or compound commands that cannot be migrated automatically.",
        )

    # Parse command tokens with shlex
    try:
        tokens = shlex.split(exec_cmd_stripped, comments=False, posix=True)
    except ValueError as e:
        return (
            content,
            MigrationStatus.FAILED,
            f"Failed to parse ExecStart command quoting: {e}",
        )

    if not tokens:
        return (
            content,
            MigrationStatus.FAILED,
            "ExecStart command is empty.",
        )

    # Verify that uvicorn and backend.main:app exist in tokens
    uvicorn_idx = -1
    app_idx = -1
    for idx, tok in enumerate(tokens):
        if tok == "uvicorn" or tok.endswith("/uvicorn"):
            uvicorn_idx = idx
        elif tok == "backend.main:app":
            app_idx = idx

    if uvicorn_idx == -1 or app_idx == -1 or app_idx != uvicorn_idx + 1:
        return (
            content,
            MigrationStatus.FAILED,
            "ExecStart does not follow standard '.../uvicorn backend.main:app [options]' invocation.",
        )

    new_tokens = list(tokens[: app_idx + 1])
    i = app_idx + 1
    extracted_host = None
    extracted_port = None
    has_no_proxy_headers = False

    while i < len(tokens):
        token = tokens[i]
        if token == "--host" and i + 1 < len(tokens):
            extracted_host = tokens[i + 1]
            new_tokens.extend(["--host", "$MP3METAFIX_HOST"])
            i += 2
        elif token.startswith("--host="):
            extracted_host = token.split("=", 1)[1]
            new_tokens.extend(["--host", "$MP3METAFIX_HOST"])
            i += 1
        elif token == "--port" and i + 1 < len(tokens):
            extracted_port = tokens[i + 1]
            new_tokens.extend(["--port", "$MP3METAFIX_PORT"])
            i += 2
        elif token.startswith("--port="):
            extracted_port = token.split("=", 1)[1]
            new_tokens.extend(["--port", "$MP3METAFIX_PORT"])
            i += 1
        elif token == "--no-proxy-headers":
            has_no_proxy_headers = True
            new_tokens.append(token)
            i += 1
        else:
            new_tokens.append(token)
            i += 1

    # Ensure required flags are present
    if "--host" not in new_tokens:
        new_tokens.extend(["--host", "$MP3METAFIX_HOST"])

    if "--port" not in new_tokens:
        new_tokens.extend(["--port", "$MP3METAFIX_PORT"])

    if not has_no_proxy_headers:
        new_tokens.append("--no-proxy-headers")

    formatted_args = [format_systemd_arg(t) for t in new_tokens]
    updated_cmd = " ".join(formatted_args)

    new_lines = list(lines)
    new_lines[exec_idx] = f"{exec_prefix}ExecStart={updated_cmd}"

    # Insert missing host/port environment variables into [Service] before ExecStart
    insert_env_lines = []
    if not has_host_env:
        val = (
            extracted_host
            if (extracted_host and extracted_host not in ("$MP3METAFIX_HOST", "${MP3METAFIX_HOST}"))
            else "127.0.0.1"
        )
        insert_env_lines.append(f'Environment="MP3METAFIX_HOST={val}"')

    if not has_port_env:
        val = (
            extracted_port
            if (extracted_port and extracted_port not in ("$MP3METAFIX_PORT", "${MP3METAFIX_PORT}"))
            else "8844"
        )
        insert_env_lines.append(f'Environment="MP3METAFIX_PORT={val}"')

    if insert_env_lines:
        for env_line in reversed(insert_env_lines):
            new_lines.insert(exec_idx, env_line)

    # Check for missing BindReadOnlyPaths for docs if unit uses sandboxed component binds
    has_backend_bind = False
    has_docs_bind = False
    bind_source = None
    bind_runtime = None
    last_bind_idx = -1

    for idx, line in enumerate(new_lines):
        s_line = line.strip()
        if s_line.startswith("BindReadOnlyPaths="):
            last_bind_idx = idx
            bind_val = s_line.split("BindReadOnlyPaths=", 1)[1].strip()
            if "/docs:" in bind_val or bind_val.endswith("/docs"):
                has_docs_bind = True
            if "/backend:" in bind_val:
                has_backend_bind = True
                parts = bind_val.split(":", 1)
                if len(parts) == 2:
                    bind_source = Path(parts[0]).parent
                    bind_runtime = Path(parts[1]).parent

    if has_backend_bind and not has_docs_bind and bind_source and bind_runtime:
        docs_source = bind_source / "docs"
        docs_runtime = bind_runtime / "docs"
        if docs_source.is_dir():
            try:
                docs_runtime.mkdir(mode=0o755, exist_ok=True)
                os.chmod(docs_runtime, 0o755)
            except Exception:
                pass
            new_lines.insert(last_bind_idx + 1, f"BindReadOnlyPaths={docs_source}:{docs_runtime}")

    new_content = "\n".join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"

    if new_content == content:
        return content, MigrationStatus.UNCHANGED, None

    return new_content, MigrationStatus.CHANGED, None


def migrate_service_file(file_path: Path) -> Tuple[str, str]:
    """Migrate a service file on disk safely, atomically, and preserving permissions.

    Returns:
        (status, message): status is CHANGED, UNCHANGED, or FAILED.
    """
    if not file_path.is_file():
        return MigrationStatus.FAILED, f"Target file '{file_path}' does not exist or is not a file."

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return MigrationStatus.FAILED, f"Could not read '{file_path}': {e}"

    new_content, status, err_msg = migrate_service_content(content)
    if status == MigrationStatus.FAILED:
        return MigrationStatus.FAILED, f"Migration rejected for '{file_path}': {err_msg}"

    if status == MigrationStatus.UNCHANGED:
        return MigrationStatus.UNCHANGED, f"Service '{file_path}' is already up to date."

    # Status is CHANGED: perform atomic write preserving permissions
    temp_fd = None
    temp_path = None
    try:
        orig_stat = file_path.stat()
        parent_dir = file_path.parent

        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=parent_dir,
            prefix=f".{file_path.name}.tmp."
        )
        temp_path = Path(temp_path_str)

        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            temp_fd = None  # Closed by context manager
            f.write(new_content)

        # Preserve file permissions and ownership
        os.chmod(temp_path, orig_stat.st_mode)
        try:
            os.chown(temp_path, orig_stat.st_uid, orig_stat.st_gid)
        except (PermissionError, AttributeError):
            pass

        # Atomic in-place replacement
        os.replace(temp_path, file_path)
        temp_path = None
        return MigrationStatus.CHANGED, f"Migrated launch settings in '{file_path}'."
    except Exception as e:
        return MigrationStatus.FAILED, f"Failed writing '{file_path}': {e}"
    finally:
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def main():
    if len(sys.argv) < 2:
        print("Usage: migrate_service.py <path_to_service_file>", file=sys.stderr)
        sys.exit(1)

    target_path = Path(sys.argv[1])
    status, msg = migrate_service_file(target_path)

    if status == MigrationStatus.CHANGED:
        print(f"[+] {msg}")
        sys.exit(0)
    elif status == MigrationStatus.UNCHANGED:
        print(f"[*] {msg}")
        sys.exit(2)
    else:  # FAILED
        print(f"[!] Error: {msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
