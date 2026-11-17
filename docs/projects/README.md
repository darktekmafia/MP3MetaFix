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

3. **Album / EP Releases (`alb_...`) & Audio Ownership**:
   - Release containers for EPs, LPs, single bundles, and compilations, supporting both **Draft** and **Finalized** states.
   - **Independent Owned Copies**: Assigning a Take to an Album entry copies its audio and metadata snapshot into the Album's own storage directory. Editing or deleting a source Take does not automatically modify existing Album recordings; updating from a source Take requires an explicit **Refresh** (`Compare & Refresh from Source Take` -> `Confirm & Refresh Copy`).
   - **Draft Album Workflows**: Add entries (including multiple Takes from the same Track), replace an entry's recording with another Take, refresh an entry from its source Take, remove entries, reorder/assign discs, replace master cover artwork, and delete entire draft Albums (without deleting source Tracks, source Takes, or other Albums).
   - **Finalization**: Finalizing an Album validates all active and retained artifacts and permanently locks the release against further edits or deletion.
   - **Album Entry Preview**: Each entry with owned audio provides a compact Play/Pause (`▶` / `⏸`) preview button using the shared audio player and the Album-owned stream endpoint. Previewing is intentionally simple and read-only, with no Album-specific waveform, seekbar, duration display, playlist, or autoplay.

4. **Revision Tracking vs. Takes**:
   - **Takes**: Reserved strictly for actual audio changes (recording takes, tempo alterations, stem replacements, alternate mixes/masters).
   - **Revisions**: Non-audio changes (artwork replacements, tag and title modifications, credits updates, musical key/BPM adjustments, synchronized lyrics edits, or album tracklist re-sequencing) are tracked as **revisions** (`Rev 1`, `Rev 2`, etc.) with timestamped changelogs rather than duplicating audio files as new takes.

5. **Two-Way Editor Ingestion & Promotion**:
   - Save directly from the embedded editors within `/manager` and `/projects` into a Track Workspace via **Save to Project** (`POST /api/projects/ingest-session`). The standalone `/app` Editor provides temporary session editing and file download, without a **Save to Project** control.
   - Promote any stored take into an active editor session with one click (`POST /api/projects/tracks/{id}/takes/{id}/load-session`).

---

## Working with Tracks, Takes, and Albums

### Create and edit a draft Album

1. Create a Track Workspace and upload one or more Takes. A Track groups versions of a song; a Take holds a particular recording. Selecting a master Take does not replace existing Album copies.
2. Create an Album, EP, Single, or Compilation, then open its details. Edit release metadata, add entries, reorder them with the sequencer controls, and assign discs.
3. Choose a Track and a specific Take for each recorded entry. Multiple Takes from one Track can occupy separate entries. Each entry has its own identity, copied recording, and metadata snapshot.
4. A placeholder reserves a position without audio. Assign a Take before finalization. Removing a placeholder removes only that entry.

Album-owned copies remain independent if source Tracks or Takes are edited or deleted. Album entry downloads and previews use the Album copy, not the current source Take.

### Replace, refresh, or remove an entry

- **Replace recording**: Choose another Take for the selected draft entry and confirm the replacement. Other entries are unchanged. Previous Album-owned audio and snapshots are retained; replacement does not overwrite their bytes.
- **Refresh from source**: Open **Compare & Refresh from Source Take**, inspect audio, lyrics, and metadata differences, then choose **Confirm & Refresh Copy**. The confirmation is tied to the previewed source evidence and Album revision. If either changes, obtain a fresh preview. A refresh with no changes does not create a new recording generation or Album revision.
- **Remove entry**: Confirm removal from the draft tracklist. Remaining entries are renumbered and keep their identities and recordings. Removed recordings and prior generations remain retained in the Album's storage; source Tracks and Takes are not deleted.
- Clearing an occupied entry back to a placeholder is still disabled. Use explicit replacement or removal instead.

### Artwork, finalization, and deletion

- Upload artwork in the draft Album details. Uploading another image replaces the active cover while retaining previous cover files. The artwork endpoint displays the active cover; recording entries are unchanged.
- **Finalize** only when the sequence, recordings, snapshots, and artwork are ready. The Album must have entries with owned recordings rather than placeholders. Validation checks active and retained artifacts; missing, unexplained, or tampered files can prevent finalization. Legitimately retained recordings and covers do not prevent it.
- A finalized Album remains readable, downloadable, and previewable, but cannot be edited or deleted. There is no unfinalize control.
- **Delete draft Album** removes the Album and its Album-owned files, including retained copies. It does not delete source Tracks, source Takes, or sibling Albums. Treat this as permanent deletion, not entry removal or an undoable archive. Interrupted cleanup can be resumed through deletion retry; an already committed deletion is hidden from normal Album lists.

### Play/Pause entry preview

Click **Play** on a recorded entry to hear its exact Album-owned recording. **Pause** stops at the current position and Play resumes it. Selecting another entry discards the previous entry's position. Playback ending resets the button; it does not advance automatically. Placeholders have no playable preview.

Draft and finalized Albums both support preview. The preview shares the existing audio player with Take playback; switching to a Take releases the Album preview. Closing Album details stops its own preview without stopping unrelated Take playback. There is no separate Album waveform, seekbar, duration display, playlist, or autoplay. Playback is read-only and does not advance Album revisions.

### Workflow limits and failure messages

- Existing-Album writes require the revision shown when the action began. A stale-revision conflict means reload the Album and review the intended change before retrying. Refresh also requires a fresh comparison when its confirmed source has changed.
- Metadata/reorder changes, recording replacement, explicit source refresh, recorded-entry removal, artwork upload/replacement, finalization, and draft deletion use the v2 journal. Entry addition and initial Take assignment also use it on already-enrolled v2 Albums. On journal-less Albums, entry addition, initial Take assignment and other non-replacement entry edits, and placeholder removal retain their existing direct manifest-publication paths. Those paths do not write v2 transaction receipts and are not covered by the v2 recovery coordinator; their recovery guarantees must not be treated as identical to journaled operations. Legacy v1 journals are not automatically upgraded. An unresolved transaction or incompatible journal can block further writes.
- A **durability uncertainty** response does not prove that a write failed or succeeded. Preserve the Album data and transaction evidence; do not manually delete generations or repeatedly submit replacements to work around it. Explicit storage recovery is separate from normal reads and is not an in-app automatic repair workflow.
- Retained generations consume storage quota and are not an in-app undo/version-restore feature. Keep independent backups. Linux is the primary target; application safeguards are not a guarantee against hardware failure, external filesystem changes, or every power-loss condition.
- Album-wide release ZIP/cue-sheet export remains planned. Individual Album entry downloads are available now.

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
| `GET` | `/api/projects/albums/{id}` | Get album details, entries, and revision history |
| `PATCH` | `/api/projects/albums/{id}` | Update album details and tracklist sequence |
| `DELETE` | `/api/projects/albums/{id}` | Delete a draft album and its Album-owned files (`?expected_revision=...`) |
| `POST` | `/api/projects/albums/{id}/finalize` | Validate and permanently finalize a draft album (`?expected_revision=...`) |
| `POST` | `/api/projects/albums/{id}/entries` | Add a track entry (with optional Take copy) to a draft album |
| `PATCH` | `/api/projects/albums/{id}/entries/{entry_id}` | Update entry details or replace its recording with a Take |
| `DELETE` | `/api/projects/albums/{id}/entries/{entry_id}` | Remove an entry from a draft album (`?expected_revision=...`) |
| `GET` | `/api/projects/albums/{id}/entries/{entry_id}/refresh-preview` | Compare Album-owned copy against current source Take |
| `POST` | `/api/projects/albums/{id}/entries/{entry_id}/refresh` | Confirm explicit refresh of an entry from its source Take |
| `GET` | `/api/projects/albums/{id}/entries/{entry_id}/stream` | Stream Album-owned entry audio (HTTP 206 Partial Content) |
| `GET` | `/api/projects/albums/{id}/entries/{entry_id}/download` | Download Album-owned entry audio |
| `POST` | `/api/projects/albums/{id}/artwork` | Upload or replace master album cover artwork |
| `GET` | `/api/projects/albums/{id}/artwork` | Stream active album cover artwork |
| `GET` | `/api/storage/quota` | Get tenant storage quota utilization stats |
