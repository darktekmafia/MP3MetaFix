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

## 🔄 2. In-App Web Updater & Version Inspector

Provide a complete in-app lifecycle updater directly from the web interface, leveraging `install.sh --update --headless` and GitHub API:

- [ ] **Automated Background Update Checker**:
  - Background version check on web UI page load comparing local `VERSION` against latest GitHub release/tag.
  - Manual **"Check for Updates"** button in Settings / Header.
  - **"Update Available"** badge in the navbar when a new version is detected.
- [ ] **Version Details & Release Notes Inspector**:
  - **Currently Installed Details**: View active version, build date, Git commit hash, and running mode (Desktop launcher vs Systemd service).
  - **New Version Preview**: Multi-line changelog and release notes modal displaying additions, fixes, and non-breaking/breaking change flags before updating.
- [ ] **In-Browser Update Execution & Live Console Stream**:
  - **"Update Now"** action triggering `install.sh --update --headless` securely on the server.
  - Live console modal streaming stdout/stderr in real-time (via Server-Sent Events or WebSocket) to monitor git pull, dependency upgrades, and service restart.
  - Safety guards: Disable update trigger while an active MP3 session is being edited.
- [ ] **Auto-Reconnection & Refresh Prompt**:
  - Automated client-side healthcheck polling (`/api/health`) as `install.sh` restarts the systemd service.
  - Success banner prompting the user to reload the page once the new version is verified online.

---

## 🪟 3. Windows Cross-Platform Support

A seamless Windows desktop and server experience without requiring WSL:

- [ ] **Windows Standalone Scripts**:
  - `run.bat` / `run.ps1` and `install.bat` that set up a Python virtual environment, install requirements, launch the FastAPI server, and open the default browser.
- [ ] **Standalone Windows Executable (`.exe`)**:
  - Bundled standalone binary using **PyInstaller** or **PyWebView** (Microsoft Edge WebView2 backend) for a single-file portable desktop app that runs without needing Python installed on the host.
  - Windows system tray icon with quick start/stop and "Open Web Interface" actions.
- [ ] **Windows Service Support**:
  - Service configuration via **WinSW** or **NSSM** for running in the background on Windows Server instances.

---

## 📦 4. Batch Processing & Queue Management

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

## 🎧 5. Multi-Format Audio Expansion

Expand the underlying audio engine beyond MP3:

- [ ] **FLAC (Free Lossless Audio Codec)**: Vorbis comment tagging and FLAC picture block embedding.
- [ ] **M4A / AAC / ALAC (MP4 Container)**: iTunes-style atom metadata (`©nam`, `©ART`, `covr`).
- [ ] **OGG / Opus**: Vorbis comment tagging.
- [ ] **WAV / AIFF**: ID3 chunk and RIFF INFO list metadata.

---

## ✂️ 6. Audio Utilities & Editing Tools

- [ ] **Lossless Silence Trimming**:
  - Auto-detect and trim lead-in and tail silence commonly present on AI-generated audio without re-encoding the audio stream.
- [ ] **Audio Fading (Fade-in / Fade-out)**:
  - Smooth 1–3 second fade-out tool for abrupt endings in generated tracks.
- [ ] **Volume Normalization / ReplayGain**:
  - Calculate and write standard ReplayGain / EBU R128 volume tags.

---

## 🎨 7. UI/UX, Themes & Personalization

- [ ] **Light Mode & Dark Mode Support**:
  - Theme toggle switch in header/settings (Dark, Light, System Default).
  - Clean, high-contrast light mode palette tailored with subtle glassmorphic cards, crisp borders, and modern typography.
  - Persistent user preference saved in `localStorage`.
  - Automatic synchronization with OS `prefers-color-scheme` media queries.
  - Smooth CSS variable transitions between themes without jarring color jumps or reload flashes.
- [ ] **Mobile-First Responsive Design & Touch Optimization (Suno On-the-Go)**:
  - **Adaptive Viewport Layouts**: Fluid single-column responsive stacking for smartphones and tablets (`< 768px`), collapsible artwork manager, and compact metadata cards.
  - **Sticky Mobile Bottom Action Bar**: Persistent bottom bar housing the Save/Download action and mini audio playback controls for seamless one-thumb operation while editing long lyrics or metadata fields.
  - **Touch & Mobile File Pickers**: Enlarged 48px tap targets, mobile file drawer integration, and direct photo library picker for album art.
  - **Native Web Share API (`navigator.share`)**: One-tap export to send fixed MP3s directly to mobile music players (VLC, Files, Telegram, Discord, audio apps) on Android and iOS.
  - **MediaSession API Integration**: Display track title, artist, album art, and seek controls in the mobile lockscreen and notification shade during audio preview.
- [ ] **UI Density & Customization**:
  - Compact table view vs spacious editing panel mode.
  - Customizable default landing tab (e.g. Essential vs Advanced tags vs Lyrics).

---

## 🌐 8. Integrations, Packaging & Deployment

- [ ] **Official Docker & OCI Container Images**:
  - Multi-arch Dockerfile (`linux/amd64`, `linux/arm64`) published to GitHub Container Registry (GHCR) for unRAID, TrueNAS, and Kubernetes deployment.
- [ ] **External Metadata Lookup**:
  - Optional MusicBrainz / AcoustID fingerprinting for commercial and remix identification.
- [ ] **PWA (Progressive Web App)**:
  - Offline capability and installation as a standalone progressive desktop app.

---

*Note: Feel free to add, remove, or re-order ideas in this file as project priorities shift.*
