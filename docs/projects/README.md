# MP3Projects Studio (`/projects`) — Multi-Track Project & Album Guide

> [!NOTE]
> **Status: Under Active Development (Specification & Planning Phase)**  
> This workspace is currently in the planning and specification phase. The dispatcher card on `/` is gated and will be introduced with configurable admin access controls.

---

## 🎯 Purpose & Scope

**MP3Projects (`/projects`)** is a dedicated studio workspace designed for grouping, sequencing, and packaging multi-track releases (EPs, full-length LPs, single bundles with stems, podcast series) under a unified persistent project model.

Unlike single-track editing (`/app`) or broad directory batch processing (`/manager`), MP3Projects treats a collection of audio files as a **single cohesive release project**.

---

## 📋 Planned Capabilities & Workflow

### 1. Project-Centric Audio Bundling
- Group multiple audio files into a named project container.
- Establish shared release metadata (Album Name, Primary Artist, Release Date, Publisher/Label, Copyright, Common Genre).
- Unified high-resolution album cover art management across the entire tracklist.

### 2. Track Sequencing & Numbering
- Drag-and-drop track reordering with automatic track number stamping (`1/10`, `2/10`, etc.) and disc assignment.
- Track-specific metadata overrides (individual track titles, featured guest artists, custom lyricists).

### 3. Stem Bundle & Multi-Track Packaging
- Manage multi-track stem sets (Vocals, Instrumental, Stems) linked to individual master tracks.
- Automatic naming and metadata inheritance across stem variations.

### 4. Persistent Project Sessions
- Save project state into session definitions to resume curation across browser restarts without losing staging edits.
- Multi-user and tenant ownership readiness for collaborative release assembly.

### 5. Mastering & Distribution Export
- One-click export to structured `.zip` archives containing sequenced, tagged audio files.
- Companion cue sheet (`.cue`) generation and formatted tracklist text exports.

---

*For roadmap progress, see the [Master Product Roadmap](../../ROADMAP.md#%EF%B8%8F-project-d-mp3projects-projects--multi-track-project--album-studio-roadmap).*
