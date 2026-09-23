# MP3MetaFix Master Product Roadmap & Multi-Interface Vision

This document details the multi-interface roadmap for **MP3MetaFix**. 

Although MP3MetaFix runs as a unified high-performance Python FastAPI service with a shared native-format metadata engine and cryptographic storage model, **each frontend interface is treated as a distinct sub-project with its own dedicated roadmap, UX philosophy, and target user persona**.

---

## 🧭 Interface Overview & Strategy Matrix

| Project / Interface | Route | Primary Target Persona | Screen & Form Factor | Key Design Focus & Scope |
|---|---|---|---|---|
| **Gateway Hub** | `/` | System Administrators, Homelab Users, Multi-Device Operators | All screens (320px–4K) | Compact app dispatching and basic availability; diagnostics in `/admin` |
| **MP3MetaFix Editor** | `/app` | Mobile Creators, Smartphone/Tablet Users, Single-Track Producers | Mobile-first (phones, tablets, responsive desktop) | Speed, friction-free single-track tagging, touch waveforms, Suno prompt parsing, canned presets |
| **MP3MetaManager** | `/manager` | Desktop Power Users, DJs, Album Curators, Batch Producers | Desktop-first (widescreen, 1080p–4K displays) | **Functional Superset of `/app`**: Full single-track inspector + high-density tables, multi-file batch tagging, deep ID3 frame inspector/editor, stem trees, synced lyrics (LRC/SYLT) |
| **Core Platform** | `/api` | DevOps, Package Maintainers, Infrastructure Engineers | CLI, Systemd, Reverse Proxies, Docker | Security hardening, zero-downtime updates, cross-platform deployment |

> [!NOTE]
> **Architectural Principle: Workflow & Device-Driven Interface Selection (Superset Model)**  
> **Required future architecture:** `/manager` must include every `/app` capability through shared backend services and an integrated inspector. That inspector is not implemented yet; the current manager is a shell. Desktop batch workflows and mobile-focused editing are separate interface goals, not separate format engines.

---

## 🏠 Project A: Gateway Hub (`/`) — System Portal & Dispatcher Roadmap

The **Gateway Hub** is the compact front door for choosing a workspace. Detailed host diagnostics and temporary-storage quotas are consolidated in the **Administrator Control Center (`/admin`)**, accessible through the account menu.

### Completed Features ✅
- [x] **Full System Authentication & Access Control Subsystem**:
  - Default protected mode requiring administrator login across Hub (`/`), Desktop MetaManager (`/manager`), System Diagnostics (`/api/system/stats`), and backend mutating APIs.
  - Automated First-Run Setup Wizard modal for primary administrator account creation.
  - Configurable Guest Mode toggle enabling public single-track tagging at `/app` without compromising server diagnostics or batch workspace.
  - PBKDF2-HMAC-SHA256 (600,000 rounds) password hashing + brute-force rate-limiting.
  - In-app Settings & Quota Management modal for session limits, disk quota, and password changes.
- [x] **Telemetry Consolidation in `/admin`**:
  - CPU load, memory, disk, and temporary-storage diagnostics remain in the administrator Control Center.
  - Removed the hub quickbar and recurring telemetry requests; retained a one-time health/version check.
  - Raised the hub header above workspace cards to keep the account dropdown accessible on mobile.
- [x] **Workspace Dispatcher Cards**:
  - Compact selector cards for **MP3MetaFix** and **MP3MetaManager** with feature breakdown grids and direct entry buttons.
- [x] **Administrator Diagnostics Dashboard**:
  - CPU, memory, host disk, cache quotas, uptime, and network configuration shown in `/admin`.
- [x] **High-Contrast Responsive UI (WCAG AAA)**:
  - High-contrast footers, readable links, and glassmorphic telemetry cards across all screen resolutions (320px–4K).

### Active Backlog & Future Vision 📋
- [x] **Display installed version publicly; check for updates only in `/admin`**:
  - Keep the installed application version visible in the header for guests and signed-in users, using a read-only local version/health response. Displaying or clicking it does not trigger a remote update check.
  - Removed automatic and manual update checks, update prompts, and update-check controls from `/app`, `/manager`, and the public hub. Confined update discovery and installation controls exclusively to `/admin`, gated by administrator authentication.
  - Verified guest startup, refresh, session restore, and version-display interactions never request `/api/updates/check`, eliminating unauthenticated 401 triggers while preserving authorized administrator update management.
- [ ] **Multi-User Role & Quota Policies**:
  - Granular per-user storage quotas, tenant directories, and role management (Editor, Viewer, Admin).
- [ ] **Service Daemon Maintenance Triggers**:
  - Authenticated admin actions to trigger cache cleanup, storage purge, or service restart directly from the web portal.
- [ ] **Live Audio Processing Metrics**:
  - Real-time counters displaying active streaming sessions, total MP3s processed, and average save latency.
- [ ] **System Health History & Historical Graphs**:
  - Visual CPU and memory utilization trendlines over the past 24 hours.

---

## 📱 Project B: MP3MetaFix (`/app`) — Mobile-First Single-Track Editor Roadmap

**MP3MetaFix** is the dedicated single-track audio metadata, cover art, and inspection workspace. Designed mobile-first for friction-free music tagging on smartphones, tablets, and desktops alike.

### Completed Features ✅
- [x] **Native-format engine and UI implementation (v0.5.0)**:
  - Shared engine supports M4A AAC/ALAC/Opus atoms and WAV ID3 artwork/tags plus existing RIFF INFO text; encoded audio is preserved.
  - Synthetic MP3/AAC M4A/WAV tests verify upload, restore, playback, filename patterns, artwork, and export without changing format. Tested with WAV and real Suno Opus M4A files.
  - `/manager` inherits these shared engine/API capabilities as part of its required `/app` feature parity; its current UI remains a placeholder.


- [x] **Editor update-check compatibility**: Update results render correctly when the optional header badge is absent.
- [x] **Fluid Mobile-First Responsive Layout (320px to 4K)**:
  - Latest browser checks found no horizontal document overflow at 320px, 375px, and 1440px widths; this is viewport testing, not a certification of every device/browser.
  - Balanced 2-column layout on tablets ($\ge 720\text{px}$) keeping artwork and metadata forms immediately accessible.
  - Centered, constrained album art preview container (`max-width: 210px` on phones) to prevent massive vertical scrolling.
- [x] **Complete ID3v2.3 / ID3v2.4 Metadata Tagging**:
  - Track Title, Artist, Album, Album Artist, Genre, Year / Date, Track/Total, Disc/Total, BPM, Composer, Comments, and Lyrics.
- [x] **Retina Canvas Audio Waveform Visualizer & Scrubber**:
  - Web Audio API dynamic peak rendering, played/unplayed gradient states, continuous drag scrubbing, and hovering time tooltips.
- [x] **Album Artwork APIC Extraction & Replacement**:
  - Inspect embedded cover art, upload new images (PNG/JPEG retained in normalized form; WebP converted to JPEG), and extract/remove artwork.
  - Responsive 3-button action row (`[ Upload ] [ Extract ] [ Remove ]`).
- [x] **Canned Comment Presets & Preset Manager**:
  - Quick dropdown prefilling for creator profiles (e.g. Suno links) and local storage management modal.
- [x] **Dynamic Filename Formatter**:
  - Instant pattern-based renaming (`%artist% - %title%.mp3`, `%track% - %title%.mp3`) with preset buttons.
- [x] **Native Save & Named File Download**:
  - Modern Chromium File System Access API with RFC 5987 UTF-8 download fallbacks.
- [x] **Suno.com URL & Clip UUID Metadata / Artwork Extraction**:
  - Server-side Next.js stream deserializer fetching Title, Creator (`@username`), Musical Style (`TCON`), Structured Lyrics with structure markers (`USLT`), High-Resolution Artwork (1024×1024 into `APIC`), and Model attribution (`COMM`).
- [x] **Embedded Suno Tag Auto-Detection & Selective Merge Modal**:
  - Automatic UUID recognition from uploaded MP3 comment tags (`made with suno; ... id=...`).
  - Mobile-first, non-disruptive detection pill (`✨ Suno Detected`) and interactive per-field selective merge table with `Apply Selected`, `Fill Blank Only`, and `Apply All` presets (zero blind overwrites).
- [x] **Configurable AI Metadata Extractors & Gating Policy**:
  - Administrator policy toggle in `/admin` (under "AI Services & Metadata Extractors") and Quick Settings modal with `suno_integration_enabled` defaulting to `False` (disabled) for a clean default state.
  - Automatic tag detection in `/app`, the `✨ Suno Detected` status pill, and the `✨ Suno Sync` button are hidden/gated when disabled.
  - Backend API routes (`/api/suno/extract` and `/api/suno/apply-artwork`) enforce `HTTP 403 Forbidden` protection when disabled.
- [x] **Dynamic Quick Settings Pinning**:
  - Surfaced configurable system settings (`guest_mode_enabled`, `suno_integration_enabled`, `max_sessions`, `max_global_storage_mb`, `session_ttl_minutes`, `max_upload_size_mb`) in the Quick Settings modal across all views with instant real-time toggling and auto-save.

- [x] **Diagnosed and resolved M4A parsing failure**: The affected Suno download contained Opus-in-MP4; added Opus to the codec allowlist. Verified working with a real Suno download.

### Active Backlog & Future Vision 📋
- [ ] **Native Web Share API (`navigator.share`)**:
  - One-tap mobile export to send modified MP3s directly to mobile audio players (VLC, Files, Telegram, Discord, Apple Music).
- [ ] **MediaSession API Integration**:
  - Display track title, artist, album art, and seek controls in the mobile lockscreen and notification media widget during audio preview playback.
- [ ] **Sticky Mobile Bottom Action Bar**:
  - Persistent bottom toolbar housing Save/Download and Play/Pause controls for one-thumb editing when viewing long lyrics.
- [ ] **PWA (Progressive Web App) Manifest**:
  - Installable to home screen on iOS and Android with offline caching and standalone display mode.
- [ ] **Light / Dark Theme Switcher**:
  - User toggle between obsidian dark mode and clean glassmorphic light mode with OS `prefers-color-scheme` synchronization.

---

## 🗂️ Project C: MP3MetaManager (`/manager`) — Desktop Power-User Workspace Roadmap

**MP3MetaManager** is the desktop-focused power-user interface engineered for heavy multi-track batch operations, album assembly, library management, and deep audio organization. **Its required future scope includes all `/app` features in the same workspace. Today only the shell/navigation exists; inspector and batch capabilities remain backlog items**.

### Completed Features ✅
- [x] **Desktop Workspace Foundation**:
  - Multi-panel desktop shell with structured sidebar categories (Batch Queue, Suno AI Stems, Synced Lyrics, Karaoke Tap-to-Sync).
  - High-density top toolbar with search filter and batch action controls.
- [x] **Global Header Switcher Integration**:
  - Unified app switcher navigation pill linking back to `/app` and `/` seamlessly.
- [x] **Coming Soon State & Navigation Safety**:
  - Clear placeholder state with active editor handoff buttons to prevent dead-end interactions while in development.

### Active Backlog & Future Vision 📋
- [ ] **Full Feature Parity with MP3MetaFix (`/app`) via Integrated Single-Track Inspector**:
  - Embedded collapsible **Track Detail Inspector Drawer / Side Panel** allowing comprehensive single-track editing without leaving the batch workspace:
    - *Full Tag Suite*: In-place editing for Track Title, Artist, Album, Album Artist, Genre, Year/Date, Track/Total, Disc/Total, BPM, Composer, Comments, and Unsynchronized Lyrics (`USLT`).
    - *Album Artwork Studio*: Direct cover art drag-and-drop replacement, high-res preview, format normalization (JPEG/PNG/WebP), extraction, and removal.
    - *Retina Canvas Audio Waveform Visualizer*: Full audio playback with dynamic waveform peaks, continuous drag scrubbing, and HTTP 206 streaming for the currently selected track.
    - *Canned Comment Presets & Preset Manager*: Quick-select preset dropdown and persistent custom preset creation.
    - *Dynamic Filename Formatter*: Instant pattern-based renaming (`%artist% - %title%.mp3`, `%track% - %title%.mp3`) with preset buttons.
    - *Suno.com URL / Share Link Auto-Parser*: Direct URL fetching and tag prefilling for single tracks within the desktop queue.
    - *Single-Track Native Save & Export*: In-place disk writeback and direct download.
- [ ] **Universal ID3 Frame & Raw Metadata Inspector / Editor ("View/Edit All Embedded Information")**:
  - **Comprehensive Frame Manager**: View, add, edit, and delete **any** embedded ID3v2.3 / ID3v2.4 frame:
    - *Standard Text Frames*: `TIT1` (Grouping), `TIT2` (Title), `TIT3` (Subtitle), `TPE1`–`TPE4` (Artists/Conductor), `TALB` (Album), `TOAL` (Original Album), `TCOM` (Composer), `TEXT` (Lyricist), `TCON` (Genre), `TCOP` (Copyright), `TPUB` (Publisher), `TDRC`/`TYER` (Recording Date), `TRCK` (Track), `TPOS` (Disc), `TBPM` (BPM), `TKEY` (Initial Key), `TLAN` (Language), `TSRC` (ISRC), `TSSE` (Encoder).
    - *User-Defined & Extended Text Frames (`TXXX`)*: Inspect and create custom key-value pairs (e.g. `SUNO_ID`, `PROMPT`, `SEED`, `MODEL_VERSION`, `REPLAYGAIN_TRACK_GAIN`, `REPLAYGAIN_TRACK_PEAK`).
    - *URL Frames (`WXXX` / `WOAR` / `WCOM`)*: Official artist webpage, audio source webpage, copyright links.
    - *Multi-Language Comments (`COMM`)* and *Unsynchronized Lyrics (`USLT`)*: Manage multiple comment/lyric descriptors with custom ISO-639 language codes.
    - *Synchronized Lyrics (`SYLT`)* and *Event Timing Codes (`ETCO`)*: Embedded time-coded lyric cues and tempo events.
    - *Multi-Picture APIC Manager*: View, extract, or embed multiple artwork types within a single file (Front Cover `0x03`, Back Cover `0x04`, Media/CD `0x06`, Artist Lead `0x08`, Leaflet `0x01`).
    - *Specialized Binary Frames*: `PRIV` (Private software metadata), `GEOB` (General Encapsulated Objects), `UFID` (Unique File Identifier), `POPM` (Popularimeter ratings & play counts), `RVA2` (Relative Volume Adjustment), `MCDI` (Music CD Identifier).
  - **MPEG Stream & Low-Level Byte Inspector**:
    - Detailed audio container analysis: MPEG audio layer, channel mode, LAME/Xing VBR headers, padding byte allocation, and ID3 header flags (unsynchronization, extended header, experimental).
    - Raw Frame Hex Viewer: Inspect raw byte offsets, header flags, and hex payloads for any frame.
- [ ] **High-Density Spreadsheet Batch Editor**:
  - Multi-column spreadsheet grid supporting keyboard navigation (<kbd>Tab</kbd>, <kbd>Enter</kbd>, arrow keys) for editing hundreds of tracks simultaneously.
  - Multi-row selection with bulk tag applicator (apply common Artist, Album, Genre, Year, Cover Art to all selected rows).
  - Regex search and replace across track titles and filenames.
  - Sequential auto-numbering (`1/12`, `2/12`, ...) and drag-and-drop track reordering.
- [ ] **Batch File & Folder Ingestion**:
  - Drag and drop dozens of audio files or whole folder trees using the File System Access Directory Picker.
- [ ] **Custom Lyric Video Studio & MP4 Generator (`.mp4`)**:
  - Direct creation of clean, customizable 9:16 (vertical mobile / Reels / TikTok / Shorts) and 16:9 (horizontal widescreen YouTube) MP4 lyric videos directly from audio + synchronized lyrics (`SYLT` / `.lrc`).
  - **Visual & Layout Customization**:
    - *Background Styling*: Dynamic ambient blurred album art glow, animated gradient canvas, custom background video/image loop, or minimalist studio dark mode.
    - *Center Artwork Card*: High-res album cover preview with configurable corner rounding, shadow depth, and scale.
    - *Typography & Text Controls*: Configurable song title, artist `@handle`, font family, font size, line spacing, and text alignment.
    - *Synced Karaoke Text Engine*: Smooth vertical scrolling and active line-by-line highlighting synchronized with playback timecodes.
    - *Branding & Watermark Control*: Replace the default "MADE WITH SUNO" footer watermark with the artist's own logo, social handle text, or export completely unbranded.
  - **Fast Encoding**: Real-time client-side Canvas preview with high-performance server-side FFmpeg H.264/AAC video composition.
- [ ] **Suno AI Stem Pack Bundler & Multi-Track Manager (WAV/MP3)**:
  - Ingest separated Suno stems (*Vocals*, *Instrumental*, *Bass*, *Drums*).
  - Automatically apply shared song metadata (Artist, Title, Year, Cover Art) across all stem files while stamping specific role identifiers (e.g. `[Title] (Vocals)`).
  - One-click packaging into structured multi-track `.zip` archives or DAW-ready directories.
- [ ] **Suno MP4 Video Ingestion & Asset Extractor**:
  - Drag and drop Suno `.mp4` video files to extract clean audio (lossless WAV or MP3 320kbps), extract timestamped lyrics, and recover high-resolution poster artwork.
- [ ] **Tap-to-Sync Karaoke Lyric Stamping (`.lrc` & ID3 `SYLT`)**:
  - Interactive "Tap-to-Sync" tool allowing creators to tap <kbd>Space</kbd> during playback to stamp exact timestamps onto lyric lines.
  - Export standard `.lrc` sidecar companion files or embed directly into the ID3 `SYLT` binary frame.
- [ ] **Suno AI Stem & Generation Tree Organizer**:
  - Visual parent/child lineage graph organizing variations, extensions, and separated vocal/instrumental stems.
- [ ] **Bulk Cover Art Manager**:
  - Batch extract, resize, square-crop, and embed high-resolution artwork across an entire album or batch queue.
- [ ] **Batch ZIP Archive Download & In-Place Writeback**:
  - Download all modified tracks as a structured `.zip` archive or save modified tags directly back to the original files on disk.
- [ ] **Audio Fingerprinting & Duplicate Detection**:
  - **Mandatory read-only operation:** compute and compare Chromaprint / AcoustID fingerprints solely to identify identical takes and duplicate audio renders. Store analysis results separately from source files.
  - Never modify, replace, remove, obscure, or spoof an existing fingerprint, watermark, or provenance marker. Never alter or re-encode audio to change its fingerprint or evade duplicate detection. This applies to Suno and any future supported service.
  - Detection must not write to source audio or metadata, or automatically delete, merge, or replace duplicates; present findings for review only. Verify source files remain byte-for-byte unchanged after analysis.
  - Treat preservation as a project requirement independent of provider terms; review applicable service terms before integrating fingerprinting or external lookup services.

---

## ⚙️ Project D: Core Platform, Security & Infrastructure Roadmap

The underlying Python backend, Mutagen audio engine, systemd service architecture, and deployment automation powering all frontends.

### Completed Features ✅
- [x] **Baseline Security Controls (incomplete; see audit remediation backlog)**:
  - Timestamped HMAC-SHA256 session cookies, POSIX `0700` filesystem isolation, decoupled SHA-256 hashed storage directories.
  - Chunk-level magic bytes validation, uploaded-image checks (Pillow thresholds and dimension limits), sliding-window rate limiting with proxy anti-spoofing.
  - Origin/Sec-Fetch-Site CSRF protection and generic errors on normal editing paths; v0.5.1 also restricts updater output to fixed status messages.
- [x] **Non-Destructive Systemd Migration Engine (`scripts/migrate_service.py`)**:
  - Safe, atomic unit updates during upgrades while preserving administrator customizations, custom environment variables, and cgroup resource limits.
- [x] **Service Network Access, Proxy Trust & LAN Maintenance CLI (`scripts/configure_access.py`)**:
  - Fast maintenance commands (`install.sh --access`, `--lan`, `--local`, `--bind`, `--proxy`, `--domain`, `--no-domain`, `--no-proxy`, `--trusted-proxies`).
- [x] **Self-Re-Executing Linux Installer (`install.sh`)**:
  - Automated upgrade handoff executing newly pulled code immediately without requiring secondary update runs.

### Active Backlog & Future Vision 📋

- [x] **Application security audit remediation (v0.5.1)** — [verification and deployment exceptions](docs/SECURITY_REMEDIATION_2026-09-21.md):
  - Reject/normalize embedded active-content artwork before preview or serving (high).
  - Enforce request-byte limits before multipart spooling/authentication and reserve storage across workers (high).
  - Fail closed on damaged account stores; serialize first-admin creation (high, conditional).
  - Revoke prior account tokens after password changes and define logout revocation (medium).
  - Serialize in-app updater processes across workers/disconnects and emit fixed status messages. Web updates require a local restart.
  - Validate outbound redirect destinations and bound Suno response reads (medium).
  - Added tested service containment; supported deployment profiles and remaining limitations are documented in the security and deployment guides.
- [x] **Scoped dedicated service-account migration helper**:
  - Implemented migration, retry, and rollback for the supported user-service deployment. General installer integration and LXC system-service conversion remain planned.
- [x] **Strict installer release-branch enforcement**:
  - Keep release updates explicitly sourced from `origin main`; removed the plain `git pull` fallback that could use another configured upstream or development branch on failure.
  - Detect checkouts on `development` or other non-`main` branches and stop with clear guidance before attempting release merges.
  - Require fast-forward merges (`--ff-only`) from `origin main` and fail clearly on fetch or divergence errors without touching or corrupting local files.
  - Documented development testing separately from the main-only release updater.
- [x] **General service-account migration and installer integration**:
  - Extended the migration/rollback helper ([docs/ACCOUNT_MIGRATION.md](docs/ACCOUNT_MIGRATION.md)) and `install.sh` (`--check-account`, `--migrate-account`, `--retry-account`, `--rollback-account`) to support both user-level and system-service deployments with sandbox preflight checks.
  - Preserves application accounts/passwords, signing secrets, audio/session data, environment settings, network bindings, and proxy configuration with verified byte-for-byte SHA-256 integrity and POSIX `0700`/`0600` permissions.
  - Automated service user creation in `install.sh` ensuring headless/server installs bind to dedicated `mp3metafix` service account rather than root.
- [ ] **Guided, one-shot installation with service-account selection or creation**:
  - Design for users who cannot confidently perform CLI administration: after the initial installer launch, use plain-language prompts and safe defaults to complete as much setup as possible without separate commands or manual file edits.
  - Offer creation of a dedicated non-login Linux service account as the recommended choice, or selection of a suitable existing unprivileged account. Explain that this is separate from the application's administrator login; never silently default the backend to root.
  - Automate missing dependencies, account validation/creation, application/data permissions, service setup, supported hardening, startup, and health checks. Clearly explain any required privilege prompt or unsupported host restriction.
  - Finish with the working access URL and clear success/recovery guidance. Make reruns safe and provide explicit unattended options for experienced operators.
  - Integrate hardening and account migration into upgrades while preserving existing customizations. Plan a narrowly scoped, authorized restart mechanism so routine updates do not ultimately require users to type service commands; do not grant the web backend general administrative access.
  - **Current limitation:** existing v0.5.1 upgrades preserve the service identity and sandbox configuration; automatic account migration and guided account creation are not implemented; the new explicit helper supports only the standard local developer user service.
- [ ] **Reliable uploads behind authenticated reverse proxies**:
  - Investigate intermittent upload authentication challenges with HTTP Basic Auth proxies. Preserve application authorization and validate repeated upload/edit/download flows through supported proxy configurations.
  - Verify guest update-check removal and browser authentication recovery separately; do not mark the upload issue resolved until end-to-end testing confirms it.
- [ ] **Apply stored quota/TTL settings to runtime policy**: settings persist today, while runtime enforcement reads environment-derived configuration.

- [ ] **Multi-Arch Docker & OCI Container Images**:
  - Official multi-architecture Dockerfile (`linux/amd64`, `linux/arm64`) published to GitHub Container Registry (GHCR) for unRAID, TrueNAS, and Docker Compose.
- [ ] **Native Windows Desktop Experience**:
  - Standalone bundled Windows `.exe` using PyInstaller / PyWebView (Edge WebView2 backend) with system tray integration and zero Python prerequisite.
- [ ] **Multi-Format Audio Engine Expansion**:
  - MP3, M4A (AAC/ALAC/Opus), and WAV handlers are implemented and tested. Remaining formats: **FLAC** (Vorbis comments & picture blocks), **OGG / Opus**, and **AIFF** (ID3 chunks).
- [ ] **Pluggable Storage Backend Drivers (`StorageProvider`)**:
  - Abstract storage interface supporting Local Filesystem, SMB/NFS shared network mounts, and S3 / MinIO Object Storage.
- [ ] **Asynchronous Background Task Queue**:
  - Worker queue for heavy batch audio transcoding and multi-gigabyte ZIP packaging with Server-Sent Events (SSE) progress streaming.

---

*Note: As each sub-project evolves, milestones and priorities are tracked and refined in this roadmap.*
