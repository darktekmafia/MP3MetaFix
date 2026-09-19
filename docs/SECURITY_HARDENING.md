# MP3MetaFix Security Hardening & Threat Defense Guide

This document logs the threat model, attack surface analysis, vulnerability vectors, and defense implementations for MP3MetaFix.

---

## 🛡️ Threat Model & Defense Checklist

- [x] **1. DOM XSS Sanitization in Frontend**: Prevent HTML/script injection via untrusted metadata/tags/filenames in UI notifications. *(Implemented in `frontend/js/app.js`)*
- [x] **2. Image Decompression Bomb Defense**: Protect Python Pillow image processing against memory exhaustion and pixel flood attacks. *(Implemented in `backend/security.py`)*
- [x] **3. Global Storage Quota & Temp Protection**: Prevent disk exhaustion DoS attacks by capping maximum temporary storage size and auto-pruning. *(Implemented in `backend/storage.py` and `backend/config.py`)*
- [x] **4. Origin & Sec-Fetch-Site CSRF Defense**: Block cross-site request forgery and unauthorized external state-changing requests. *(Implemented in `backend/security.py` & `backend/main.py`)*
- [x] **5. In-Memory Upload Rate Limiting**: Limit rapid-fire upload bursts per client IP. *(Implemented in `backend/security.py` & `backend/main.py`)*
- [x] **6. Systemd Process Resource Sandboxing**: Bound memory, CPU, and process execution in system service units. *(Implemented in `deploy/mp3metafix.service` & `install.sh`)*
- [x] **7. Pydantic Model Payload Length Bounds**: Prevent memory/CPU inflation DoS via oversized metadata strings. *(Implemented in `backend/metadata_engine.py`)*
- [x] **8. Rate Limiter Anti-Spoofing & Memory Leak Defense**: Validate proxy trust subnets and auto-prune stale IP dictionaries. *(Implemented in `backend/security.py`)*
- [x] **9. Internal Filesystem Path Exception Masking**: Prevent directory structure and OS user disclosure in HTTP error responses. *(Implemented in `backend/main.py`)*
- [x] **10. MIME Confusion & CSP Content Protections**: Restrict execution contexts on media streams and downloads. *(Implemented in `backend/security.py` & `backend/main.py`)*
- [x] **11. Time-Bounded HMAC Session Tokens**: Cryptographically embed and verify UNIX timestamps in session cookies to prevent perpetual replay. *(Implemented in `backend/security.py`)*
- [x] **12. Multi-User POSIX File Isolation (0700 Permissions)**: Prevent unprivileged local Linux users on multi-tenant VPS from snooping session directories. *(Implemented in `backend/storage.py`)*
- [x] **13. CORS Credential Isolation**: Restrict allowed CORS origins via regex to prohibit credential leakage to wildcard domains. *(Implemented in `backend/main.py`)*
- [x] **14. Localhost Default Host Binding & TLS Secure Cookies**: Bind to `127.0.0.1` by default and dynamically set `Secure` cookie flag over TLS/HTTPS. *(Implemented in `backend/config.py` & `backend/main.py`)*
- [x] **15. Single Audio Byte-Range Bounds & Underflow Defense**: Enforce strict single-range HTTP 206 validation. *(Implemented in `backend/main.py`)*
- [x] **16. Artwork Processing Error Sanitization**: Sanitize client-facing image decode errors and server log formats. *(Implemented in `backend/security.py`)*
- [ ] **17. Update Installation Endpoint Access Control**: In-app updater endpoint disabled pending admin design. *(Implemented in `backend/main.py`)*
- [x] **18. Safe Systemd Unit Parser Hardening & Migration**: Atomic in-place unit updates with POSIX quoting and shell injection defense. *(Implemented in `scripts/migrate_service.py` & `install.sh`)*

---

## 🔍 Detailed Vector Analysis & Implementations

### Vector 1: DOM XSS via Metadata / Toasts [COMPLETED]
- **Threat**: Untrusted metadata strings containing script or image error payloads (`<img src=x onerror=...>`) rendered via `innerHTML`.
- **Defense**: Replaced `innerHTML` with safe DOM node construction and `textContent` for dynamic text nodes in `frontend/js/app.js`.

### Vector 2: Image Decompression Bomb & Memory Exhaustion [COMPLETED]
- **Threat**: Highly compressed images (PNG/WebP) expanding to multi-gigabyte uncompressed bitmaps upon decompression, triggering OOM kernel kills (`SIGKILL`).
- **Defense**: Configured `Image.MAX_IMAGE_PIXELS = 10_000_000` (max 10 MP) and enforced dimension boundary checks (max 4096x4096px) in `backend/security.py`.

### Vector 3: Storage & Inode Exhaustion (Disk DoS) [COMPLETED]
- **Threat**: Rapid-fire uploads of 150MB audio files filling the entire host filesystem partition before TTL cleanup triggers.
- **Defense**: Enforced `MAX_GLOBAL_TEMP_STORAGE_MB = 2048` (2GB default) with automatic LRU session pruning when storage approaches capacity limits in `backend/storage.py`.

### Vector 4: Cross-Site Request Forgery (CSRF) [COMPLETED]
- **Threat**: Malicious third-party web pages issuing forged state-changing API requests (`POST /api/save`, `DELETE /api/session`) using browser session cookies.
- **Defense**: Implemented `CSRFProtectionMiddleware` blocking `Sec-Fetch-Site: cross-site` and untrusted `Origin` headers on mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`).

### Vector 5: Upload Flood Rate Limiting [COMPLETED]
- **Threat**: Automated bots exhausting network bandwidth and worker processing queues.
- **Defense**: Implemented `InMemoryRateLimiter` sliding-window rate limiter (25 uploads / 60 seconds per client IP) on `POST /api/upload`.

### Vector 6: Systemd Process Sandboxing [COMPLETED]
- **Threat**: Memory leaks or CPU exhaustion affecting host system stability.
- **Defense**: Added systemd cgroup limits: `MemoryMax=512M`, `TasksMax=64`, `CPUQuota=80%` in `deploy/mp3metafix.service` and `install.sh`.

### Vector 7: Metadata Payload Inflation & Buffer Exhaustion [COMPLETED]
- **Threat**: An attacker with a valid session transmits multi-megabyte JSON payloads in `POST /api/save` (e.g. 50MB strings in `lyrics` or `title`), forcing Mutagen and Python memory allocators to construct bloated ID3 frames and burn CPU.
- **Defense**: Configured strict `Field(max_length=...)` bounds in `backend/metadata_engine.py` (Title/Artist/Album: 500 chars, Lyrics: 64KB, Numbers: 50 chars, Comments: 10KB). Pydantic automatically rejects oversized payloads with HTTP 422 before processing.

### Vector 8: Rate Limiter Header Spoofing & Memory Growth [COMPLETED]
- **Threat**: Attackers bypassing IP rate limits by sending randomized `X-Forwarded-For` headers, or exhausting server heap memory by connecting once from millions of spoofed IPs to inflate the rate limiter tracking dictionary.
- **Defense**:
  1. Strict proxy verification in `backend/security.py`: `X-Forwarded-For` is only honored if the connecting socket peer matches explicitly configured `MP3METAFIX_TRUSTED_PROXIES` (defaulting strictly to loopback `127.0.0.1, ::1`). Direct connections from untrusted LAN IPs have forwarding headers ignored.
  2. Automatic periodic pruning (`_purge_stale`) in `InMemoryRateLimiter` to delete idle IPs and enforce `max_tracked_ips=5000` with LRU eviction.

### Vector 9: Internal Filesystem Path & Traceback Disclosures [COMPLETED]
- **Threat**: Triggering parsing or write exceptions to elicit raw Python tracebacks containing absolute server filesystem paths (`/run/media/...`, `/home/...`).
- **Defense**: Masked raw error strings in HTTP responses across `backend/main.py`. Errors are logged internally to server logs via `logger.exception(...)` while users receive generic, non-disclosing error descriptions.

### Vector 10: MIME Confusion & Content Isolation [COMPLETED]
- **Threat**: Crafting polyglot audio files containing HTML or script tags and tricking browsers into rendering them in an executable document context.
- **Defense**: Enforced `X-Content-Type-Options: nosniff`, strict `Content-Type: audio/mpeg`, attachment download semantics, and restrictive `Content-Security-Policy` headers across all endpoints.

### Vector 11: Timestamped Cryptographic Session Tokens [COMPLETED]
- **Threat**: Captured session cookies being replayed indefinitely across long periods or offline token manipulation attempts.
- **Defense**: Formatted tokens strictly as `{session_id}.{timestamp}.{HMAC_signature}` in `backend/security.py`. Server validates expiration against `SESSION_COOKIE_MAX_AGE` cryptographically before checking storage state. Non-expiring 2-part legacy tokens are completely rejected.

### Vector 12: Multi-Tenant VPS POSIX Permissions Isolation [COMPLETED]
- **Threat**: On a multi-user Linux VPS or shared host, unprivileged local system users viewing or tampering with temporary audio and image files in `data/temp/`.
- **Defense**: Enforced strict `0700` (`rwx------`) POSIX permissions on `TEMP_DIR` and each hashed session directory in `backend/storage.py`.

### Vector 13: CORS Credential Leakage Prevention [COMPLETED]
- **Threat**: Loose wildcard CORS settings (`allow_origins=["*"]`) combined with credentials allowing malicious external domains to trigger credentialed cross-origin read operations.
- **Defense**: Replaced wildcard origins with strict regex allowing only verified same-origin and localhost developers in `backend/main.py`.

### Vector 14: Default Localhost Binding & Dynamic TLS Cookie Flags [COMPLETED]
- **Threat**: Unintended exposure of unencrypted HTTP services on public network interfaces (`0.0.0.0`), and transmitting plain session cookies over unencrypted transport.
- **Defense**: Defaulted `MP3METAFIX_HOST` to `127.0.0.1` (requiring explicit override or reverse proxy) and dynamically attached the `Secure` cookie flag when serving via TLS/HTTPS.

### Vector 15: Single Audio Byte-Range Bounds & Underflow Defense [COMPLETED]
- **Threat**: Malformed, inverted (`bytes=500-200`), out-of-bounds, or unsupported multi-range headers triggering negative content lengths or undefined chunk behavior during audio streaming.
- **Defense**: Enforced single byte-range validation in `backend/main.py` supporting normal, open-ended, and suffix ranges, returning standard HTTP `416 Range Not Satisfiable` with `Content-Range: bytes */{size}` for unsatisfiable ranges or unsupported multi-range requests.

### Vector 16: Artwork Processing Error Sanitization [COMPLETED]
- **Threat**: Image processing errors exposing internal library state, raw byte sequences, or filesystem paths to clients or logs.
- **Defense**: Configured fixed, sanitized client error messages in `backend/security.py` and restricted server logging to non-sensitive exception class names (`type(e).__name__`).

### Vector 17: Update Installation Endpoint Access Control [PENDING ADMIN DESIGN]
- **Threat**: Unauthenticated visitors or non-admin users triggering server update scripts and restarts.
- **Defense**: In-app update execution endpoint (`POST /api/updates/apply`) is disabled (HTTP 403 Forbidden) pending dedicated administrative authentication, concurrency lock design, and service privilege review.

### Vector 18: Safe Systemd Service Migration & Unit Parser Hardening [COMPLETED]
- **Threat**: Automated update scripts clobbering administrator customizations, corrupting complex `ExecStart` commands with naive regex or whitespace string splitting, exposing temporary files to symlink race conditions, or altering unit file permission modes.
- **Defense**:
  1. Strict `[Service]` section parsing with POSIX `shlex` tokenization in `scripts/migrate_service.py`.
  2. Explicit rejection of compound shell commands, pipelines (`|`), redirects (`>`), subshells, or invalid quoting, leaving unparseable units untouched.
  3. Atomic file writes using unguessable directory-local temporary files (`mkstemp`) with explicit preservation of original POSIX file mode (`stat.S_IMODE`) and ownership (`os.chown`).
  4. Tri-state CLI exit codes (`0`=changed, `2`=unchanged, `1`=failed) ensuring the installer propagates failures and prevents reporting false successes.
  5. Process image replacement (`exec bash`) upon git updates guarded with commit hash checks and `_MP3METAFIX_REEXEC=1` environment variables to prevent infinite restart loops.



