# MP3MetaFix Future Implementation & Roadmap

This document serves as the living backlog of ideas, enhancements, feature proposals, and architectural extensions for **MP3MetaFix**.

Items are grouped by focus area and can be prioritized into structured version milestones as needs evolve.

---

## 🎵 1. Suno.com AI Music Workflow & Tag Automation (Primary Focus)

Because MP3MetaFix is tailored for processing AI-generated music downloads from platforms like [Suno.com](https://suno.com), specialized workflow enhancements can streamline tagging and organization:

- [ ] **Suno Metadata Auto-Parser**:
  - Paste a Suno track URL, share link, or prompt text box to automatically parse and populate:
    - Track Title & Subtitle / Variation name (e.g., *Full Song*, *Part 2*, *Extended*)
    - Generated Lyrics / Structure tags (`[Verse 1]`, `[Chorus]`, `[Guitar Solo]`, `[Outro]`) into the ID3 `USLT` unsynchronized lyrics frame.
    - Style / Prompt tags (e.g. *80s synthwave, female vocals, melancholic*) into Genre and Comments.
- [ ] **Suno Artwork Importer**:
  - Direct import or drag-and-drop of high-resolution Suno track thumbnail artwork without manual downloading and resizing.
- [ ] **AI Music Tagging Presets**:
  - Pre-configured tagging templates tailored for AI generations:
    - Model/Engine tags in comment frame (e.g. `Generated with Suno v3.5 / v4`).
    - Stems & Instrumental flags (`[Instrumental]`, `[Vocals Only]`).
    - Prompt & Style preservation in custom ID3 `TXXX:PROMPT` or `COMM` frames.
- [ ] **Dynamic Batch Filename Rules for Suno**:
  - Pattern presets like `%artist% - %title% (%style%) [%model%].mp3`.

---

## 🪟 2. Windows Cross-Platform Support

A seamless Windows desktop and server experience without requiring WSL:

- [ ] **Windows Standalone Scripts**:
  - `run.bat` / `run.ps1` and `install.bat` that set up a Python virtual environment, install requirements, launch the FastAPI server, and open the default browser.
- [ ] **Standalone Windows Executable (`.exe`)**:
  - Bundled standalone binary using **PyInstaller** or **PyWebView** (Microsoft Edge WebView2 backend) for a single-file portable desktop app that runs without needing Python installed on the host.
  - Windows system tray icon with quick start/stop and "Open Web Interface" actions.
- [ ] **Windows Service Support**:
  - Service configuration via **WinSW** or **NSSM** for running in the background on Windows Server instances.

---

## 📦 3. Batch Processing & Queue Management

- [ ] **Multi-File Upload & Queue**:
  - Drag and drop dozens of MP3 files at once.
  - Batch table view with inline editing for titles, track numbers, and shared album metadata.
- [ ] **Bulk Metadata Applicator**:
  - Apply common Album, Artist, Genre, Year, and Cover Art across all files in the active queue with a single click.
- [ ] **Sequential Auto-Numbering**:
  - Auto-increment track numbers (`1/10`, `2/10`, ...) based on custom sorting or queue ordering.
- [ ] **Bulk ZIP / Archive Download**:
  - Download all modified tracks in the session as a structured `.zip` archive or directly write back to a chosen local directory via File System Access API.

---

## 🎧 4. Multi-Format Audio Expansion

Expand the underlying audio engine beyond MP3:

- [ ] **FLAC (Free Lossless Audio Codec)**: Vorbis comment tagging and FLAC picture block embedding.
- [ ] **M4A / AAC / ALAC (MP4 Container)**: iTunes-style atom metadata (`©nam`, `©ART`, `covr`).
- [ ] **OGG / Opus**: Vorbis comment tagging.
- [ ] **WAV / AIFF**: ID3 chunk and RIFF INFO list metadata.

---

## ✂️ 5. Audio Utilities & Editing Tools

- [ ] **Lossless Silence Trimming**:
  - Auto-detect and trim lead-in and tail silence commonly present on AI-generated audio without re-encoding the audio stream.
- [ ] **Audio Fading (Fade-in / Fade-out)**:
  - Smooth 1–3 second fade-out tool for abrupt endings in generated tracks.
- [ ] **Volume Normalization / ReplayGain**:
  - Calculate and write standard ReplayGain / EBU R128 volume tags.

---

## 🌐 6. Integrations, Packaging & Deployment

- [ ] **Official Docker & OCI Container Images**:
  - Multi-arch Dockerfile (`linux/amd64`, `linux/arm64`) published to GitHub Container Registry (GHCR) for unRAID, TrueNAS, and Kubernetes deployment.
- [ ] **External Metadata Lookup**:
  - Optional MusicBrainz / AcoustID fingerprinting for commercial and remix identification.
- [ ] **PWA (Progressive Web App)**:
  - Offline capability and installation as a standalone progressive desktop app.

---

*Note: Feel free to add, remove, or re-order ideas in this file as project priorities shift.*
