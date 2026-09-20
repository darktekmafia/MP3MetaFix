# MP3MetaFix Master Product Roadmap & Multi-Interface Vision

This document details the multi-interface roadmap for **MP3MetaFix**. 

Although MP3MetaFix runs as a unified high-performance Python FastAPI service with a single ID3 metadata engine and cryptographic storage model, **each frontend interface is treated as a distinct sub-project with its own dedicated roadmap, UX philosophy, and target user persona**.

---

## 🧭 Interface Overview & Strategy Matrix

| Project / Interface | Route | Primary Target Persona | Screen & Form Factor | Key Design Focus & Scope |
|---|---|---|---|---|
| **Gateway Hub** | `/` | System Administrators, Homelab Users, Multi-Device Operators | All screens (320px–4K) | System telemetry, health diagnostics, app dispatching |
| **MP3MetaFix Editor** | `/app` | Mobile Creators, Smartphone/Tablet Users, Single-Track Producers | Mobile-first (phones, tablets, responsive desktop) | Speed, friction-free single-track tagging, touch waveforms, Suno prompt parsing, canned presets |
| **MP3MetaManager** | `/manager` | Desktop Power Users, DJs, Album Curators, Batch Producers | Desktop-first (widescreen, 1080p–4K displays) | **Functional Superset of `/app`**: Full single-track inspector + high-density tables, multi-file batch tagging, deep ID3 frame inspector/editor, stem trees, synced lyrics (LRC/SYLT) |
| **Core Platform** | `/api` | DevOps, Package Maintainers, Infrastructure Engineers | CLI, Systemd, Reverse Proxies, Docker | Security hardening, zero-downtime updates, cross-platform deployment |

> [!NOTE]
> **Architectural Principle: Workflow & Device-Driven Interface Selection (Superset Model)**  
> **MP3MetaManager (`/manager`) is engineered as a complete functional superset of MP3MetaFix (`/app`)**. All single-track editing tools, waveform visualizers, cover art management, preset managers, and Suno link parsers present in `/app` are natively integrated into `/manager` via an embedded single-track inspector drawer. Users never need to switch between applications just to edit a single track's tags or cover art; interface selection is determined solely by the user's active workflow (focused quick edit vs comprehensive multi-track session) and client device form factor (mobile touch vs desktop workstation).

---

## 🏠 Project A: Gateway Hub (`/`) — System Portal & Dispatcher Roadmap

The **Gateway Hub** serves as the front door and system dashboard for the MP3MetaFix instance. It provides instantaneous visibility into host health, temporary MP3 cache quotas, and clean one-click launching into specialized workspaces.

### Completed Features ✅
- [x] **Live System Telemetry Quickbar (`GET /api/system/stats`)**:
  - Real-time CPU load (`1m`, `5m`, `15m`), memory utilization, disk space, and MP3 temporary cache quota tracking.
  - Responsive 2x2 micro-card mobile view with smooth-scrolling quick jump to full diagnostics.
- [x] **Workspace Dispatcher Cards**:
  - Compact selector cards for **MP3MetaFix** and **MP3MetaManager** with feature breakdown grids and direct entry buttons.
- [x] **Interactive Hardware Diagnostics Modal**:
  - Full system breakdown showing process memory, cgroup limits, runtime mode, and storage TTL status.
- [x] **High-Contrast Responsive UI (WCAG AAA)**:
  - High-contrast footers, readable links, and glassmorphic telemetry cards across all screen resolutions (320px–4K).

### Active Backlog & Future Vision 📋
- [ ] **Guest & Public Mode Access Controls**:
  - Administrator toggle allowing guest visitors to access only the single-track `/app` editor while locking `/manager` and system diagnostics behind authentication.
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
- [x] **Fluid Mobile-First Responsive Layout (320px to 4K)**:
  - Zero right-edge overflow on narrow screens (tested across iPhone SE, iPhone 16 Pro Max, iPad Mini, and desktop).
  - Balanced 2-column layout on tablets ($\ge 720\text{px}$) keeping artwork and metadata forms immediately accessible.
  - Centered, constrained album art preview container (`max-width: 210px` on phones) to prevent massive vertical scrolling.
- [x] **Complete ID3v2.3 / ID3v2.4 Metadata Tagging**:
  - Track Title, Artist, Album, Album Artist, Genre, Year / Date, Track/Total, Disc/Total, BPM, Composer, Comments, and Lyrics.
- [x] **Retina Canvas Audio Waveform Visualizer & Scrubber**:
  - Web Audio API dynamic peak rendering, played/unplayed gradient states, continuous drag scrubbing, and hovering time tooltips.
- [x] **Album Artwork APIC Extraction & Replacement**:
  - Inspect embedded cover art, upload new images (PNG/JPEG/WebP normalized to JPEG), and extract/remove artwork.
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

**MP3MetaManager** is the desktop-focused power-user interface engineered for heavy multi-track batch operations, album assembly, library management, and deep audio organization. **It incorporates all single-track editing features of `/app` directly into its workspace, eliminating any need to switch applications**.

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
  - Chromaprint / AcoustID audio fingerprinting to identify identical takes and duplicate audio renders.

---

## ⚙️ Project D: Core Platform, Security & Infrastructure Roadmap

The underlying Python backend, Mutagen audio engine, systemd service architecture, and deployment automation powering all frontends.

### Completed Features ✅
- [x] **14-Point Security Perimeter**:
  - Timestamped HMAC-SHA256 session cookies, POSIX `0700` filesystem isolation, decoupled SHA-256 hashed storage directories.
  - Chunk-level magic bytes validation, decompression bomb defenses (Pillow 10 MP limit), sliding-window rate limiting with proxy anti-spoofing.
  - Origin/Sec-Fetch-Site CSRF protection and exception masking.
- [x] **Non-Destructive Systemd Migration Engine (`scripts/migrate_service.py`)**:
  - Safe, atomic unit updates during upgrades while preserving administrator customizations, custom environment variables, and cgroup resource limits.
- [x] **Service Network Access, Proxy Trust & LAN Maintenance CLI (`scripts/configure_access.py`)**:
  - Fast maintenance commands (`install.sh --access`, `--lan`, `--local`, `--bind`, `--proxy`, `--domain`, `--no-domain`, `--no-proxy`, `--trusted-proxies`).
- [x] **Self-Re-Executing Linux Installer (`install.sh`)**:
  - Automated upgrade handoff executing newly pulled code immediately without requiring secondary update runs.

### Active Backlog & Future Vision 📋
- [ ] **Multi-Arch Docker & OCI Container Images**:
  - Official multi-architecture Dockerfile (`linux/amd64`, `linux/arm64`) published to GitHub Container Registry (GHCR) for unRAID, TrueNAS, and Docker Compose.
- [ ] **Native Windows Desktop Experience**:
  - Standalone bundled Windows `.exe` using PyInstaller / PyWebView (Edge WebView2 backend) with system tray integration and zero Python prerequisite.
- [ ] **Multi-Format Audio Engine Expansion**:
  - Extend Mutagen engine support to **FLAC** (Vorbis comments & picture blocks), **M4A / AAC / ALAC** (MP4 atoms), **OGG / Opus**, and **WAV / AIFF** (ID3 chunks).
- [ ] **Pluggable Storage Backend Drivers (`StorageProvider`)**:
  - Abstract storage interface supporting Local Filesystem, SMB/NFS shared network mounts, and S3 / MinIO Object Storage.
- [ ] **Asynchronous Background Task Queue**:
  - Worker queue for heavy batch audio transcoding and multi-gigabyte ZIP packaging with Server-Sent Events (SSE) progress streaming.

---

*Note: As each sub-project evolves, milestones and priorities are tracked and refined in this roadmap.*
