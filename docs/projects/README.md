# MP3MetaProjects Studio (`/projects`) — Multi-Track Project & Album Guide

MP3MetaProjects (`/projects`) is a dedicated studio workspace designed for organizing multi-take audio workflows, separated stems, synchronized lyrics versions, and sequencing multi-track Album/EP releases under a persistent, tenant-isolated storage model.

---

## 🎯 Architecture & Core Concepts

Unlike single-track quick tagging (`/app`) or directory batch management (`/manager`), MP3MetaProjects manages persistent user-owned audio libraries:

1. **Track Workspaces (`trk_...`)**:
   - Each track acts as a dedicated workspace for a song.
   - Encloses multiple **Takes (`tak_...`)**, allowing creators to track multiple mix iterations, AI model generations (e.g. Suno v3.5 vs v4.0), and recording takes.
   - Features **Master Take Designation (`is_master`)** to identify the definitive version for releases.
   - Holds AI generation prompts, style tags, and production revision notes.

2. **Separated Stems Rack (`stm_...`)**:
   - Audio takes support attached stem separations with designated roles: `vocals`, `instrumental`, `drums`, `bass`, `guitar`, `synth`, `fx`, `backing_vocals`, and `other`.
   - Ingests MP3, M4A, and WAV stems with independent HTTP 206 streaming and download capabilities.

3. **Album / EP Releases (`alb_...`)**:
   - Release containers for EPs, LPs, single bundles, and compilations.
   - Master album artwork upload, normalization, and display.
   - Interactive tracklist sequencer: add tracks from workspaces, reorder positions (`▲ Up` / `▼ Down`), and configure disc assignments.
   - Release metadata (Album Artist, Year, Genre, Catalog Number, Credits & Liner Notes).

4. **Revision Tracking vs. Takes**:
   - **Takes**: Reserved strictly for actual audio changes (recording takes, tempo alterations, stem replacements, alternate mixes/masters).
   - **Revisions**: Non-audio changes (artwork replacements, tag and title modifications, credits updates, musical key/BPM adjustments, synchronized lyrics edits, or album tracklist re-sequencing) are tracked as **revisions** (`Rev 1`, `Rev 2`, etc.) with timestamped changelogs rather than duplicating audio files as new takes.

5. **Two-Way Editor Ingestion & Promotion**:
   - Save directly from single-track editor sessions (`/app` and `/manager`) into any track workspace via `POST /api/projects/ingest-session`.
   - Promote any stored take into an active editor session with one click (`POST /api/projects/tracks/{id}/takes/{id}/load-session`).

---

## 🔒 Security & Tenant Isolation

- **POSIX `0700` File Permissions**: All storage directories (`data/storage/tenants/`) are created with strict `rwx------` permissions.
- **Decoupled Hashed Filesystem Storage**: Internal folder paths use opaque directory hashes `SHA-256("tenant_storage:" + secret + ":" + user_id)[:32]` to ensure absolute filesystem isolation with zero raw ID exposure.
- **Quota Accounting & Defense**: Live pre-flight file size checks enforce tenant storage limits (`/api/storage/quota`) before persistence.
- **Chunk-Level Magic Byte Validation**: Audio uploads are validated via binary header inspection before persistence.

---

## 🛠️ API Reference Summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/projects/tracks` | List track workspaces (optional status filter) |
| `POST` | `/api/projects/tracks` | Create a new track workspace |
| `GET` | `/api/projects/tracks/{id}` | Get track workspace with takes, stems & lyrics |
| `PATCH` | `/api/projects/tracks/{id}` | Update track details or status |
| `DELETE` | `/api/projects/tracks/{id}` | Delete track workspace and enclosed takes |
| `POST` | `/api/projects/tracks/{id}/takes` | Upload audio take (MP3, M4A, WAV) |
| `GET` | `/api/projects/tracks/{id}/takes/{take_id}/stream` | Stream take audio (HTTP 206 Partial Content) |
| `GET` | `/api/projects/tracks/{id}/takes/{take_id}/download` | Download take audio with RFC 5987 filename |
| `POST` | `/api/projects/tracks/{id}/takes/{take_id}/stems` | Upload separated audio stem |
| `GET` | `/api/projects/tracks/{id}/takes/{take_id}/stems/{stem_id}/stream` | Stream stem audio (HTTP 206 Partial Content) |
| `GET` | `/api/projects/tracks/{id}/takes/{take_id}/stems/{stem_id}/download` | Download individual stem audio file |
| `DELETE` | `/api/projects/tracks/{id}/takes/{take_id}/stems/{stem_id}` | Delete separated stem |
| `GET` | `/api/projects/tracks/{id}/takes/{take_id}/stems/download-pack` | Export all take stems as a ZIP archive |
| `POST` | `/api/projects/tracks/{id}/takes/{take_id}/load-session` | Promote take into active editing session |
| `GET` | `/api/projects/albums` | List album releases |
| `POST` | `/api/projects/albums` | Create album release project |
| `PATCH` | `/api/projects/albums/{id}` | Update album details and tracklist sequence |
| `POST` | `/api/projects/albums/{id}/artwork` | Upload master album cover artwork |
| `GET` | `/api/storage/quota` | Get tenant storage quota utilization stats |
