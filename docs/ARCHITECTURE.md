# MP3MetaFix Architecture & Security Specification

This document details the software architecture, metadata processing engine, security controls, and storage lifecycle of MP3MetaFix.

---

## 1. System Overview

MP3MetaFix is built as a lightweight, secure client-server application:
- **Backend**: Python 3.14 + FastAPI + Starlette + Mutagen + Pillow.
- **Frontend**: Zero-framework Vanilla JS + CSS Glassmorphism Design System.
- **IPC / Transport**: RESTful JSON APIs and HTTP 206 Partial Content audio streaming.

---

## 2. Security Perimeter & Threat Model

Because MP3MetaFix accepts arbitrary user uploads and is intended to run exposed behind reverse proxies and domain names, security is enforced across every layer:

```
[ Incoming Request ]
        │
        ▼
[ SecurityHeadersMiddleware ] (CSP, HSTS, X-Content-Type-Options: nosniff, SAMEORIGIN)
        │
        ▼
[ ProxyHeadersMiddleware ] (Handles X-Forwarded-For, X-Forwarded-Proto safely)
        │
        ▼
[ CSRFProtectionMiddleware ] (Blocks cross-site mutating requests & unauthorized origins)
        │
        ▼
[ InMemoryRateLimiter ] (Sliding-window IP rate limiting with trusted-proxy anti-spoofing)
        │
        ▼
[ Timestamped HMAC Cookie Authenticator ] (Validates HttpOnly {uuid}.{timestamp}.{signature})
        │
        ▼
[ Upload & Metadata Validator ]
  ├── 1. Chunk-level Magic Bytes Verification (ID3 / MPEG Frame Sync 0xFF 0xFB/FA/F3/F2)
  ├── 2. Upload Size Quota Enforcement (default: 150MB limit streamed)
  ├── 3. Decompression Bomb Defense (Pillow MAX_IMAGE_PIXELS = 10 MP, 4096x4096px limit)
  ├── 4. Pydantic Payload Length Constraints (Bounded title, artist, lyrics, comments)
  └── 5. Client Filename Sanitization (Replaces path traversals, control characters)
        │
        ▼
[ Decoupled Hashed Storage (POSIX 0700) ] (/data/temp/{SHA256(secret:uuid4)[:32]}/)
        │
        ▼
[ Quota Guard & Background TTL Purge ]
  ├── Global Storage Quota Guard (Auto-LRU pruning when disk storage reaches max quota)
  └── Periodic TTL Worker (Purges expired sessions > 60 minutes)
```

### Key Security Controls:
1. **Zero Raw Identifier Exposure**:
   - Internal session UUIDs and filesystem folder paths are never exposed in browser URLs, JavaScript state, or responses.
   - Authentication is maintained via an `HttpOnly`, `SameSite=Lax` cookie containing an HMAC-SHA256 signed token with embedded issuance timestamp (`session_id.timestamp.signature`).
2. **Decoupled Hashed Filesystem Storage & 0700 Permissions**:
   - Physical storage directories on the host filesystem are named using a one-way deterministic cryptographic hash: `SHA-256(server_secret:session_id)[:32]`.
   - All session directories are created with `0700` (`rwx------`) permissions, isolating temp storage across multi-tenant Linux hosts.
3. **Magic Bytes Header Verification**:
   - Files are inspected at the byte level before full persistence. Disguised executable files (e.g. `.exe`, `.elf`, `.php`, `.sh` renamed to `.mp3`) are rejected immediately with `HTTP 400 Bad Request`.
4. **Automated Lifecycle & Storage Quota LRU**:
   - Dynamic LRU session pruning triggers if total temporary storage exceeds `MAX_GLOBAL_TEMP_STORAGE_MB` (default 2GB).
   - A background asyncio task executes periodically (every 5 minutes) to prune sessions whose `last_accessed_at` timestamp exceeds the configurable TTL (`MP3METAFIX_SESSION_TTL_MINUTES`, default 60 minutes).
5. **CSRF & Origin Isolation**:
   - `CSRFProtectionMiddleware` rejects mutating state requests (`POST`, `PUT`, `DELETE`, `PATCH`) with foreign `Sec-Fetch-Site` or non-whitelisted `Origin` headers.
   - CORS is restricted to exact regex matchers (`localhost`, `127.0.0.1`, and explicit origins).
6. **Exception Masking**:
   - Internal Python exceptions and file paths are masked from HTTP responses to prevent server path leakage, while full diagnostics are recorded to internal server logs.
7. **Header Protection**:
   - `Content-Security-Policy`: Disallows untrusted script execution.
   - `X-Content-Type-Options: nosniff`: Prevents MIME confusion attacks.
   - `X-Frame-Options: SAMEORIGIN`: Protects against clickjacking.

---

## 3. Metadata Engine & Mutagen ID3 Frames

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

## 4. Audio Streaming & Range Requests

To allow instantaneous scrubbing and preview in web browsers, the `GET /api/stream/{session_id}` endpoint implements **HTTP 206 Partial Content**:
- Parses `Range: bytes=start-end` request headers.
- Streams audio chunks (64 KB) on-demand.
- Supports seeking without downloading the full audio file ahead of time.

---

## 5. Download & File System Operations

- **File System Access API**:
  - In supported Chromium-based browsers, `window.showSaveFilePicker()` is utilized to prompt the user to choose an explicit save location on their local filesystem.
- **Named Path Downloads**:
  - `GET /api/download/{session_id}/{filename}` routes provide direct file path semantics for browsers and download managers.
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

MP3MetaFix decouples user interaction into three specialized interfaces sharing a unified FastAPI backend, Mutagen ID3 engine, and cryptographic storage security model. Each interface is engineered and maintained as an independent sub-project with its own dedicated product roadmap:

```
                  ┌──────────────────────────────────────────────┐
                  │          FastAPI Backend Core (/api)          │
                  │  (HMAC Auth, Hashed Storage, Mutagen Engine) │
                  └───────┬──────────────┬──────────────┬────────┘
                          │              │              │
         ┌────────────────┴───┐   ┌──────┴─────────┐   ┌┴─────────────────────────────┐
         │  Gateway Hub (/)   │   │ MP3MetaFix     │   │ MP3MetaManager (/manager)    │
         │                    │   │ (/app)         │   │ (Superset of /app)           │
         │ - Live Telemetry   │   │ - Mobile-First │   │ - Desktop Power-User         │
         │ - Diagnostics Modal│   │ - Single-Track │   │ - Integrated Single-Track UI │
         │ - App Dispatcher   │   │ - Waveform     │   │ - Multi-Track Batch Table    │
         │ - Subnet Stats     │   │ - Canned Tags  │   │ - Deep ID3/Hex Byte Engine   │
         │                    │   │ - Suno Parser  │   │ - Synced Lyrics/LRC & Stems  │
         └────────────────────┘   └────────────────┘   └──────────────────────────────┘
```

### Architectural Principle: Workflow & Device-Driven Selection (Superset Model)
**MP3MetaManager (`/manager`) is engineered as a complete functional superset of MP3MetaFix (`/app`)**.
- The choice of interface is dictated by the user's **current workflow** and **client device form factor**:
  - **Mobile Phones & Focused Quick Edits (`/app`)**: Lightweight, distraction-free single-track editing optimized for touchscreens and quick mobile workflows.
  - **Desktop Workstations & Power Curation (`/manager`)**: Full-screen workspace with deep batch tools, multi-track spreadsheet tables, stem trees, synced lyrics, and universal raw ID3 frame inspector.
- **Zero Need for App Switching**: Desktop users never need to jump between interfaces just to edit single-track tags, scrub a waveform, or replace artwork. All `/app` features are embedded directly within `/manager` via an integrated single-track inspector drawer.

### 1. Gateway Hub & Telemetry Dashboard (`/`)
- **Target Persona**: System Administrators, Homelab Operators, Multi-Device Users.
- **Design Philosophy**: High-contrast, WCAG AAA accessible system portal providing immediate visibility into host health and hardware resources.
- **Core Endpoints**: `GET /api/system/stats` (CPU load averages, RAM capacity/usage, disk space, temporary storage quotas).
- **Security & Privacy**: Zero sensitive information exposure (no host directory paths, internal IP leakage, or system secrets).

### 2. MP3MetaFix Focused Editor (`/app`)
- **Target Persona**: Mobile Creators, Smartphone/Tablet Users, Single-Track Producers.
- **Design Philosophy**: Lightweight, touch-optimized, mobile-first design with zero horizontal overflow across 320px–4K displays.
- **Responsive Architecture**:
  - CSS Grid track floors enforced via `minmax(0, 1fr)` to prevent min-content text blowout on narrow screens.
  - Constrained album artwork preview dimensions (`max-width: 210px` on mobile phones) to prevent massive vertical scrolling.
  - Balanced 2-column workspace on tablets ($\ge 720\text{px}$) keeping artwork and metadata forms immediately accessible.
  - Responsive button label typography (`.btn-txt-full` / `.btn-txt-short`).
- **Core Capabilities**: Complete ID3v2.3/ID3v2.4 frame editing, Web Audio dynamic waveform canvas rendering, APIC cover art processing, canned comment presets, dynamic filename formatting, Suno link parsing, and File System Access API save integration.

### 3. MP3MetaManager Desktop Workspace (`/manager`)
- **Target Persona**: Desktop Power Users, DJs, Album Curators, Batch Producers.
- **Design Philosophy**: High-density desktop workspace built for widescreen 1080p–4K displays.
- **Superset Core Capabilities**:
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
2. **SSRF Defense & Strict Validation**: Requests are confined to `https://suno.com/song/{uuid}` with bounded timeouts (10s) and browser headers.
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
- **Automatic Detection**: When an uploaded MP3 contains a Suno UUID in comments, a non-disruptive micro-pill (`✨ Suno Detected`) appears in the file bar.
- **Interactive Diff Table**: A side-by-side comparison displays Current MP3 values versus Suno Extracted values.
- **Granular User Control**: Checkboxes allow the user to select specific fields to apply, with presets for `Apply Selected`, `Fill Blank Only` (enriches empty tags without modifying user edits), and `Apply All`.

### Terms of Service & Acceptable Use Posture
- The integration operates on the **Public Link Preview model** (identical to Discord/Twitter OpenGraph hydration).
- Zero user account credentials, JWT tokens, or private generation endpoints are accessed.
- For complete policy mapping and compliance specifications, see [`docs/SUNO_TOS_COMPLIANCE.md`](SUNO_TOS_COMPLIANCE.md).

---

## 9. Sub-Project Roadmap Alignment

For full feature backlogs, milestones, and strategic plans for each interface, see [ROADMAP.md](../ROADMAP.md).
