"""Systemd service network access configuration helper for MP3MetaFix.

Provides safe inspection and modification of service network binding settings
(MP3METAFIX_HOST, MP3METAFIX_PORT, MP3METAFIX_TRUST_PROXIES, and MP3METAFIX_TRUSTED_PROXIES)
in systemd units, preserving all existing directives, permissions, and security limits.
"""

import ipaddress
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class ConfigStatus:
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    FAILED = "failed"


def validate_bind_host(host: str) -> bool:
    """Validate that the provided bind host is a valid IP address or hostname.

    Rejects shell metacharacters, whitespace, quotes, or dangerous characters.
    """
    if not host or not isinstance(host, str):
        return False

    # Check for invalid characters in raw string
    disallowed = [" ", "\t", "\n", "\r", ";", "&", "|", "`", "$", "(", ")", "<", ">", '"', "'", "\\"]
    if any(char in host for char in disallowed):
        return False

    norm_host = host.strip()
    if not norm_host:
        return False

    # Check if special keyword
    if norm_host.lower() in ("localhost", "lan", "local"):
        return True

    # Check if valid IP address (IPv4 or IPv6)
    try:
        ipaddress.ip_address(norm_host)
        return True
    except ValueError:
        pass

    # Check if valid RFC 1123 hostname / domain
    hostname_regex = re.compile(
        r"^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
    )
    if hostname_regex.match(norm_host):
        return True

    return False


def validate_bind_port(port_val: Any) -> bool:
    """Validate that port is an integer between 1 and 65535."""
    try:
        port = int(str(port_val).strip())
        return 1 <= port <= 65535
    except (ValueError, TypeError):
        return False


def validate_trusted_proxies(proxies_str: str) -> bool:
    """Validate comma-separated list of trusted proxy IPs or CIDR networks.

    Rejects shell metacharacters, dangerous characters, or invalid IP/CIDR entries.
    """
    if not proxies_str or not isinstance(proxies_str, str):
        return False

    disallowed = ["\t", "\n", "\r", ";", "&", "|", "`", "$", "(", ")", "<", ">", '"', "'", "\\"]
    if any(char in proxies_str for char in disallowed):
        return False

    parts = [p.strip() for p in proxies_str.split(",") if p.strip()]
    if not parts:
        return False

    for part in parts:
        # Validate each part as valid IP address or CIDR network
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            return False

    return True


def validate_proxy_host(domain_or_url: str) -> bool:
    """Validate reverse proxy domain, hostname, or URL for health check probing.

    Supports domain names (e.g. mp3.yourdomain.com), hostnames with ports,
    full URLs (http:// or https://), and clear keywords (none, clear).
    Rejects shell metacharacters, whitespace, semicolons, quotes, and dangerous characters.
    """
    if not domain_or_url or not isinstance(domain_or_url, str):
        return False

    disallowed = [" ", "\t", "\n", "\r", ";", "&", "|", "`", "$", "(", ")", "<", ">", '"', "'", "\\"]
    if any(char in domain_or_url for char in disallowed):
        return False

    clean = domain_or_url.strip()
    if clean.lower() in ("none", "clear", "disable", "disabled", "false"):
        return True

    # Strip scheme if present
    if clean.lower().startswith("https://"):
        clean = clean[8:]
    elif clean.lower().startswith("http://"):
        clean = clean[7:]

    # Strip path / trailing slash if present
    if "/" in clean:
        clean = clean.split("/", 1)[0]

    if not clean:
        return False

    # Handle IPv6 brackets if any e.g. [::1]:8844
    if clean.startswith("[") and "]" in clean:
        ip_part = clean[1:clean.index("]")]
        port_part = clean[clean.index("]") + 1:]
        if port_part.startswith(":"):
            if not validate_bind_port(port_part[1:]):
                return False
        elif port_part != "":
            return False
        try:
            ipaddress.IPv6Address(ip_part)
            return True
        except ValueError:
            return False
    elif ":" in clean:
        host_part, port_part = clean.rsplit(":", 1)
        if not validate_bind_port(port_part):
            return False
        clean = host_part

    # Validate remaining host as IP address or RFC 1123 hostname
    try:
        ipaddress.ip_address(clean)
        return True
    except ValueError:
        pass

    hostname_regex = re.compile(
        r"^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
    )
    return bool(hostname_regex.match(clean))


def parse_env_file(content: str) -> Dict[str, str]:
    """Parse standard systemd EnvironmentFile content into a dictionary of key-value pairs."""
    env = {}
    if not content:
        return env
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if "=" in stripped:
            key, val = stripped.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
                # Unescape standard escaped characters
                val = val.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
            env[key] = val
    return env


def render_env_file(content: str, updates: Dict[str, Optional[str]]) -> str:
    """Apply updates to an EnvironmentFile content, modifying existing keys or appending new ones.

    If an update value is None, the key is left unchanged.
    If an update value is "" (empty string) and key is MP3METAFIX_PROXY_HOST, the line is removed.
    """
    lines = content.splitlines() if content else []
    remaining_updates = dict(updates)
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in remaining_updates:
                val = remaining_updates.pop(key)
                if val is not None:
                    if val == "" and key == "MP3METAFIX_PROXY_HOST":
                        # Omit/delete proxy host if cleared
                        continue
                    # Escape quotes and backslashes for env file
                    escaped_val = val.replace("\\", "\\\\").replace('"', '\\"')
                    new_lines.append(f'{key}="{escaped_val}"')
                    continue
        new_lines.append(line)

    for key, val in remaining_updates.items():
        if val is not None and (val != "" or key != "MP3METAFIX_PROXY_HOST"):
            escaped_val = val.replace("\\", "\\\\").replace('"', '\\"')
            new_lines.append(f'{key}="{escaped_val}"')

    result = "\n".join(new_lines)
    if not result.endswith("\n"):
        result += "\n"
    return result


def get_service_binding(content: str, unit_file_path: Optional[Path] = None) -> Dict[str, Any]:
    """Extract network binding configuration from a systemd unit content and referenced EnvironmentFiles.

    Only parses within the [Service] section.

    Returns:
        Dict with keys: host, port, trust_proxies, trusted_proxies, proxy_host, is_configured, env_file
    """
    result = {
        "host": "127.0.0.1",
        "port": "8844",
        "trust_proxies": "false",
        "trusted_proxies": "127.0.0.1,::1",
        "proxy_host": "",
        "is_configured": False,
        "env_file": None,
    }

    if not content:
        return result

    lines = content.splitlines()
    section_pattern = re.compile(r"^\s*\[([a-zA-Z0-9_\-]+)\]\s*$")
    current_section = None
    env_files = []

    for line in lines:
        stripped = line.strip()
        sec_match = section_pattern.match(stripped)
        if sec_match:
            current_section = sec_match.group(1)
            continue

        if current_section == "Service":
            if stripped.startswith("Environment="):
                val_part = stripped.split("Environment=", 1)[1].strip().strip('"')
                for item in val_part.split():
                    if item.startswith("MP3METAFIX_HOST="):
                        result["host"] = item.split("=", 1)[1]
                        result["is_configured"] = True
                    elif item.startswith("MP3METAFIX_PORT="):
                        result["port"] = item.split("=", 1)[1]
                        result["is_configured"] = True
                    elif item.startswith("MP3METAFIX_TRUST_PROXIES="):
                        result["trust_proxies"] = item.split("=", 1)[1].lower()
                        result["is_configured"] = True
                    elif item.startswith("MP3METAFIX_TRUSTED_PROXIES="):
                        result["trusted_proxies"] = item.split("=", 1)[1]
                        result["is_configured"] = True
                    elif item.startswith("MP3METAFIX_PROXY_HOST="):
                        result["proxy_host"] = item.split("=", 1)[1]
                        result["is_configured"] = True
            elif stripped.startswith("EnvironmentFile="):
                raw_path = stripped.split("EnvironmentFile=", 1)[1].strip().strip('"')
                raw_path = raw_path.lstrip('-').strip()
                if raw_path:
                    env_files.append(raw_path)

    for env_file_str in env_files:
        env_p = Path(env_file_str)
        if not env_p.is_absolute() and unit_file_path:
            env_p = unit_file_path.parent / env_p
        result["env_file"] = str(env_p)
        if env_p.is_file():
            try:
                env_dict = parse_env_file(env_p.read_text(encoding="utf-8"))
                if "MP3METAFIX_HOST" in env_dict:
                    result["host"] = env_dict["MP3METAFIX_HOST"]
                    result["is_configured"] = True
                if "MP3METAFIX_PORT" in env_dict:
                    result["port"] = env_dict["MP3METAFIX_PORT"]
                    result["is_configured"] = True
                if "MP3METAFIX_TRUST_PROXIES" in env_dict:
                    result["trust_proxies"] = env_dict["MP3METAFIX_TRUST_PROXIES"].lower()
                    result["is_configured"] = True
                if "MP3METAFIX_TRUSTED_PROXIES" in env_dict:
                    result["trusted_proxies"] = env_dict["MP3METAFIX_TRUSTED_PROXIES"]
                    result["is_configured"] = True
                if "MP3METAFIX_PROXY_HOST" in env_dict:
                    result["proxy_host"] = env_dict["MP3METAFIX_PROXY_HOST"]
                    result["is_configured"] = True
            except Exception:
                pass

    return result


def set_service_binding(
    content: str,
    host: Optional[str] = None,
    port: Optional[str] = None,
    trust_proxies: Optional[Any] = None,
    trusted_proxies: Optional[str] = None,
    proxy_host: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    """Update host, port, proxy trust, and/or proxy domain configuration in systemd service unit content.

    Strictly modifies only the [Service] section and preserves all other directives.

    Returns:
        (new_content, status, error_message):
            - status: ConfigStatus.CHANGED, ConfigStatus.UNCHANGED, or ConfigStatus.FAILED
    """
    if host is not None:
        norm_host = host.strip()
        if norm_host.lower() == "lan":
            norm_host = "0.0.0.0"
        elif norm_host.lower() == "local":
            norm_host = "127.0.0.1"

        if not validate_bind_host(norm_host):
            return content, ConfigStatus.FAILED, f"Invalid bind host: '{host}'"
        target_host = norm_host
    else:
        target_host = None

    if port is not None:
        norm_port = str(port).strip()
        if not validate_bind_port(norm_port):
            return content, ConfigStatus.FAILED, f"Invalid bind port: '{port}'"
        target_port = norm_port
    else:
        target_port = None

    if trust_proxies is not None:
        if isinstance(trust_proxies, bool):
            target_trust_proxies = "true" if trust_proxies else "false"
        else:
            norm_tp = str(trust_proxies).strip().lower()
            if norm_tp in ("true", "1", "yes", "enable", "enabled"):
                target_trust_proxies = "true"
            elif norm_tp in ("false", "0", "no", "disable", "disabled"):
                target_trust_proxies = "false"
            else:
                return content, ConfigStatus.FAILED, f"Invalid trust_proxies value: '{trust_proxies}' (use true or false)"
    else:
        target_trust_proxies = None

    if trusted_proxies is not None:
        norm_tps = str(trusted_proxies).strip()
        if not validate_trusted_proxies(norm_tps):
            return content, ConfigStatus.FAILED, f"Invalid trusted_proxies value: '{trusted_proxies}'"
        # Format as clean comma-separated list without spaces
        target_trusted_proxies = ",".join(p.strip() for p in norm_tps.split(",") if p.strip())
    else:
        target_trusted_proxies = None

    if proxy_host is not None:
        norm_ph = str(proxy_host).strip()
        if norm_ph.lower() in ("", "none", "clear", "disable", "disabled", "false"):
            target_proxy_host = ""
        else:
            if not validate_proxy_host(norm_ph):
                return content, ConfigStatus.FAILED, f"Invalid proxy domain / hostname: '{proxy_host}'"
            target_proxy_host = norm_ph
    else:
        target_proxy_host = None

    if not content or not content.strip():
        return content, ConfigStatus.FAILED, "Unit content is empty."

    lines = content.splitlines()
    section_pattern = re.compile(r"^\s*\[([a-zA-Z0-9_\-]+)\]\s*$")
    current_section = None

    service_start_idx = None
    exec_idx = None
    host_line_idx = None
    port_line_idx = None
    trust_line_idx = None
    trusted_line_idx = None
    proxy_host_line_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        sec_match = section_pattern.match(stripped)
        if sec_match:
            current_section = sec_match.group(1)
            if current_section == "Service" and service_start_idx is None:
                service_start_idx = i
            continue

        if current_section == "Service":
            if stripped.startswith("ExecStart=") and exec_idx is None:
                exec_idx = i
            elif stripped.startswith("Environment="):
                if "MP3METAFIX_HOST=" in stripped:
                    host_line_idx = i
                if "MP3METAFIX_PORT=" in stripped:
                    port_line_idx = i
                if "MP3METAFIX_TRUST_PROXIES=" in stripped:
                    trust_line_idx = i
                if "MP3METAFIX_TRUSTED_PROXIES=" in stripped:
                    trusted_line_idx = i
                if "MP3METAFIX_PROXY_HOST=" in stripped:
                    proxy_host_line_idx = i

    if service_start_idx is None:
        return content, ConfigStatus.FAILED, "No [Service] section found in unit content."

    new_lines = list(lines)

    # 1. Update or insert host
    if target_host is not None:
        if host_line_idx is not None:
            new_lines[host_line_idx] = f'Environment="MP3METAFIX_HOST={target_host}"'
        else:
            insert_pos = exec_idx if exec_idx is not None else service_start_idx + 1
            new_lines.insert(insert_pos, f'Environment="MP3METAFIX_HOST={target_host}"')
            if exec_idx is not None:
                exec_idx += 1
            if port_line_idx is not None and port_line_idx >= insert_pos:
                port_line_idx += 1
            if trust_line_idx is not None and trust_line_idx >= insert_pos:
                trust_line_idx += 1
            if trusted_line_idx is not None and trusted_line_idx >= insert_pos:
                trusted_line_idx += 1
            if proxy_host_line_idx is not None and proxy_host_line_idx >= insert_pos:
                proxy_host_line_idx += 1

    # 2. Update or insert port
    if target_port is not None:
        if port_line_idx is not None:
            new_lines[port_line_idx] = f'Environment="MP3METAFIX_PORT={target_port}"'
        else:
            insert_pos = exec_idx if exec_idx is not None else service_start_idx + 1
            new_lines.insert(insert_pos, f'Environment="MP3METAFIX_PORT={target_port}"')
            if exec_idx is not None:
                exec_idx += 1
            if trust_line_idx is not None and trust_line_idx >= insert_pos:
                trust_line_idx += 1
            if trusted_line_idx is not None and trusted_line_idx >= insert_pos:
                trusted_line_idx += 1
            if proxy_host_line_idx is not None and proxy_host_line_idx >= insert_pos:
                proxy_host_line_idx += 1

    # 3. Update or insert trust_proxies
    if target_trust_proxies is not None:
        if trust_line_idx is not None:
            new_lines[trust_line_idx] = f'Environment="MP3METAFIX_TRUST_PROXIES={target_trust_proxies}"'
        else:
            insert_pos = exec_idx if exec_idx is not None else service_start_idx + 1
            new_lines.insert(insert_pos, f'Environment="MP3METAFIX_TRUST_PROXIES={target_trust_proxies}"')
            if exec_idx is not None:
                exec_idx += 1
            if trusted_line_idx is not None and trusted_line_idx >= insert_pos:
                trusted_line_idx += 1
            if proxy_host_line_idx is not None and proxy_host_line_idx >= insert_pos:
                proxy_host_line_idx += 1

    # 4. Update or insert trusted_proxies
    if target_trusted_proxies is not None:
        if trusted_line_idx is not None:
            new_lines[trusted_line_idx] = f'Environment="MP3METAFIX_TRUSTED_PROXIES={target_trusted_proxies}"'
        else:
            insert_pos = exec_idx if exec_idx is not None else service_start_idx + 1
            new_lines.insert(insert_pos, f'Environment="MP3METAFIX_TRUSTED_PROXIES={target_trusted_proxies}"')
            if exec_idx is not None:
                exec_idx += 1
            if proxy_host_line_idx is not None and proxy_host_line_idx >= insert_pos:
                proxy_host_line_idx += 1

    # 5. Update, insert, or clear proxy_host
    if target_proxy_host is not None:
        if target_proxy_host == "":
            if proxy_host_line_idx is not None:
                del new_lines[proxy_host_line_idx]
        else:
            if proxy_host_line_idx is not None:
                new_lines[proxy_host_line_idx] = f'Environment="MP3METAFIX_PROXY_HOST={target_proxy_host}"'
            else:
                insert_pos = exec_idx if exec_idx is not None else service_start_idx + 1
                new_lines.insert(insert_pos, f'Environment="MP3METAFIX_PROXY_HOST={target_proxy_host}"')

    new_content = "\n".join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"

    if new_content == content:
        return content, ConfigStatus.UNCHANGED, None

    return new_content, ConfigStatus.CHANGED, None


def update_service_file(
    file_path: Path,
    host: Optional[str] = None,
    port: Optional[str] = None,
    trust_proxies: Optional[Any] = None,
    trusted_proxies: Optional[str] = None,
    proxy_host: Optional[str] = None,
) -> Tuple[str, str]:
    """Safely update network binding in a service file and/or its referenced EnvironmentFile on disk.

    Performs atomic file replacement and preserves file permissions.
    """
    if not file_path.is_file():
        return ConfigStatus.FAILED, f"Target file '{file_path}' does not exist or is not a file."

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return ConfigStatus.FAILED, f"Could not read '{file_path}': {e}"

    # First validate the requested settings
    target_host = None
    if host is not None:
        norm_host = host.strip()
        if norm_host.lower() == "lan":
            norm_host = "0.0.0.0"
        elif norm_host.lower() == "local":
            norm_host = "127.0.0.1"
        if not validate_bind_host(norm_host):
            return ConfigStatus.FAILED, f"Configuration rejected: Invalid bind host: '{host}'"
        target_host = norm_host

    target_port = None
    if port is not None:
        norm_port = str(port).strip()
        if not validate_bind_port(norm_port):
            return ConfigStatus.FAILED, f"Configuration rejected: Invalid bind port: '{port}'"
        target_port = norm_port

    target_trust_proxies = None
    if trust_proxies is not None:
        if isinstance(trust_proxies, bool):
            target_trust_proxies = "true" if trust_proxies else "false"
        else:
            norm_tp = str(trust_proxies).strip().lower()
            if norm_tp in ("true", "1", "yes", "enable", "enabled"):
                target_trust_proxies = "true"
            elif norm_tp in ("false", "0", "no", "disable", "disabled"):
                target_trust_proxies = "false"
            else:
                return ConfigStatus.FAILED, f"Configuration rejected: Invalid trust_proxies value: '{trust_proxies}' (use true or false)"

    target_trusted_proxies = None
    if trusted_proxies is not None:
        norm_tps = str(trusted_proxies).strip()
        if not validate_trusted_proxies(norm_tps):
            return ConfigStatus.FAILED, f"Configuration rejected: Invalid trusted_proxies value: '{trusted_proxies}'"
        target_trusted_proxies = ",".join(p.strip() for p in norm_tps.split(",") if p.strip())

    target_proxy_host = None
    if proxy_host is not None:
        norm_ph = str(proxy_host).strip()
        if norm_ph.lower() in ("", "none", "clear", "disable", "disabled", "false"):
            target_proxy_host = ""
        else:
            if not validate_proxy_host(norm_ph):
                return ConfigStatus.FAILED, f"Configuration rejected: Invalid proxy domain / hostname: '{proxy_host}'"
            target_proxy_host = norm_ph

    # Check for EnvironmentFile in unit
    env_file_path = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("EnvironmentFile="):
            raw_path = stripped.split("EnvironmentFile=", 1)[1].strip().strip('"').lstrip('-').strip()
            if raw_path:
                p = Path(raw_path)
                if not p.is_absolute():
                    p = file_path.parent / p
                env_file_path = p
                break

    any_changed = False

    # If EnvironmentFile exists, update it
    if env_file_path and env_file_path.is_file():
        try:
            env_content = env_file_path.read_text(encoding="utf-8")
        except Exception as e:
            return ConfigStatus.FAILED, f"Could not read EnvironmentFile '{env_file_path}': {e}"

        env_updates = {}
        if target_host is not None:
            env_updates["MP3METAFIX_HOST"] = target_host
        if target_port is not None:
            env_updates["MP3METAFIX_PORT"] = target_port
        if target_trust_proxies is not None:
            env_updates["MP3METAFIX_TRUST_PROXIES"] = target_trust_proxies
        if target_trusted_proxies is not None:
            env_updates["MP3METAFIX_TRUSTED_PROXIES"] = target_trusted_proxies
        if target_proxy_host is not None:
            env_updates["MP3METAFIX_PROXY_HOST"] = target_proxy_host

        new_env_content = render_env_file(env_content, env_updates)
        if new_env_content != env_content:
            # Atomic write to env file
            temp_fd = None
            temp_path = None
            try:
                orig_stat = env_file_path.stat()
                temp_fd, temp_path_str = tempfile.mkstemp(dir=env_file_path.parent, prefix=f".{env_file_path.name}.tmp.")
                temp_path = Path(temp_path_str)
                with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                    temp_fd = None
                    f.write(new_env_content)
                os.chmod(temp_path, orig_stat.st_mode)
                try:
                    os.chown(temp_path, orig_stat.st_uid, orig_stat.st_gid)
                except (PermissionError, AttributeError):
                    pass
                os.replace(temp_path, env_file_path)
                temp_path = None
                any_changed = True
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

        # If unit also has inline Environment= lines for MP3METAFIX_, update them too
        if "MP3METAFIX_" in content:
            new_content, status, err_msg = set_service_binding(
                content,
                host=target_host,
                port=target_port,
                trust_proxies=target_trust_proxies,
                trusted_proxies=target_trusted_proxies,
                proxy_host=target_proxy_host,
            )
            if status == ConfigStatus.CHANGED:
                # write unit file atomically
                temp_fd = None
                temp_path = None
                try:
                    orig_stat = file_path.stat()
                    temp_fd, temp_path_str = tempfile.mkstemp(dir=file_path.parent, prefix=f".{file_path.name}.tmp.")
                    temp_path = Path(temp_path_str)
                    with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                        temp_fd = None
                        f.write(new_content)
                    os.chmod(temp_path, orig_stat.st_mode)
                    try:
                        os.chown(temp_path, orig_stat.st_uid, orig_stat.st_gid)
                    except (PermissionError, AttributeError):
                        pass
                    os.replace(temp_path, file_path)
                    temp_path = None
                    any_changed = True
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

        if any_changed:
            return ConfigStatus.CHANGED, f"Updated network configuration in '{env_file_path}'."
        else:
            return ConfigStatus.UNCHANGED, f"Service '{file_path}' is already configured as requested."

    # Otherwise, standard unit file update
    new_content, status, err_msg = set_service_binding(
        content,
        host=target_host,
        port=target_port,
        trust_proxies=target_trust_proxies,
        trusted_proxies=target_trusted_proxies,
        proxy_host=target_proxy_host,
    )
    if status == ConfigStatus.FAILED:
        return ConfigStatus.FAILED, f"Configuration rejected: {err_msg}"

    if status == ConfigStatus.UNCHANGED:
        return ConfigStatus.UNCHANGED, f"Service '{file_path}' is already configured as requested."

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

        os.chmod(temp_path, orig_stat.st_mode)
        try:
            os.chown(temp_path, orig_stat.st_uid, orig_stat.st_gid)
        except (PermissionError, AttributeError):
            pass

        os.replace(temp_path, file_path)
        temp_path = None
        return ConfigStatus.CHANGED, f"Updated network configuration in '{file_path}'."
    except Exception as e:
        return ConfigStatus.FAILED, f"Failed writing '{file_path}': {e}"
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
        print("Usage: configure_access.py <get|set> <path_to_service_file> [--host HOST] [--port PORT] [--trust-proxies true|false] [--trusted-proxies IPS] [--domain DOMAIN]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "get":
        if len(sys.argv) < 3:
            print("Usage: configure_access.py get <path_to_service_file>", file=sys.stderr)
            sys.exit(1)
        target_path = Path(sys.argv[2])
        if not target_path.is_file():
            print(f"[!] Error: File '{target_path}' not found.", file=sys.stderr)
            sys.exit(1)
        try:
            content = target_path.read_text(encoding="utf-8")
            info = get_service_binding(content, target_path)
            print(json.dumps(info))
            sys.exit(0)
        except Exception as e:
            print(f"[!] Error reading file: {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "set":
        if len(sys.argv) < 3:
            print("Usage: configure_access.py set <path_to_service_file> [--host HOST] [--port PORT] [--trust-proxies true|false] [--trusted-proxies IPS] [--domain DOMAIN]", file=sys.stderr)
            sys.exit(1)
        target_path = Path(sys.argv[2])

        target_host = None
        target_port = None
        target_trust = None
        target_trusted = None
        target_domain = None
        idx = 3
        while idx < len(sys.argv):
            arg = sys.argv[idx]
            if arg == "--host" and idx + 1 < len(sys.argv):
                target_host = sys.argv[idx + 1]
                idx += 2
            elif arg.startswith("--host="):
                target_host = arg.split("=", 1)[1]
                idx += 1
            elif arg == "--port" and idx + 1 < len(sys.argv):
                target_port = sys.argv[idx + 1]
                idx += 2
            elif arg.startswith("--port="):
                target_port = arg.split("=", 1)[1]
                idx += 1
            elif arg in ("--trust-proxies", "--trust-proxy") and idx + 1 < len(sys.argv):
                target_trust = sys.argv[idx + 1]
                idx += 2
            elif arg.startswith("--trust-proxies=") or arg.startswith("--trust-proxy="):
                target_trust = arg.split("=", 1)[1]
                idx += 1
            elif arg in ("--trusted-proxies", "--trusted-proxy") and idx + 1 < len(sys.argv):
                target_trusted = sys.argv[idx + 1]
                idx += 2
            elif arg.startswith("--trusted-proxies=") or arg.startswith("--trusted-proxy="):
                target_trusted = arg.split("=", 1)[1]
                idx += 1
            elif arg in ("--domain", "--proxy-host", "--url", "--public-url") and idx + 1 < len(sys.argv):
                target_domain = sys.argv[idx + 1]
                idx += 2
            elif arg.startswith("--domain=") or arg.startswith("--proxy-host=") or arg.startswith("--url=") or arg.startswith("--public-url="):
                target_domain = arg.split("=", 1)[1]
                idx += 1
            elif arg in ("--no-domain", "--clear-domain", "--no-proxy-host"):
                target_domain = ""
                idx += 1
            else:
                print(f"[!] Error: Unknown argument '{arg}'", file=sys.stderr)
                sys.exit(1)

        if target_host is None and target_port is None and target_trust is None and target_trusted is None and target_domain is None:
            print("[!] Error: At least one parameter (--host, --port, --trust-proxies, --trusted-proxies, --domain) must be specified for 'set'.", file=sys.stderr)
            sys.exit(1)

        status, msg = update_service_file(
            target_path,
            host=target_host,
            port=target_port,
            trust_proxies=target_trust,
            trusted_proxies=target_trusted,
            proxy_host=target_domain,
        )
        if status == ConfigStatus.CHANGED:
            print(f"[+] {msg}")
            sys.exit(0)
        elif status == ConfigStatus.UNCHANGED:
            print(f"[*] {msg}")
            sys.exit(2)
        else:
            print(f"[!] Error: {msg}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unknown command: '{cmd}'. Supported commands: get, set", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
