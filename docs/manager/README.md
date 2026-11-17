# MP3MetaManager (`/manager`) — Desktop Power-User Workspace Guide

> [!NOTE]
> **Status: Active Development**
> MP3MetaManager includes the integrated single-track editor, Synced Lyrics / Tap-to-Sync studio, persistent Track Workspaces, and separated stem upload, mixing preview, and ZIP download. Batch spreadsheet tagging and the universal raw-frame inspector remain planned.

---

## 🎯 Purpose & Scope

**MP3MetaManager (`/manager`)** is the desktop-focused power-user interface engineered for large audio libraries, multi-file batch operations, album curation, and deep low-level metadata inspection.

As an architectural standard, **MP3MetaManager is a functional superset of `/app`**: all capabilities available in `/app` are directly accessible inside `/manager` through sidebar routing and shared backend engines, alongside advanced power-user workflows.

---

## ✨ Implemented Capabilities & Features

### 1. Integrated MP3MetaFix Editor (`/manager` Sidebar → `MP3Metafix`)
- **Full Tagging Suite**: In-place single-track audio editing for Title, Artist, Album, Album Artist, Genre, Year, Track/Disc numbers, BPM, Composer, Comments, and Unsynchronized Lyrics (`USLT`).
- **Cover Artwork Studio**: Drag-and-drop replacement, preview, format normalization (JPEG/PNG/WebP), and removal.
- **Audio Waveform Player**: Real-time waveform scrubbing, playback controls, and duration displays.
- **Suno AI Sync & Canned Presets**: Watermark-preserving Suno metadata extraction and configurable canned comment presets.

### 2. Synced Lyrics / SYLT & Tap-to-Sync Studio (`/manager` Sidebar → `Synced Lyrics / SYLT`)
- **Interactive Tap-to-Sync Timing**:
  - Live timestamping (<kbd>Space</kbd> or on-screen button) synchronized with audio playback.
  - **Smart Auto-Intro Timing**: Song-opening `[Intro]` tags automatically receive `00:00.00` timestamps upon start.
  - **Auto-Section Header Timing**: Tapping the first vocal line of a section (e.g. `[Verse 1]`, `[Chorus]`, `[Bridge]`) automatically timestamps the preceding section header with a $500\text{ms}$ lead-in reduction so non-singable cues are timed without manual effort.
  - **Selected Line Nudging & Navigation**:
    - Select any lyric row and click **`-100ms`** / **`+100ms`** (or press <kbd>[</kbd> / <kbd>]</kbd> / <kbd>-</kbd> / <kbd>+</kbd>) to nudge timing with real-time toast feedback.
    - Navigate rows seamlessly with <kbd>↑</kbd> and <kbd>↓</kbd> arrow keys.
    - Step-back button (<kbd>Backspace</kbd>) reverts the last recorded stamp and auto-clears any paired section header.
- **Structural Tag Classification & Strip Mode**:
  - Automatically identifies bracketed section tags (e.g., `[Intro]`, `[Verse 1]`, `[Chorus]`, `[Guitar Solo]`, `[Outro]`) as styled visual pill badges.
  - **"Strip Section Tags" Toggle**: Completely excludes non-singable tags from the stamping queue, hero counter, and exported lyrics for pure vocal timing.
- **Live Karaoke Preview Visualizer**:
  - Real-time karaoke tracking with active-line spotlighting, previous-line fade, and upcoming line preview.
  - Dedicated instrumental break cues during musical solos and intros.
- **Direct Save & File Conversion**:
  - **Save SYLT to Audio**: Atomically embeds native ID3 `SYLT` (Synchronized Lyrics/Text, millisecond format) frames directly into the MP3 file.
  - **Import & Export `.LRC`**: Full compatibility with standard `.lrc` companion files for external media players.
  - **Pull Track Lyrics**: Instantly imports existing unsynchronized lyrics (`USLT`) into the sync queue.

---

### 3. Track Workspaces and Stems Manager

- **Track Workspaces**: Create a persistent song workspace, upload multiple Takes, designate a master Take, stream or download stored audio, and load a Take into the integrated editor. **Save to Project** can save a new Take or update the loaded Take; check the selected destination before saving.
- **Stems Manager**: Select a Track and Take, batch-upload separated stem files, review detected roles, and preview stems together using mute, solo, per-stem volume, master volume, and seek controls. Download individual stems or the Take's Stem Pack ZIP.
- These tools organize supplied recordings and stems; they do not perform automatic stem separation. Album release workflows are described in the [MP3Projects guide](/docs/#projects).

## 📋 Backlog & Future Vision

### 3. High-Density Spreadsheet Batch Editor
- Multi-column spreadsheet grid with full keyboard navigation (<kbd>Tab</kbd>, <kbd>Enter</kbd>, arrow keys).
- Multi-row selection to batch-apply common Artist, Album, Genre, Year, or Cover Art across hundreds of files.
- Regex search-and-replace across filenames and metadata tags.
- Sequential auto-numbering and drag-and-drop track reordering.

### 4. Universal ID3 Frame & Raw Metadata Inspector
- **Comprehensive Frame Manager**: View, add, edit, and delete standard and specialized ID3v2.3 / ID3v2.4 frames (`TXXX`, `COMM`, `USLT`, `SYLT`, `ETCO`, `APIC`, `PRIV`, `GEOB`, `UFID`, `POPM`, `RVA2`).
- **MPEG & Container Byte Inspector**: MPEG audio layers, VBR headers, ID3 flags, and raw byte hex payloads.

### 5. Additional Stem Automation & AI Generation Tree
- Shared metadata stamping and additional DAW-oriented packaging remain planned beyond the existing stem upload, mixer preview, and ZIP download tools.
- A visual parent/child lineage graph for variations and extensions remains planned.

### 6. Custom Lyric Video Studio & MP4 Generator (`.mp4`)
- Generate vertical (9:16) and widescreen (16:9) animated lyric videos from audio + SYLT/LRC lyrics.

---

*For roadmap progress and implementation milestones, see the [Master Product Roadmap](../../ROADMAP.md#-project-c-mp3metamanager-manager--desktop-power-user-workspace-roadmap).*
