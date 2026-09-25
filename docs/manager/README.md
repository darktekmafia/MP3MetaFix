# MP3MetaManager (`/manager`) — Desktop Power-User Workspace Guide

> [!NOTE]
> **Status: Under Active Development (Workspace Shell Implemented)**  
> The current version includes the multi-panel desktop layout, header navigation, and feature routing. The integrated single-track inspector, batch spreadsheet, raw ID3 frame editor, and stem bundler are in development.

---

## 🎯 Purpose & Scope

**MP3MetaManager (`/manager`)** is the desktop-focused power-user interface engineered for large audio libraries, multi-file batch operations, album curation, and deep low-level metadata inspection.

As an architectural standard, **MP3MetaManager is a functional superset of `/app`**: once fully implemented, every capability available in `/app` will be accessible directly inside `/manager` through shared backend services and an integrated detail inspector.

---

## 📋 Planned Capabilities & Features

### 1. Integrated Single-Track Inspector
- Collapsible detail drawer allowing comprehensive single-track editing without leaving the batch queue.
- Live waveform preview, artwork management, canned comment presets, and Suno AI extraction.

### 2. High-Density Spreadsheet Batch Editor
- Multi-column spreadsheet grid with full keyboard navigation (<kbd>Tab</kbd>, <kbd>Enter</kbd>, arrow keys).
- Multi-row selection to batch-apply common Artist, Album, Genre, Year, or Cover Art across hundreds of files.
- Regex search-and-replace across filenames and metadata tags.
- Sequential auto-numbering and drag-and-drop track reordering.

### 3. Universal ID3 Frame & Raw Metadata Inspector
- **Comprehensive Frame Manager**: View, add, edit, and delete standard and specialized ID3v2.3 / ID3v2.4 frames:
  - Text frames (`TIT1`–`TIT3`, `TPE1`–`TPE4`, `TALB`, `TCOM`, `TCON`, `TDRC`, `TBPM`, `TKEY`, `TSRC`, etc.)
  - User-defined extended text frames (`TXXX`)
  - Multi-language comments (`COMM`) and unsynchronized lyrics (`USLT`)
  - Synchronized lyrics (`SYLT`) and event timing codes (`ETCO`)
  - Multi-picture APIC manager (Front Cover, Back Cover, Media/CD, Artist Lead)
  - Specialized binary frames (`PRIV`, `GEOB`, `UFID`, `POPM`, `RVA2`)
- **MPEG & Container Byte Inspector**: Inspect MPEG audio layers, VBR headers, ID3 header flags, and raw byte hex payloads.

### 4. Stem Pack Bundler & AI Generation Tree
- Ingest separated stems (Vocals, Instrumental, Bass, Drums) and stamp shared track metadata with role identifiers.
- Package into DAW-ready directories or structured `.zip` archives.
- Visual parent/child lineage graph for variations and extensions.

### 5. Tap-to-Sync Karaoke Lyric Stamping (`.lrc` & `SYLT`)
- Interactive tap-to-sync tool stamping precise millisecond timestamps onto lyric lines during audio playback.
- Export standard `.lrc` companion files or embed directly into audio tags.

---

*For roadmap progress and implementation milestones, see the [Master Product Roadmap](../../ROADMAP.md#-project-c-mp3metamanager-manager--desktop-power-user-workspace-roadmap).*
