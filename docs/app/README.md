# MP3MetaFix Editor (`/app`) — User & Feature Guide

**MP3MetaFix (`/app`)** is the lightweight, mobile-first single-track audio metadata, artwork, and inspection workspace. It is designed for fast, friction-free music tagging on smartphones, tablets, and desktop browsers alike.

---

## 🎧 Supported Audio Formats & Preservation

MP3MetaFix processes audio files in-place without re-encoding the underlying audio stream, ensuring **zero generation loss**:

| Format | Audio Codecs | Supported Metadata & Tags | Artwork Support |
|---|---|---|---|
| **MP3** (`.mp3`) | MPEG Layer III | ID3v2.3 / ID3v2.4 (Title, Artist, Album, Genre, Year, Track, BPM, Comments, Lyrics) | Embedded `APIC` (Front Cover, PNG/JPEG/WebP normalized) |
| **M4A / MP4** (`.m4a`) | AAC, ALAC, Opus | Native MP4 Atoms (`©nam`, `©ART`, `©alb`, `©gen`, `©day`, `trkn`, `disk`, `tmpo`, `©cmt`, `©lyr`) | Embedded `covr` atom (JPEG/PNG) |
| **WAV** (`.wav`) | Lossless PCM | Native ID3 chunk with synchronized RIFF `INFO` text tags (`INAM`, `IART`, `IPRD`, `ICMT`) | Embedded ID3 `APIC` |

> [!NOTE]
> When editing and downloading audio files, the original container and audio format are strictly preserved.

---

## ✨ Core Features & Workflow

### 1. Tagging & Metadata Editor
- **Full Tag Suite**: Track Title, Artist, Album, Album Artist, Genre, Year / Date, Track Number / Total Tracks, Disc Number / Total Discs, BPM, Composer, Comments, and Lyrics.
- **Dynamic Filename Formatter**: One-click pattern-based renaming (`%artist% - %title%.mp3`, `%track% - %title%.mp3`, etc.) applied automatically upon download.
- **Canned Comment Presets**: Quick-select preset dropdown to easily stamp creator profiles, social links, mastering notes, or copyright markers. Create, edit, and delete custom presets via the built-in Presets Manager.

### 2. Album Artwork Studio
- **Inspect & Extract**: View embedded cover art and download original high-resolution image files.
- **Replace & Normalize**: Drag and drop any replacement image (PNG, JPEG, or WebP). Uploaded images are checked against decompression bomb thresholds (`MAX_IMAGE_PIXELS = 10,000,000`, max 4096×4096px) and normalized cleanly.
- **Remove Artwork**: Clear embedded artwork with a single click.

### 3. Built-In Audio Preview & Waveform Scrubber
- **Interactive Retina Waveform**: Real-time Web Audio peak visualization with dynamic playback progress gradients.
- **Precision Scrubbing**: Continuous drag-to-seek, hover time tooltips, and keyboard shortcuts (<kbd>Space</kbd> to play/pause).
- **HTTP 206 Streaming**: Seamless partial content streaming for instant audio playback without waiting for whole-file downloads.

### 4. Suno.com AI Metadata Extraction & Selective Merge
When working with songs created on [Suno.com](https://suno.com):
- **Embedded Tag Recognition**: Automatically detects Suno clip UUIDs in uploaded files and displays a non-disruptive `✨ Suno Detected` status pill.
- **Zero Blind Overwrites (Selective Merge Modal)**: Interactively inspect incoming tags against existing file data with clear field-by-field diffs. Choose from `Apply Selected`, `Fill Blank Only`, or `Apply All` presets.
- **URL & Share Link Parsing**: Paste any Suno track link to fetch Title, Creator handle, Musical Style genre tags, structured lyrics with section markers, and 1024×1024 artwork.
- *Note: Suno AI integration can be enabled or disabled by the administrator in `/admin`.*

---

## 📱 Mobile-First Touch Design

- **Responsive Viewports**: Tested from narrow mobile screens ($\ge 320\text{px}$) to 4K displays with zero horizontal overflow.
- **Touch-Friendly Controls**: Touch targets meet or exceed $44\times 44\text{px}$ for comfortable one-thumb editing.
- **Constrained Layouts**: Album art previews and form grids adapt dynamically to prevent massive vertical scrolling on smartphones.
