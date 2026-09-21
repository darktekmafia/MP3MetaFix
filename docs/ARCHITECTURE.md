# MP3MetaFix Architecture & Security Specification

This document details the software architecture, metadata processing engine, security controls, and storage lifecycle of MP3MetaFix.

---

## 1. System Overview

MP3MetaFix is a client-server application with known security gaps:
- **Backend**: Python 3.14 + FastAPI + Starlette + Mutagen + Pillow.
- **Frontend**: Zero-framework Vanilla JS + CSS Glassmorphism Design System.
- **IPC / Transport**: RESTful JSON APIs and HTTP 206 Partial Content audio streaming.

---

## 2. Security Perimeter & Threat Model

The implemented boundaries and open gaps are documented in [SECURITY_HARDENING.md](SECURITY_HARDENING.md) and the [2026-09-20 audit](SECURITY_AUDIT_2026-09-20.md). They are not a completed security perimeter.

Actual upload processing is important: HTTP middleware handles proxy/CSRF/header behavior; FastAPI parses multipart data into temporary files **before** dependency authorization and endpoint upload rate/size checks. After access checks, the endpoint validates the initial audio signature, streams to hashed session storage with a byte counter, and parses the container. Earlier diagrams incorrectly implied authentication and size limits preceded all disk writes.

- Account identity and temporary file-session cookies are distinct HMAC-signed, expiring credentials. File cookies do not confer login/admin access. Account tokens currently survive password changes and logout.
- Session storage uses `SHA-256("storage_dir:" + secret + ":" + session_id)[:32]`, requesting 0700 directory permissions. File-session IDs are not returned in JSON; account IDs are returned by authentication APIs.
- A 10 MiB quota precheck and LRU/TTL cleanup manage session storage, but do not reserve the full upload or concurrent save copies. Runtime limits use environment configuration, not saved UI quota preferences.
- Safe DOM text rendering, CSRF checks, proxy trust, Pydantic field bounds, and generic normal-editing errors exist. Updater output masking remains incomplete.
- CSP allows inline scripts; nosniff cannot protect an endpoint explicitly serving untrusted HTML. Embedded artwork is currently such an endpoint. Uploaded images undergo Pillow normalization, but embedded artwork does not.
- HSTS is not emitted by the backend. HTTPS termination, proxy request limits, and installed service containment require separate configuration/verification.

---

## 3. Shared Metadata Engine & Mutagen ID3 Frames

MP3MetaFix maps high-level user fields to standard ID3v2.4 and ID3v2.3 tags:

| High-Level Field | ID3 Frame ID | Encoding | Description |
|-------------------|--------------|----------|-------------|
| Title | `TIT2` | UTF-8 | Track name |
| Artist | `TPE1` | UTF-8 | Lead performer |
| Album | `TALB` | UTF-8 | Album / Collection title |
| Album Artist | `TPE2` | UTF-8 | Band / Album artist |
| Composer | `TCOM` | UTF-8 | Song composer |
| Genre | `TCON` | UTF-8 | Music category / style |
| Year / Date | `TDRC` & `TYER` | UTF-8 | Recording timestamp / year |
| Track Number | `TRCK` | UTF-8 | Formatted as `track/total` |
| Disc Number | `TPOS` | UTF-8 | Formatted as `disc/total` |
| BPM | `TBPM` | UTF-8 | Beats per minute |
| Comment | `COMM` | UTF-8 | General notes |
| Lyrics | `USLT` | UTF-8 | Unsynchronized song lyrics |
| Cover Art | `APIC` | Binary | Embedded front cover image (`type=3`) |

### Shared audio formats (v0.5.0)

The shared backend uses `audio_formats.py`, `metadata_engine.py`, storage manager, and API routes. `/manager` must reuse these capabilities when its single-track inspector is implemented, rather than maintaining a separate format engine.

| Container | Metadata and artwork | MIME | Stored file |
|---|---|---|---|
| MP3 | ID3v2 text, USLT, APIC | `audio/mpeg` | `audio.mp3` |
| M4A (AAC/ALAC/Opus) | MP4 text atoms, trkn/disk tuples, tmpo, lyrics, covr JPEG/PNG | `audio/mp4` | `audio.m4a` |
| WAV | RIFF-embedded ID3; existing INFO text fallback and synchronization | `audio/wav` | `audio.wav` |

Owner-confirmed MP3, WAV, and real Suno Opus M4A editing. Synthetic AAC M4A tests also pass. The table describes implemented handlers, not complete compatibility certification for every encoder variant.

`audio_info` includes `format`, `extension`, and `mime_type` in upload and session-restore responses. Filenames and native save dialogs retain that extension, even when a supplied pattern names another format. M4A numeric values are validated before saving; unsupported values return HTTP 422 without modifying the file. WAV INFO title/artist/album/genre/year/comment/track/composer values are synchronized only where those entries already exist; unknown INFO entries and other chunks remain intact. UTF-8 is written for changed INFO text; legacy text falls back to Windows-1252 when reading.

Metadata writes copy the session file to a sibling temporary file, write native tags, validate the result, and atomically replace the session file. No transcoder runs in the application. Encoded samples remain unchanged, unrelated tags remain intact, and original uploads on the user's device are untouched. Saves briefly require space for a second copy (and an additional temporary copy when synchronizing WAV INFO). Existing MP3 sessions work without migration.

## 4. Audio Streaming & Range Requests

To allow instantaneous scrubbing and preview in web browsers, the `GET /api/stream` endpoint implements **HTTP 206 Partial Content**:
- Parses `Range: bytes=start-end` request headers.
- Streams audio chunks (64 KB) on-demand.
- Supports seeking without downloading the full audio file ahead of time.

---

## 5. Download & File System Operations

- **File System Access API**:
  - In supported Chromium-based browsers, `window.showSaveFilePicker()` is utilized to prompt the user to choose an explicit save location on their local filesystem.
- **Named Path Downloads**:
  - `GET /api/download/{filename}` routes supply a suggested download name; they do not select arbitrary filesystem paths. The signed session selects the file.
- **Content-Disposition Encoding**:
  - Employs RFC 5987 parameter encoding (`filename*=UTF-8''...`) for full Unicode fidelity and an ASCII-sanitized `filename` fallback.

---

## 6. Service Lifecycle, Network Configuration & Migration Architecture

MP3MetaFix packages an intelligent, non-destructive maintenance engine for systemd units and network configuration:

### Systemd Unit Migration (`scripts/migrate_service.py`)
- **Scope Isolation**: Parsing and edits are strictly confined to the `[Service]` section.
- **Safe Command Tokenization**: Uses POSIX-compliant `shlex` tokenization to preserve arguments with spaces while avoiding destructive whitespace splitting.
- **Safety Rejection**: Explicitly rejects compound commands, subshells, shell pipelines (`|`), redirects (`>`), and invalid syntax to prevent unit corruption.
- **Atomic State Updates**: Creates temporary unit files in the target directory, mirrors original POSIX file mode and ownership, and performs atomic replacement via `os.replace`.
- **Customization Preservation**: Updates only the Uvicorn launch flags (`--no-proxy-headers`, `--host $MP3METAFIX_HOST`, `--port $MP3METAFIX_PORT`) while preserving all administrator-defined environment variables, workers, and sandboxing limits (`MemoryMax`, `TasksMax`, `CPUQuota`).
- **Self-Re-Executing Updates**: Re-executes the installer process image in-place (`exec bash`) when new commits are pulled, guaranteeing that newly introduced migrations and fixes execute immediately during the initial update run.

### Network Access & Reverse Proxy Configuration (`scripts/configure_access.py`)
- **Safe Binding & Proxy Management**: Inspects and adjusts `MP3METAFIX_HOST`, `MP3METAFIX_PORT`, `MP3METAFIX_TRUST_PROXIES`, `MP3METAFIX_TRUSTED_PROXIES`, and `MP3METAFIX_PROXY_HOST` directives inside `[Service]` without manual unit file editing.
- **Strict Input Validation**: Validates IPv4/IPv6 addresses, RFC 1123 hostnames, and comma-separated CIDR subnets before modifying configuration; rejects metacharacters, semicolons, and injection strings.
- **Atomic Replacement**: Employs permissions-preserving atomic file replacement matching `scripts/migrate_service.py`.
- **Informational Proxy Probing**: The configured `MP3METAFIX_PROXY_HOST` domain/URL is strictly informational for verifying upstream reachability and presenting client access links; it does not alter server socket listening boundaries (which remain strictly governed by `MP3METAFIX_HOST`).
- **Automated Lifecycle & Health Probing**: Integrated with `install.sh` (`--access`, `--lan`, `--local`, `--bind`, `--proxy`, `--domain`, `--no-domain`, `--no-proxy`, `--trusted-proxies`) to automate daemon reloading, service restarts, and dual-probe health verification across both local interfaces and external proxy domains.

---

## 7. Multi-Interface Architecture & Sub-Project Decoupling

Four routes share one backend: `/` dispatches workspaces, `/admin` shows administrator diagnostics/updates, `/app` implements single-track editing, and `/manager` currently supplies a desktop shell with a coming-soon state.

### Architectural principle: shared capabilities

Every `/app` capability must be available through the same engine/API for `/manager`. The planned integrated inspector, batch tools, raw frame inspector, stems, and synced lyrics are **not implemented**. See the [roadmap](../ROADMAP.md) for planned scope. Switching interfaces does not create a separate audio engine.

### 1. Gateway Hub (`/`) and Administrator Control Center (`/admin`)
- **Gateway Hub**: Workspace selector with a one-time `GET /api/health` request for basic availability and version. No telemetry quickbar or recurring resource polling remains on this page.
- **Administrator Control Center**: Detailed CPU, memory, disk, temporary-storage, uptime, and network diagnostics use `GET /api/system/stats`. Polling starts after administrator authentication and stops on logout or an authorization failure.
- **Navigation**: Administrators reach `/admin` through the account menu’s **Admin Dashboard** link. The hub header sits above workspace cards so its dropdown is not obscured.
- **Authorization**: The backend telemetry endpoint retains `require_admin`; removing hub telemetry does not change API access policy.

### 2. MP3MetaFix Focused Editor (`/app`)

The editor update inspector treats its header notification badge as optional; both update-result branches still render the version dialog when that badge is absent.
- **Target Persona**: Mobile Creators, Smartphone/Tablet Users, Single-Track Producers.
- **Design Philosophy**: Lightweight, touch-optimized, mobile-first design with zero horizontal overflow across 320px–4K displays.
- **Responsive Architecture**:
  - CSS Grid track floors enforced via `minmax(0, 1fr)` to prevent min-content text blowout on narrow screens.
  - Constrained album artwork preview dimensions (`max-width: 210px` on mobile phones) to prevent massive vertical scrolling.
  - Balanced 2-column workspace on tablets ($\ge 720\text{px}$) keeping artwork and metadata forms immediately accessible.
  - Responsive button label typography (`.btn-txt-full` / `.btn-txt-short`).
- **Core Capabilities**: Editing the supported normalized fields in native MP3/M4A/WAV tags, Web Audio dynamic waveform canvas rendering, APIC cover art processing, canned comment presets, dynamic filename formatting, Suno link parsing, and File System Access API save integration.

### 3. MP3MetaManager Desktop Workspace (`/manager`)
- **Target Persona**: Desktop Power Users, DJs, Album Curators, Batch Producers.
- **Design Philosophy**: High-density desktop workspace built for widescreen 1080p–4K displays.
- **Planned Superset Capabilities (not yet implemented)**:
  - **Integrated Single-Track Inspector**: Built-in drawer offering in-place access to all `/app` features (all ID3 fields, waveform scrubber, cover art studio, canned comment presets, dynamic filename generator, and Suno prompt parser).
  - **Multi-Track Batch Spreadsheet Editor**: High-density table with keyboard navigation (<kbd>Tab</kbd>, <kbd>Enter</kbd>), bulk tag propagation, regex find-and-replace, and auto-numbering.
  - **Universal ID3 Frame & Raw MPEG Byte Inspector**: Direct viewing, editing, and addition of any standard ID3 frame, custom `TXXX` key-values, multi-language `COMM`/`USLT` descriptors, multiple `APIC` pictures, and low-level hex inspection.
  - **Karaoke Tap-to-Sync & Synced Lyrics (`.lrc` / `SYLT`)**: Interactive playback timestamping tool for synced lyrics sidecars and ID3 frames.
  - **Suno AI Stem & Generation Tree Organizer**: Visual lineage graph organizing extensions, variations, and split stems.

### 4. Global App Switcher
- Embedded header pill navigation allowing instant switching between `/app`, `/manager`, and `/` across all interfaces without losing active context.

---

## 8. Suno.com Metadata Extraction & Selective Merge Architecture

MP3MetaFix integrates automated server-side extraction and non-destructive client merging for AI-generated music tracks from Suno.com:

### Extraction Architecture (`backend/suno_extractor.py`)
1. **UUID Identification**: Matches standard v4 UUIDs from direct strings, song URLs (`https://suno.com/song/{uuid}`), and embedded comment tags (`made with suno; ... id={uuid}`).
2. **Initial URL Construction**: Requests begin at `https://suno.com/song/{uuid}` with a 10s timeout and browser headers; automatic redirects and whole-response reads remain audit findings.
3. **Next.js SSR Stream Deserializer**: Parses Server Component stream payloads (`self.__next_f.push`) to extract:
   - Track Title (`TIT2`)
   - Creator Display Name & Handle (`TPE1`, `TPE2`, `@username`)
   - Musical Style & Prompt Tags (`TCON`)
   - Structured Lyrics with structural section markers (`USLT`)
   - High-Resolution Album Artwork (`APIC` up to 1024×1024 JPEG from Suno CDN)
   - Creation Date / Year (`TDRC`)
   - Model Engine / Version (`COMM`)
4. **Artwork Staging & Validation (`POST /api/suno/apply-artwork`)**:
   - Downloads artwork from Suno CDN domain (`cdn2.suno.ai`, `cdn1.suno.ai`).
   - Normalizes and validates byte streams via Pillow with decompression bomb defenses (`MAX_IMAGE_PIXELS = 10,000,000`).
   - Stages normalized artwork directly into the cryptographic session directory.

### Non-Destructive Selective Merge Model (Zero Blind Overwrites)
- **Automatic Detection**: When uploaded audio contains a Suno UUID in comments, a non-disruptive micro-pill (`✨ Suno Detected`) appears in the file bar.
- **Interactive Diff Table**: A side-by-side comparison displays Current Audio values versus Suno Extracted values.
- **Granular User Control**: Checkboxes allow the user to select specific fields to apply, with presets for `Apply Selected`, `Fill Blank Only` (enriches empty tags without modifying user edits), and `Apply All`.

### Terms of Service & Acceptable Use Posture
- The integration fetches public song pages on demand. No equivalence to another service’s permissions or legal compliance has been established.
- Zero user account credentials, JWT tokens, or private generation endpoints are accessed.
- For implementation scope and pending policy review, see [`docs/SUNO_TOS_COMPLIANCE.md`](SUNO_TOS_COMPLIANCE.md).

---

## 9. Authentication & Access Control Subsystem

MP3MetaFix integrates a zero-external-dependency authentication subsystem built on standard library cryptographic primitives:

```
[ Unauthenticated Client ]
        │
        ▼
   [ GET /api/auth/status ]
        │
        ├── !initialized ─────────► [ First-Run Setup Wizard Modal ] ──► POST /api/auth/setup
        │
        ├── initialized & !auth ──► Check Guest Mode Policy:
        │                              ├── If Guest Mode & (/ or /app) ──► Allow Hub & Single-Track Tagging (Guest Pill)
        │                              └── Else (/manager or Guest Mode Off) ─► Show Login Modal ──► POST /api/auth/login
        │
        └── authenticated ────────► [ Header Account Pill & Dropdown ]
                                       ├── Settings & Security Modal (Guest Mode Toggle, Quotas, Passwords)
                                       └── Sign Out ──► POST /api/auth/logout
```

### Key Subsystem Characteristics:
1. **Zero-Dependency Password Hashing**:
   - Standard library `hashlib.pbkdf2_hmac` (`sha256`, 600,000 iterations, unique 16-byte random salt).
   - Format: `pbkdf2_sha256$600000${salt_hex}${hash_hex}`.
   - Verification uses timing-attack-resistant `secrets.compare_digest`.
2. **Distinct Cookie Trust Boundaries**:
   - `mp3metafix_auth`: Manages user account authentication (`{user_id}.{timestamp}.{sig}`).
   - `mp3metafix_session`: Manages isolated temporary file-editing storage (`{session_id}.{timestamp}.{sig}`).
   - The two cookie names and backend dependency checks separate their roles; both currently use the configured signing secret.
3. **Filesystem Security (POSIX 0700 / 0600)**:
   - Account and system settings metadata are stored in `data/auth/users.json` and `data/auth/settings.json`.
   - The `data/auth/` directory is created with POSIX `0700` (`rwx------`) permissions and files are created with POSIX `0600` (`rw-------`).
4. **Brute-Force Rate Limiting (`LoginRateLimiter`)**:
   - Enforces a 5-attempt sliding window per client IP per 60 seconds with an automatic 5-minute cooldown period upon threshold violation.
5. **Configurable Guest Mode Policy**:
   - Administrator-toggled policy allowing anonymous visitors to access the Gateway Hub (`/`) and focused single-track editor (`/app`) while the manager UI prompts for login. Static pages are public. Telemetry and settings writes require administrators; settings reads require authentication.

---

## 10. Sub-Project Roadmap Alignment

For full feature backlogs, milestones, and strategic plans for each interface, see [ROADMAP.md](../ROADMAP.md).

## 11. Update execution and persisted settings

The update endpoint is enabled and administrator-protected, but uses a process-local asyncio lock. It does not serialize installers across the two observed workers or safely handle every disconnect; raw output/exception sanitization is incomplete. See audit finding 5 before modifying this privileged flow.

AuthManager persists Guest Mode and quota/TTL preferences. Guest Mode is consulted by editing authorization; runtime upload, storage, and session limits still use environment-derived configuration. Password changes update the hash without invalidating existing tokens. Invalid account JSON is treated as an empty store and can reopen setup; this is a known high-impact failure mode, not intended recovery behavior.
