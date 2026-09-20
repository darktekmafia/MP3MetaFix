# Changelog

All notable changes to **MP3MetaFix** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-09-19

### Added
- **Multi-Interface Architecture & Gateway Hub (`/`)**:
  - Implemented a unified Gateway Hub and system dashboard served at `/` allowing users to choose between the focused mobile-first editor (**MP3MetaFix**) and the desktop power-user workspace (**MP3MetaManager**).
  - Dedicated sub-application static routing: `/app` for MP3MetaFix and `/manager` for MP3MetaManager.
  - Interactive **Top Navigation App Switcher** pill embedded across application headers for instant switching between workspaces and the hub without losing context.
- **Live System Telemetry & Diagnostic Endpoint (`GET /api/system/stats`)**:
  - Added real-time non-sensitive telemetry endpoint returning CPU load averages (`1m`, `5m`, `15m`, cores count), RAM memory capacity & utilization, host disk storage, and MP3 temporary cache quota usage.
  - Interactive live-updating telemetry cards on the Gateway Hub with auto-refresh intervals and smooth animated meters.
  - High-density mobile telemetry quickbar formatted as a 2x2 micro-card grid with smooth-scroll jump to full system diagnostics.
- **MP3MetaManager Desktop Workspace Foundation (`/manager`)**:
  - Built desktop workspace shell with responsive sidebar categories (Batch Queue, Suno AI Stems, Synced Lyrics SYLT, Karaoke Tap-to-Sync) and top-level search and batch action toolbars.
- **Mobile-First Responsive Design & Viewport Density Overhaul**:
  - Optimized layouts for all screen sizes from 320px narrow phones (iPhone SE) up to 4K widescreen displays.
  - Eliminated horizontal overflow and right-edge clipping on narrow mobile viewports by enforcing `box-sizing: border-box`, `minmax(0, 1fr)` grid track floors, and text truncation on long file names and audio specs.
  - Added balanced 2-column workspace layout on tablets ($\ge 720\text{px}$, e.g. iPad Mini) to prevent oversized album artwork scaling.
  - Added responsive `.btn-txt-full` / `.btn-txt-short` button label switching across toolbar actions (`New File` $\to$ `New`, `Save Changes` $\to$ `Save`, `Save & Download` $\to$ `Download`, `Upload Art` $\to$ `Upload`).
  - Added touch-scrolling and native scrollbar suppression on metadata form tab navigation.
- **Suno.com Metadata Extraction & Next.js Stream Deserializer (`POST /api/suno/extract` & `POST /api/suno/apply-artwork`)**:
  - Implemented server-side metadata extraction supporting Suno song URLs, share links, and clip UUIDs (`backend/suno_extractor.py`).
  - Deserializes Next.js Server Component streaming payloads to extract Track Title, Creator Name & Handle (`@username`), Musical Style (`TCON`), Structured Lyrics with structural section markers (`USLT`), High-Resolution 1024×1024 Album Artwork (`APIC`), Release Year (`TDRC`), Model Version (`COMM`), and formatted attribution comments.
  - Implemented SSRF-protected server-side artwork attachment (`/api/suno/apply-artwork`) with domain whitelisting and Pillow decompression bomb defense.
- **Embedded Suno Tag Auto-Detection & Interactive Selective Merge Modal**:
  - Added automatic Suno Clip UUID detection from uploaded MP3 comment strings (`made with suno; ... id=...`).
  - Added mobile-first, non-disruptive detection micro-pill (`✨ Suno Detected`) in the track info bar.
  - Implemented interactive side-by-side selective merge modal with per-field selection checkboxes, `Apply Selected`, `Fill Blank Only`, and `Apply All` presets enforcing the non-destructive data rule (zero blind overwrites).
- **Workspace Engineering Standards & Rule Hardening (`AGENTS.md`)**:
  - Added **Rule 8 (Mobile-First Responsive Design for `/app`)** mandating unobtrusive prompts, zero-overflow track flooring, and compact bottom sheets.
  - Added **Rule 9 (User Choice & Non-Destructive Data Merging)** guaranteeing user per-field control over metadata overwrites.
- **Full System Authentication & Access Control Subsystem (`backend/auth.py`, `frontend/js/auth.js`)**:
  - Implemented default protected mode requiring authentication across Gateway Hub (`/`), Desktop MetaManager (`/manager`), System Telemetry (`/api/system/stats`), and all file-editing APIs.
  - **First-Run Administrator Setup Wizard**: Automated modal wizard displayed on initial visit prompting the owner to configure the primary administrator account (`POST /api/auth/setup`).
  - **Cryptographic Security & Zero-Dependency Password Hashing**: Utilized standard-library `hashlib.pbkdf2_hmac` (600,000 rounds of HMAC-SHA256 with unique 16-byte cryptographically secure salts) and timing-attack-resistant `secrets.compare_digest`.
  - **Distinct Cookie Trust Boundaries**: Separated account authentication (`mp3metafix_auth` signed token `{user_id}.{timestamp}.{sig}`) from temporary file session storage (`mp3metafix_session` token `{session_id}.{timestamp}.{sig}`) in strict compliance with project architecture rules.
  - **Brute-Force Login Rate Limiting**: Built `LoginRateLimiter` enforcing a 5-attempt sliding window per IP with automatic 5-minute cooldown.
  - **Configurable Guest Mode Policy**: Administrator-toggled Guest Mode in Settings allowing unauthenticated visitors to use the mobile-first single-track tagger at `/app` while restricting the Gateway Hub, live telemetry diagnostics, batch workspace, and server settings to logged-in administrators.
  - **In-App Settings & Quota Management**: Dedicated Settings modal supporting password changes, guest mode toggling, active session concurrency limits, and temporary storage disk quotas with persistent storage in POSIX `0700`/`0600` `data/auth/`.
  - **Header Account Pill & Dropdown**: Glassmorphic user pill with user initials avatar, role badge (`Admin`/`Guest`), and interactive dropdown menu for settings and sign out.
- **Seamless Session Restoration & Memory Retention (`GET /api/session`)**:
  - Implemented session handshake endpoint (`GET /api/session`) checking the secure `HttpOnly` cookie against active storage sessions on disk.
  - Automatically restores open file sessions on page reload or when mobile operating systems reclaim browser memory during app switching, mounting the audio waveform and populating metadata fields without showing the dropzone.

---

## [0.3.4] - 2026-09-19

### Added
- **Service Access, LAN & Reverse Proxy Maintenance Commands (`--access`, `--lan`, `--local`, `--bind`, `--proxy`, `--domain`, `--no-domain`, `--no-proxy`, `--trusted-proxies`)**:
  - Added dedicated maintenance options in `install.sh` to inspect current host/port binding, reverse proxy trust settings, configured public/proxy domain, service health status, and detected LAN access URLs without requiring manual systemd unit editing.
  - Added quick switching commands `--lan` (listen on all network interfaces `0.0.0.0`) and `--local` (restrict to localhost `127.0.0.1`) with automatic systemd daemon reloading, service restarting, and health verification.
  - Added proxy trust management flags `--proxy [IPS]`, `--no-proxy`, and `--trusted-proxies <IPS>` allowing users to enable or disable reverse proxy header handling and restrict trust to specific upstream proxy IPs (e.g. Nginx Proxy Manager) without changing the network bind address.
  - Added reverse proxy domain/hostname configuration (`--domain <DOMAIN/URL>` and `--no-domain`) with informational dual-probe health checks verifying both local interface availability and external proxy domain reachability (`https://${domain}/api/health`) without modifying server socket binding.
- **Service Configuration Helper (`scripts/configure_access.py`)**:
  - Implemented standalone Python helper for reading and updating `MP3METAFIX_HOST`, `MP3METAFIX_PORT`, `MP3METAFIX_TRUST_PROXIES`, `MP3METAFIX_TRUSTED_PROXIES`, and `MP3METAFIX_PROXY_HOST` inside systemd `[Service]` sections with strict host/port/CIDR/domain validation (preventing shell injection), atomic file replacement, and permission preservation.
- **Enhanced Post-Installation and Status Guidance**:
  - Updated post-installation summary output and `./install.sh --status` to display active URLs, reverse proxy trust status, proxy domain URLs, and clear instructions on enabling LAN access for headless and container environments.

### Fixed
- **HTTP HEAD Support on Health & Version Endpoints**:
  - Registered `/api/health` and `/api/version` endpoints with `methods=["GET", "HEAD"]` so `curl -I` requests, upstream reverse proxy health probes, and uptime monitors receive `HTTP 200 OK` rather than `HTTP 404 Not Found`.
- **Custom Bind IP Health Probes & Endpoint Reporting**:
  - Configured health check probes and active access URL reporting to dynamically target the configured interface when bound to a specific IP (e.g. `--bind 192.168.0.190`), eliminating false timeout warnings caused by probing localhost on dedicated interface binds.
- **Help Flag Aliases**:
  - Accepted `--h` and `-help` as aliases for `--help` / `-h` in `install.sh`.

### Documentation & Lifecycle
- **Hairpin NAT (NAT Loopback) & Local Health Probe Guidance**:
  - Documented in `docs/DEPLOYMENT.md` why local container health probes from within homelab/LAN networks may report external proxy domains as unreachable due to lack of router Hairpin NAT reflection while external client connectivity is unaffected.
  - Added contextual diagnostic hints referencing `docs/DEPLOYMENT.md` to `install.sh --access` and `install.sh --domain`.
- **Pre-Handoff vs Handoff-Capable Update Transition Semantics**:
  - Clarified that systems upgrading from legacy pre-migration versions (`v0.3.0` & `v0.3.1`) require a second `./install.sh --update` invocation (or manual migration execution) because the legacy in-memory script completes its old flow before on-disk code is refreshed.
  - Noted that `v0.3.2` introduced `scripts/migrate_service.py` directly, and releases `v0.3.3+` feature the in-place process handoff (`exec bash`) for automated single-invocation migration during updates.
- **Cache-Busting Asset Refresh**:
  - Bumped static asset query strings (`?v=0.3.4`) across `frontend/index.html`.

---

## [0.3.3] - 2026-09-19

### Fixed
- **Self-Updating Installer Seamless Re-Execution**:
  - Implemented automatic shell re-execution (`exec bash "${INSTALL_DIR}/install.sh" "${ORIG_ARGS[@]}"`) upon pulling new commits in `install.sh --update`.
  - Ensures the newly fetched installer and migration logic executes immediately during the very first update run without requiring a second manual update.
  - Added loop protection via commit hash differential checks and `_MP3METAFIX_REEXEC=1` environment guard.
- **Cache-Busting Asset Refresh**:
  - Bumped static asset query strings (`?v=0.3.3`) across `frontend/index.html`.

---

## [0.3.2] - 2026-09-19

### Fixed
- **Automated & Portable Systemd Service Unit Migration (`scripts/migrate_service.py`)**:
  - Added dedicated migration helper invoked automatically during `install.sh --update` to detect legacy Uvicorn launch commands and update `ExecStart` with `--no-proxy-headers` and `$MP3METAFIX_HOST` / `$MP3METAFIX_PORT`.
  - Strictly limits parsing and modifications to the `[Service]` section while preserving all administrator customizations, custom environment variables, worker counts, sandboxing flags, and cgroup resource limits (`MemoryMax`, `TasksMax`, `CPUQuota`).
  - Synthesizes missing `MP3METAFIX_HOST` and `MP3METAFIX_PORT` environment variables into `[Service]` when migrating legacy units that hardcoded IP/port CLI flags.
  - Safely and explicitly rejects unparseable, shell-wrapped, compound, or invalid `ExecStart` commands, leaving units untouched.
  - Employs secure atomic file replacement preserving original file permissions (`stat.S_IMODE`) and ownership.
- **Installer Error Propagation & Synchronous Service Restart**:
  - Configured `install.sh --update` to handle tri-state migration outcomes (changed, unchanged, failed) and execute synchronous `daemon-reload` and service restart verification, preventing false success reports on failures.
- **Installer Stale Version Reporting Fix**:
  - Re-reads `VERSION` from disk immediately following `git pull` in `install.sh` to ensure the post-update status banner accurately reflects the newly pulled release.
- **Cache-Busting Asset Refresh**:
  - Bumped static asset query strings (`?v=0.3.2`) across `frontend/index.html` to guarantee fresh script and style caching.

---

## [0.3.1] - 2026-09-19

### Fixed
- **Uvicorn Default Proxy-Headers Bypass Prevention**:
  - Added `--no-proxy-headers` to all server launch commands (`deploy/mp3metafix.service`, `install.sh`, `run.sh`), eliminating Uvicorn CLI's implicit proxy middleware that bypassed `TRUST_PROXIES=false`.
- **Dynamic Systemd Host & Port Binding**:
  - Configured `deploy/mp3metafix.service` and `install.sh` systemd service templates to use `$MP3METAFIX_HOST` and `$MP3METAFIX_PORT` variables in `ExecStart=`, allowing users to control bind interfaces (e.g. `0.0.0.0`) via environment variables.
- **Strict Cookie Scheme Resolution**:
  - Replaced direct `X-Forwarded-Proto` header parsing in cookie handling with trusted `request.url.scheme` validation, ensuring `Secure` flags are set only over verified TLS connections.
- **Cache-Busting Asset Refresh**:
  - Bumped static asset query strings (`?v=0.3.1`) across `frontend/index.html` to guarantee fresh script and style caching through Cloudflare and browser caches.

---

## [0.3.0] - 2026-09-19

### Security & Hardening
- **Configurable Trusted Proxy Whitelist & Anti-Spoofing**:
  - Replaced wildcard proxy trust with `MP3METAFIX_TRUSTED_PROXIES` allowlist (defaulting strictly to loopback `127.0.0.1, ::1`). Direct connections from untrusted LAN peers have `X-Forwarded-For` and `X-Forwarded-Proto` headers ignored.
- **Strict 3-Part Session Token Enforcement**:
  - Removed non-expiring 2-part legacy token fallback in `verify_signed_session_token`, strictly enforcing 3-part `{uuid}.{timestamp}.{sig}` tokens with active TTL verification. Active legacy sessions are invalidated and require re-upload.
- **Standards-Compliant Single Audio Byte-Range Handling**:
  - Implemented single byte-range validation in `/api/stream` supporting normal, open-ended, and suffix ranges, returning HTTP `416 Range Not Satisfiable` with `Content-Range: bytes */{size}` for inverted, out-of-bounds, empty file, or unsupported multi-range requests.
- **Artwork Error Sanitization**:
  - Replaced dynamic exception string interpolation with fixed, sanitized client error messages in `backend/security.py` and restricted server logging to non-sensitive exception class names (`type(e).__name__`).
- **Disabled In-App Update Installation Endpoint**:
  - Disabled `POST /api/updates/apply` (returning HTTP 403 Forbidden) pending dedicated administrative authorization, cross-process concurrency locking, and unprivileged service restart design.
- **Cache-Busting Asset Queries**:
  - Bumped asset cache-busting query strings (`?v=0.3.0`) in `frontend/index.html` to eliminate stale Cloudflare/browser caching.

---

## [0.2.2] - 2026-09-19

### Added
- **Configurable Canned Comments & Quick Presets**:
  - Added a quick-preset selector dropdown on the **Lyrics & Notes** tab allowing one-click comment prefilling (such as Suno creator profile URLs, AI generation tags, mastering notes, and copyright attribution).
  - Added quick **"Save Current"** action allowing users to instantly save whatever text is typed into the Comment field as a new reusable preset.
  - Added **Canned Comments & Presets Manager** modal to add, view, edit inline, delete, and restore default presets with live item counters.
  - Presets are persisted in `localStorage` (`mp3metafix_canned_comments`) with safe DOM node creation and strict XSS sanitization (`textContent`).
  - Bumped asset cache-busting queries (`?v=0.2.2`) across `index.html`.

---

## [0.2.1] - 2026-09-19

### Fixed
- **Frontend Element Null-Safety & Cache Desynchronization Resilience**:
  - Added comprehensive null-safety checks and guards across all DOM event listeners, modal handlers, and the `WaveformVisualizer` component to prevent unhandled TypeError exceptions during stale CDN cache transitions.
  - Bumped asset cache-busting queries (`?v=0.2.1`) to ensure instant client-side asset freshness across browsers and Cloudflare proxy caching.

---

## [0.2.0] - 2026-09-19

### Added
- **Comprehensive 14-Point Security Hardening & Threat Defense Engine**:
  - **Timestamped Cryptographic HMAC Session Tokens**: Tokens now cryptographically encode session UUID, issuance UNIX timestamp, and HMAC-SHA256 signature (`{uuid}.{timestamp}.{signature}`) with strict time-to-live enforcement to prevent replay attacks.
  - **Zero-Knowledge Decoupled Storage**: Session storage directories on disk are isolated and named with one-way deterministic SHA-256 hashes (`SHA-256(secret:uuid)[:32]`).
  - **POSIX 0700 Multi-Tenant VPS File Isolation**: All temporary storage and session directories are created with strict `0700` (`rwx------`) permissions to prevent unauthorized access by unprivileged users in shared environments.
  - **DOM XSS Sanitization**: Replaced `innerHTML` insertions with safe DOM node construction and `textContent` across all UI elements, dynamic toasts, and metadata badges.
  - **Pillow Decompression Bomb Defense**: Protected server memory against pixel flood and decompression attacks by capping `Image.MAX_IMAGE_PIXELS = 10,000,000` and enforcing 4096×4096px bounds.
  - **Global Storage Quota & LRU Pruning**: Added `MAX_GLOBAL_TEMP_STORAGE_MB` (default 2GB) quota enforcement with automatic Least Recently Used (LRU) session eviction to prevent disk exhaustion DoS.
  - **CSRF Protection Middleware**: Implemented `CSRFProtectionMiddleware` inspecting `Sec-Fetch-Site` and `Origin` headers on mutating requests (`POST`, `PUT`, `DELETE`, `PATCH`).
  - **In-Memory Upload Rate Limiter with Anti-Spoofing**: Enforced a sliding-window rate limit (25 uploads / 60s per client IP) with trusted proxy CIDR verification to prevent `X-Forwarded-For` spoofing, coupled with automatic LRU tracking cleanup.
  - **Pydantic Payload Length Constraints**: Enforced strict input bounds on metadata fields (Title/Artist: 500 chars, Lyrics: 64KB, Numbers: 50 chars, Comments: 10KB) to prevent payload memory inflation.
  - **Internal Filesystem Path & Traceback Exception Masking**: Sanitized API error outputs, logging detailed stack traces server-side while presenting clean, non-disclosing error messages to clients.
  - **MIME Confusion & CSP Protections**: Enforced strict `Content-Type: audio/mpeg`, `X-Content-Type-Options: nosniff`, and restrictive Content Security Policies.
  - **CORS Credential Isolation**: Replaced wildcard CORS headers with regex-validated origin matching (`localhost`, `127.0.0.1`, and explicit origins).
  - **Localhost Default Host Binding & Dynamic TLS Secure Cookies**: Defaulted network binding to `127.0.0.1` and dynamically set `Secure` cookie attributes when accessed over HTTPS.
  - **Systemd Resource Limits**: Added `MemoryMax=512M`, `TasksMax=64`, and `CPUQuota=80%` sandboxing in `mp3metafix.service`.
- **Interactive Audio Waveform Visualizer & Peak Scrubber**:
  - **Native Web Audio API & HTML5 Canvas Rendering**: Decodes audio streams on-the-fly and computes 400 normalized RMS/peak amplitude points rendered on a Retina-sharp canvas (`window.devicePixelRatio`).
  - **Dynamic Multi-Color Gradient State**: Highlights played audio with a vibrant cyan-to-purple gradient (`#38bdf8` -> `#a855f7`) while preserving sleek slate/indigo bars for upcoming audio.
  - **Interactive Scrubbing & Hover Time Tooltip**: Click-to-seek, click-and-drag continuous scrubbing with pointer capture, hover vertical guide line, and glassmorphic floating timestamp tooltip.
  - **Fluid 60fps Playhead Tracking & Accessibility**: Real-time `requestAnimationFrame` loop synchronization, automatic container resize recalculation via `ResizeObserver`, and keyboard seeking controls (`ArrowLeft`, `ArrowRight`, `Home`, `End`, `Space`).
- **In-App Web Updater & Version Inspector**:
  - **Automated Update Checker**: Background check on page load and manual "Check for Updates" querying GitHub Releases/Tags API with 10-minute in-memory caching.
  - **Version Details Modal**: Detailed inspection card displaying active version, Git commit hash, active branch, runtime mode (Desktop vs Systemd service), and repository link.
  - **Release Notes & Changelog Viewer**: Formatted multi-line preview of latest release features and bug fixes before installation.
  - **Live Terminal Console & SSE Log Streaming**: Real-time monospace terminal modal streaming stdout/stderr of `install.sh --update --headless` via Server-Sent Events (`POST /api/updates/apply`).
  - **Auto-Reconnection & One-Click Reload**: Automatic client healthcheck polling (`/api/health`) as the service restarts, prompting users to reload once back online.
- **Security Hardening Documentation**:
  - Published comprehensive security guide in `docs/SECURITY_HARDENING.md` detailing threat models, attack vectors, and verification procedures.
- **Automated Test Suite Expansion**:
  - Expanded test coverage to 20 automated tests in `tests/test_backend.py` covering rate limiting, CSRF rejection, magic byte filtering, path traversal protection, timestamped HMAC verification, storage quota LRU eviction, error masking, semver comparison, and SSE log streaming.

### Fixed
- **Cloudflare Proxy & Analytics CSP Compatibility**: Added `https://static.cloudflareinsights.com` and `https://cloudflareinsights.com` to `script-src` and `connect-src` Content Security Policy directives.
- **Cross-Browser Button Theme Styling**: Added global CSS `button` reset to eliminate native browser `ButtonFace` background rendering on header settings and version badge buttons.
- **Client Cache Invalidation**: Added asset version query parameters (`styles.css?v=0.2.0`, `app.js?v=0.2.0`) to guarantee browser cache refreshing during upgrades.

---

## [0.1.1] - 2026-09-19

### Added
- **Cryptographic Session Cookies & Zero-Knowledge Storage Architecture**:
  - Implemented HMAC-SHA256 signed `HttpOnly`, `SameSite=Lax` session cookies (`mp3metafix_session`) to eliminate exposed UUIDs and raw identifiers from URLs and browser JavaScript.
  - Decoupled server filesystem storage: session directories on disk are now named using one-way deterministic SHA-256 hashes (`SHA-256(secret:uuid)[:32]`).
  - Refactored API routes to clean, session-bound endpoints (`/api/artwork`, `/api/save`, `/api/stream`, `/api/download/{filename}`, `/api/session`).
  - Added strict HMAC signature verification, rejecting tampered or forged session tokens with HTTP 401 Unauthorized.
- **File System Access API & Save Picker**:
  - Implemented the modern Web File System Access API (`window.showSaveFilePicker`), prompting users to choose the exact destination directory and filename upon saving.
  - Added seamless fallback to browser downloads when File System Access API is not supported or declined.
- **Named Download Route & Enhanced Content-Disposition**:
  - Added `/api/download/{filename:path}` endpoint to guarantee target filenames are directly reflected in the URL path for all browsers.
  - Formatted `Content-Disposition` with RFC 5987 UTF-8 encoding (`filename*=UTF-8''...`) along with sanitized ASCII fallback.
  - Exposed `Content-Disposition` header in CORS/Access-Control settings.
- **Universal Installer & Systemd Service**:
  - Configured `install.sh` to install and enable `mp3metafix.service` by default across both desktop and server environments so the application starts automatically on boot.
  - Added desktop wrapper auto-recovery to interact with the systemd service.
  - Added `--no-service` flag for standalone installations.
- **Documentation & Quickstart Refactoring**:
  - Restructured `README.md` and `docs/DEPLOYMENT.md` to prioritize background service installation and management (`systemctl status/restart/stop`) over manual `run.sh` foreground execution.

---

## [0.1.0] - 2026-09-19

### Added
- **Core Backend Engine**:
  - FastAPI server with async file streaming and upload validation.
  - Mutagen ID3 metadata processing supporting ID3v2.4 and ID3v2.3 tags.
  - Complete APIC album art extraction, replacement, normalization, and removal via Pillow.
  - Audio stream properties inspection (duration, bitrate, sample rate, channels).
- **Security & Storage**:
  - Magic byte and MIME type validation for MP3 and image files.
  - Path traversal protection with client filename sanitization.
  - Isolated UUID4 temporary session management.
  - Automated background TTL cleanup worker for expired upload sessions.
  - Security headers middleware (CSP, HSTS, X-Content-Type-Options, X-Frame-Options).
  - Reverse proxy header forwarding compatibility (`ProxyHeadersMiddleware`).
- **Web User Interface**:
  - Glassmorphism dark mode UI using modern vanilla CSS.
  - Drag-and-drop MP3 uploader with live upload progress.
  - Dual-panel metadata editor with categorized tabs.
  - Interactive cover art manager (drag-and-drop cover replacement, download, remove).
  - Built-in HTML5 audio preview player with scrubber, seek, volume, and time tracking.
  - Live dynamic filename generator supporting patterns (`%artist% - %title%.mp3`, `%track%`, etc.).
  - Keyboard shortcuts (<kbd>Ctrl</kbd> + <kbd>S</kbd> to save and download).
- **Installer & Deployment**:
  - Universal smart Linux installer & updater `install.sh` supporting Fedora, Ubuntu, Debian, Arch, openSUSE.
  - Automatic environment detection for Desktop (application launcher and icons) vs Headless LXC (systemd service).
  - Production reverse proxy templates for Nginx and Caddy.
  - Systemd service template with security hardening directives.
  - Complete documentation suite (`README.md`, `ARCHITECTURE.md`, `DEPLOYMENT.md`).
