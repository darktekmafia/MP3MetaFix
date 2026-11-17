"""Unit and integration tests for the persistent File & Project Storage System (Option A)."""

import os
import stat
import pytest
from pathlib import Path

from backend.config import STORAGE_DIR
from backend.security import (
    generate_track_id,
    generate_take_id,
    generate_stem_id,
    generate_album_id,
    generate_album_entry_id,
    generate_deterministic_legacy_entry_id,
    is_valid_project_id,
    get_tenant_storage_dir_name,
    get_track_dir_name,
    get_take_dir_name,
    get_album_dir_name,
    get_album_entry_dir_name,
)
from backend.project_models import (
    WorkspaceStatus,
    StemRole,
    LyricsFormat,
    AlbumTrackEntry,
)
from backend.project_storage import (
    ProjectStorageManager,
    ProjectStorageError,
    ProjectStorageQuotaExceeded,
    ProjectValidationError,
    AlbumFinalizedError,
    AlbumReplacementDisabledError,
    AlbumRevisionConflictError,
    AlbumManifestCorruptError,
    AlbumRefreshConflictError,
    AlbumFinalizationError,
)


@pytest.fixture
def custom_storage(tmp_path):
    """Provide isolated ProjectStorageManager on a temporary directory."""
    return ProjectStorageManager(root_dir=tmp_path / "tenants")


def test_opaque_id_generation_and_validation():
    """Verify format, prefix, entropy, and rejection of invalid identifiers."""
    track_id = generate_track_id()
    assert track_id.startswith("trk_")
    assert is_valid_project_id(track_id, expected_prefix="trk")
    assert not is_valid_project_id(track_id, expected_prefix="tak")

    take_id = generate_take_id()
    assert take_id.startswith("tak_")
    assert is_valid_project_id(take_id, expected_prefix="tak")

    stem_id = generate_stem_id()
    assert stem_id.startswith("stm_")
    assert is_valid_project_id(stem_id, expected_prefix="stm")

    album_id = generate_album_id()
    assert album_id.startswith("alb_")
    assert is_valid_project_id(album_id, expected_prefix="alb")

    # Reject path traversal and invalid strings
    assert not is_valid_project_id("../secret")
    assert not is_valid_project_id("trk_../../evil")
    assert not is_valid_project_id("invalid_id")
    assert not is_valid_project_id("")
    assert not is_valid_project_id(None)


def test_decoupled_directory_hashing():
    """Ensure directory hashes are 32-char hex and decoupled from IDs."""
    user_hash = get_tenant_storage_dir_name("admin_user_1")
    assert len(user_hash) == 32
    assert user_hash != "admin_user_1"

    track_hash = get_track_dir_name("trk_test123456789012")
    assert len(track_hash) == 32
    assert "test123456789012" not in track_hash


def test_posix_0700_directory_isolation(custom_storage):
    """Verify tenant and workspace directories are created with POSIX 0700."""
    user_id = "test_user_posix"
    track = custom_storage.create_track(user_id=user_id, title="POSIX Track")
    
    tenant_dir = custom_storage.get_tenant_dir(user_id)
    track_dir = custom_storage._get_track_dir(user_id, track.id)

    # Check mode permissions on unix
    if os.name == "posix":
        t_mode = stat.S_IMODE(os.stat(tenant_dir).st_mode)
        assert t_mode == 0o700
        tr_mode = stat.S_IMODE(os.stat(track_dir).st_mode)
        assert tr_mode == 0o700


def test_track_workspace_crud_lifecycle(custom_storage):
    """Verify creating, reading, listing, updating, and deleting a track workspace."""
    user_id = "user_alpha"
    
    # 1. Create
    track = custom_storage.create_track(
        user_id=user_id,
        title="Cyber Symphony",
        artist="SynthArtist",
        bpm=128,
        musical_key="F# minor",
        notes="Intro needs heavy bass",
        master_lyrics="[Verse 1]\nDancing in the digital rain",
    )
    assert track.title == "Cyber Symphony"
    assert track.status == WorkspaceStatus.DRAFT
    assert track.bpm == 128
    assert track.musical_key == "F# minor"
    assert track.takes_count == 0

    # 2. Get
    fetched = custom_storage.get_track(user_id, track.id)
    assert fetched is not None
    assert fetched.id == track.id
    assert fetched.artist == "SynthArtist"

    # 3. Update
    updated = custom_storage.update_track(
        user_id,
        track.id,
        {"status": WorkspaceStatus.IN_PROGRESS, "bpm": 130, "title": "Cyber Symphony (Remastered)"},
    )
    assert updated.status == WorkspaceStatus.IN_PROGRESS
    assert updated.bpm == 130
    assert updated.title == "Cyber Symphony (Remastered)"

    # 4. List
    all_tracks = custom_storage.list_tracks(user_id)
    assert len(all_tracks) == 1
    assert all_tracks[0].id == track.id

    in_prog = custom_storage.list_tracks(user_id, status=WorkspaceStatus.IN_PROGRESS)
    assert len(in_prog) == 1
    completed = custom_storage.list_tracks(user_id, status=WorkspaceStatus.COMPLETED)
    assert len(completed) == 0

    # 5. Delete
    assert custom_storage.delete_track(user_id, track.id) is True
    assert custom_storage.get_track(user_id, track.id) is None
    assert len(custom_storage.list_tracks(user_id)) == 0


def test_take_ingestion_and_master_selection(custom_storage):
    """Verify take ingestion, audio saving, and master flag toggling."""
    user_id = "user_beta"
    track = custom_storage.create_track(user_id=user_id, title="Take Test Song")

    dummy_audio_1 = b"DUMMY_MP3_AUDIO_BYTES_TAKE_1"
    take1 = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=dummy_audio_1,
        original_filename="demo_v1.mp3",
        extension=".mp3",
        label="Suno Take 1",
        prompt="synthwave retrowave 80s",
        is_master=False,
    )
    assert take1.label == "Suno Take 1"
    assert take1.size_bytes == len(dummy_audio_1)

    # First take automatically designated as primary_take_id
    updated_track = custom_storage.get_track(user_id, track.id)
    assert updated_track.primary_take_id == take1.id
    assert updated_track.takes_count == 1

    # Audio file exists
    audio_path = custom_storage.get_take_audio_path(user_id, track.id, take1.id)
    assert audio_path is not None
    assert audio_path.is_file()
    with open(audio_path, "rb") as f:
        assert f.read() == dummy_audio_1

    # Ingest Take 2 marked as master
    dummy_audio_2 = b"DUMMY_WAV_AUDIO_BYTES_TAKE_2"
    take2 = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=dummy_audio_2,
        original_filename="studio_master.wav",
        extension=".wav",
        label="Studio Take 2",
        is_master=True,
    )
    
    # Track primary_take_id now points to take 2
    updated_track2 = custom_storage.get_track(user_id, track.id)
    assert updated_track2.primary_take_id == take2.id
    assert updated_track2.takes_count == 2

    # Check takes list
    takes = custom_storage.list_takes(user_id, track.id)
    assert len(takes) == 2
    assert takes[0].id == take1.id
    assert takes[1].id == take2.id

    # Sibling take 1 is_master was cleared
    t1_check = custom_storage.get_take(user_id, track.id, take1.id)
    assert t1_check.is_master is False
    t2_check = custom_storage.get_take(user_id, track.id, take2.id)
    assert t2_check.is_master is True


def test_stems_management(custom_storage):
    """Verify adding, retrieving, and deleting separated stems."""
    user_id = "user_stems"
    track = custom_storage.create_track(user_id=user_id, title="Stem Track")
    take = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=b"FULL_MIX",
        original_filename="mix.mp3",
    )

    vocals_bytes = b"ISOLATED_VOCALS_WAV"
    stem_vocals = custom_storage.add_stem(
        user_id=user_id,
        track_id=track.id,
        take_id=take.id,
        stem_bytes=vocals_bytes,
        original_filename="vocals.wav",
        extension=".wav",
        role=StemRole.VOCALS,
    )
    assert stem_vocals.role == StemRole.VOCALS
    assert stem_vocals.size_bytes == len(vocals_bytes)

    # Verify stem file path
    stem_path = custom_storage.get_stem_audio_path(user_id, track.id, take.id, stem_vocals.id)
    assert stem_path is not None
    assert stem_path.is_file()
    with open(stem_path, "rb") as f:
        assert f.read() == vocals_bytes

    # Check take metadata contains stem
    take_with_stems = custom_storage.get_take(user_id, track.id, take.id)
    assert stem_vocals.id in take_with_stems.stems

    # Delete stem
    assert custom_storage.delete_stem(user_id, track.id, take.id, stem_vocals.id) is True
    assert not stem_path.exists()
    take_after_delete = custom_storage.get_take(user_id, track.id, take.id)
    assert stem_vocals.id not in take_after_delete.stems


def test_take_lyrics_persistence(custom_storage):
    """Verify saving and retrieving LRC and SYLT lyrics for a take."""
    user_id = "user_lyrics"
    track = custom_storage.create_track(user_id=user_id, title="Lyrics Track")
    take = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=b"AUDIO",
        original_filename="audio.mp3",
    )

    lrc_content = "[00:10.50]First line of the song\n[00:15.00]Second line of the song"
    custom_storage.save_take_lyrics(
        user_id=user_id,
        track_id=track.id,
        take_id=take.id,
        lyrics_content=lrc_content,
        lyrics_format=LyricsFormat.LRC,
    )

    lyrics_data = custom_storage.get_take_lyrics(user_id, track.id, take.id)
    assert lyrics_data is not None
    fmt, content = lyrics_data
    assert fmt == LyricsFormat.LRC
    assert content == lrc_content


def test_album_workspace_management(custom_storage):
    """Verify creating, sequencing, adding artwork, and deleting an album project."""
    user_id = "user_album"

    # Create two tracks
    t1 = custom_storage.create_track(user_id=user_id, title="Track One")
    take1 = custom_storage.create_take(user_id=user_id, track_id=t1.id, audio_bytes=b"T1", original_filename="t1.mp3")
    t2 = custom_storage.create_track(user_id=user_id, title="Track Two")
    take2 = custom_storage.create_take(user_id=user_id, track_id=t2.id, audio_bytes=b"T2", original_filename="t2.mp3")

    # Create Album
    album = custom_storage.create_album(
        user_id=user_id,
        title="Debut EP",
        album_artist="Cyber Duo",
        year=2026,
        genre="Synthwave",
        credits="Produced by SynthStudio",
    )
    assert album.title == "Debut EP"
    assert album.tracks == []
    assert album.has_cover is False

    # Add entries via dedicated endpoint & reorder via update_album
    album = custom_storage.add_album_entry(
        user_id, album.id, t1.id, expected_revision=album.revision, take_id=take1.id, custom_title="Track One (Remix)"
    )
    album = custom_storage.add_album_entry(
        user_id, album.id, t2.id, expected_revision=album.revision, take_id=take2.id
    )
    updated_album = custom_storage.update_album(
        user_id,
        album.id,
        {"expected_revision": album.revision, "title": "Debut EP (Mastered)"},
    )
    assert len(updated_album.tracks) == 2
    assert updated_album.tracks[0].track_number == 1
    assert updated_album.tracks[0].custom_title == "Track One (Remix)"

    # Save cover art
    dummy_cover = b"\xff\xd8\xff\xe0\x00\x10JFIF_DUMMY_JPEG"
    custom_storage.save_album_cover(
        user_id, album.id, dummy_cover, expected_revision=updated_album.revision, extension=".jpg"
    )
    
    cover_path = custom_storage.get_album_cover_path(user_id, album.id)
    assert cover_path is not None
    assert cover_path.is_file()
    with open(cover_path, "rb") as f:
        assert f.read() == dummy_cover

    album_with_cover = custom_storage.get_album(user_id, album.id)
    assert album_with_cover.has_cover is True

    # Phase 1A blocks deleting albums that contain recordings or cover artwork
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.delete_album(user_id, album.id, expected_revision=album_with_cover.revision)
    assert custom_storage.get_album(user_id, album.id) is not None

    # Empty draft album deletion succeeds with matching expected_revision
    empty_album = custom_storage.create_album(user_id=user_id, title="Empty Draft")
    assert custom_storage.delete_album(user_id, empty_album.id, expected_revision=empty_album.revision) is True
    assert custom_storage.get_album(user_id, empty_album.id) is None
    # Track workspaces still exist after album deletion
    assert custom_storage.get_track(user_id, t1.id) is not None


def test_tenant_storage_isolation(custom_storage):
    """Verify User A cannot access User B's tracks, takes, or albums."""
    user_a = "alice"
    user_b = "bob"

    track_a = custom_storage.create_track(user_id=user_a, title="Alice Secret Song")
    take_a = custom_storage.create_take(
        user_id=user_a,
        track_id=track_a.id,
        audio_bytes=b"ALICE_AUDIO",
        original_filename="alice.mp3",
    )
    album_a = custom_storage.create_album(user_id=user_a, title="Alice Album")

    # Bob queries Alice's resources
    assert custom_storage.get_track(user_id=user_b, track_id=track_a.id) is None
    assert custom_storage.get_take(user_id=user_b, track_id=track_a.id, take_id=take_a.id) is None
    assert custom_storage.get_album(user_id=user_b, album_id=album_a.id) is None
    assert len(custom_storage.list_tracks(user_id=user_b)) == 0
    assert len(custom_storage.list_albums(user_id=user_b)) == 0

    # Bob cannot update or delete Alice's resources
    assert custom_storage.update_track(user_id=user_b, track_id=track_a.id, updates={"title": "Hacked"}) is None
    assert custom_storage.delete_track(user_id=user_b, track_id=track_a.id) is False


def test_quota_stats_and_overflow_protection(custom_storage, monkeypatch):
    """Verify quota calculation and exception raising when quota is exceeded."""
    user_id = "quota_user"
    
    # Set artificial small quota of 1 KB
    monkeypatch.setattr("backend.project_storage.get_runtime_global_storage_bytes", lambda: 1024)

    track = custom_storage.create_track(user_id=user_id, title="Quota Track")

    # Ingest 500 bytes (should succeed)
    custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=b"X" * 500,
        original_filename="small.mp3",
    )

    stats = custom_storage.get_quota_stats(user_id)
    assert stats.used_bytes >= 500
    assert stats.max_quota_bytes == 1024
    assert stats.tracks_count == 1
    assert stats.takes_count == 1

    # Ingest 600 bytes (500 + 600 = 1100 > 1024 -> should raise ProjectStorageQuotaExceeded)
    with pytest.raises(ProjectStorageQuotaExceeded):
        custom_storage.create_take(
            user_id=user_id,
            track_id=track.id,
            audio_bytes=b"Y" * 600,
            original_filename="overflow.mp3",
        )


def test_track_and_take_revision_tracking(custom_storage):
    """Verify revision tracking on track metadata and take metadata updates."""
    user_id = "rev_user_1"
    track = custom_storage.create_track(user_id=user_id, title="Initial Title", artist="Initial Artist")
    assert track.revision == 1
    assert len(track.revisions) == 1
    assert track.revisions[0].revision == 1
    assert track.revisions[0].change_summary == "Initial track workspace creation"

    # Update track title and notes
    updated = custom_storage.update_track(
        user_id,
        track.id,
        {"title": "Revised Title", "notes": "Added notes"},
        change_summary="Updated title and added production notes",
    )
    assert updated.revision == 2
    assert len(updated.revisions) == 2
    assert updated.revisions[1].revision == 2
    assert updated.revisions[1].change_summary == "Updated title and added production notes"
    assert set(updated.revisions[1].changed_fields) == {"title", "notes"}

    # No-op update should not bump revision
    noop = custom_storage.update_track(user_id, track.id, {"title": "Revised Title"})
    assert noop.revision == 2
    assert len(noop.revisions) == 2

    # Create Take
    take = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=b"DUMMY_AUDIO_DATA_FOR_TAKE_REV",
        original_filename="take1.mp3",
        label="Take 1",
    )
    assert take.revision == 1
    assert len(take.revisions) == 1

    # Update Take metadata without replacing audio
    updated_take = custom_storage.update_take(
        user_id,
        track.id,
        take.id,
        {"label": "Take 1 (Master Mix)", "style_tags": "synthwave, cyberpunk"},
    )
    assert updated_take.revision == 2
    assert len(updated_take.revisions) == 2
    assert updated_take.revisions[1].revision == 2
    assert set(updated_take.revisions[1].changed_fields) == {"label", "style_tags"}


def test_album_revision_tracking(custom_storage):
    """Verify revision tracking on album metadata, artwork, and sequencing."""
    user_id = "rev_user_album"
    album = custom_storage.create_album(user_id=user_id, title="Album Rev 1", album_artist="Artist 1")
    assert album.revision == 1
    assert len(album.revisions) == 1

    # Update album metadata
    updated_album = custom_storage.update_album(
        user_id,
        album.id,
        {"expected_revision": album.revision, "year": 2026, "genre": "Electronic"},
        change_summary="Added release year and genre",
    )
    assert updated_album.revision == 2
    assert len(updated_album.revisions) == 2
    assert updated_album.revisions[1].change_summary == "Added release year and genre"

    # Upload cover art bumps revision
    custom_storage.save_album_cover(
        user_id, album.id, b"FAKE_JPEG_IMAGE_BYTES", expected_revision=updated_album.revision, extension=".jpg"
    )
    refetched = custom_storage.get_album(user_id, album.id)
    assert refetched.revision == 3
    assert len(refetched.revisions) == 3
    assert refetched.revisions[2].change_summary == "Updated album cover artwork"
    assert refetched.revisions[2].changed_fields == ["cover_art"]


def test_first_take_auto_master_and_single_track_revision(custom_storage):
    """Verify the first take auto-sets is_master=True, primary_take_id, and records one track revision."""
    user_id = "user_first_take"
    track = custom_storage.create_track(user_id=user_id, title="First Take Song")
    assert track.revision == 1
    assert track.primary_take_id is None

    take1 = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=b"FIRST_TAKE_AUDIO",
        original_filename="first.mp3",
        extension=".mp3",
        label="Take 1",
        is_master=False,  # Even if False, first take becomes master
    )
    assert take1.is_master is True

    refetched_take = custom_storage.get_take(user_id, track.id, take1.id)
    assert refetched_take.is_master is True

    updated_track = custom_storage.get_track(user_id, track.id)
    assert updated_track.primary_take_id == take1.id
    assert updated_track.revision == 2
    assert len(updated_track.revisions) == 2
    assert set(updated_track.revisions[-1].changed_fields) == {"takes", "primary_take_id"}


def test_exclusive_master_invariant_across_all_operations(custom_storage):
    """Verify at most one take is_master=True and primary_take_id stays synced across all mutations."""
    user_id = "user_master_inv"
    track = custom_storage.create_track(user_id=user_id, title="Master Invariant Track")

    audio_1 = b"AUDIO_TAKE_ONE_BYTES"
    audio_2 = b"AUDIO_TAKE_TWO_BYTES"
    audio_3 = b"AUDIO_TAKE_THREE_BYTES"

    t1 = custom_storage.create_take(user_id, track.id, audio_1, "t1.mp3", ".mp3", label="T1", is_master=False)
    t2 = custom_storage.create_take(user_id, track.id, audio_2, "t2.mp3", ".mp3", label="T2", is_master=False)
    t3 = custom_storage.create_take(user_id, track.id, audio_3, "t3.mp3", ".mp3", label="T3", is_master=False)

    def _assert_master(expected_master_id):
        trk = custom_storage.get_track(user_id, track.id)
        takes = custom_storage.list_takes(user_id, track.id)
        masters = [t.id for t in takes if t.is_master]
        assert trk.primary_take_id == expected_master_id
        if expected_master_id is None:
            assert masters == []
        else:
            assert masters == [expected_master_id]

    # Initially t1 is master
    _assert_master(t1.id)

    # Cannot un-master the active master via update_take(is_master=False)
    custom_storage.update_take(user_id, track.id, t1.id, {"is_master": False})
    _assert_master(t1.id)

    # Switch master to t2 via update_take
    custom_storage.update_take(user_id, track.id, t2.id, {"is_master": True})
    _assert_master(t2.id)

    # Switch master to t3 via update_track(primary_take_id=t3.id)
    custom_storage.update_track(user_id, track.id, {"primary_take_id": t3.id})
    _assert_master(t3.id)

    # Switch master back to t1 via update_take_audio(is_master=True) with updated bytes
    audio_1_v2 = b"AUDIO_TAKE_ONE_UPDATED"
    custom_storage.update_take_audio(user_id, track.id, t1.id, audio_1_v2, extension=".mp3", is_master=True)
    _assert_master(t1.id)

    # Verify sibling takes' audio bytes were never modified by master transitions
    assert custom_storage.get_take_audio_path(user_id, track.id, t2.id).read_bytes() == audio_2
    assert custom_storage.get_take_audio_path(user_id, track.id, t3.id).read_bytes() == audio_3

    # Delete current master t1 -> promotes latest remaining take (t3)
    assert custom_storage.delete_take(user_id, track.id, t1.id) is True
    _assert_master(t3.id)

    # Delete remaining master t3 -> promotes t2
    assert custom_storage.delete_take(user_id, track.id, t3.id) is True
    _assert_master(t2.id)

    # Delete final take t2 -> clears primary_take_id to None
    assert custom_storage.delete_take(user_id, track.id, t2.id) is True
    _assert_master(None)


def test_track_revision_semantics_ignore_routine_take_edits(custom_storage):
    """Verify routine take note/label/lyrics/audio edits do not create parent track revisions."""
    user_id = "user_rev_semantics"
    track = custom_storage.create_track(user_id=user_id, title="Revision Semantics Track")
    assert track.revision == 1

    # Add take 1 (auto master) -> track revision 2
    t1 = custom_storage.create_take(user_id, track.id, b"A1", "a1.mp3", ".mp3", label="Take 1")
    assert custom_storage.get_track(user_id, track.id).revision == 2

    # Add take 2 (non-master) -> track revision 3 (changed_fields == ["takes"])
    t2 = custom_storage.create_take(user_id, track.id, b"A2", "a2.mp3", ".mp3", label="Take 2", is_master=False)
    trk_3 = custom_storage.get_track(user_id, track.id)
    assert trk_3.revision == 3
    assert trk_3.revisions[-1].changed_fields == ["takes"]

    # Routine take note + label edit -> take revision bumps, track revision unchanged (still 3)
    t2_edited = custom_storage.update_take(user_id, track.id, t2.id, {"label": "Take 2 Renamed", "notes": "Better eq"})
    assert t2_edited.revision == 2
    assert custom_storage.get_track(user_id, track.id).revision == 3

    # Take lyrics edit -> track revision unchanged (still 3)
    assert custom_storage.save_take_lyrics(user_id, track.id, t2.id, "[00:01.00]Hello", LyricsFormat.LRC) is True
    assert custom_storage.get_track(user_id, track.id).revision == 3

    # Update take 2 audio without changing master -> take revision bumps, track revision unchanged (still 3)
    t2_audio = custom_storage.update_take_audio(user_id, track.id, t2.id, b"A2_NEW", extension=".mp3", is_master=False)
    assert t2_audio.revision == 4  # 1 init + 1 notes/label + 1 lyrics_format + 1 audio
    assert custom_storage.get_track(user_id, track.id).revision == 3

    # Promote take 2 to master -> track revision bumps to 4 with ["primary_take_id"]
    custom_storage.update_take(user_id, track.id, t2.id, {"is_master": True})
    trk_4 = custom_storage.get_track(user_id, track.id)
    assert trk_4.revision == 4
    assert trk_4.primary_take_id == t2.id
    assert trk_4.revisions[-1].changed_fields == ["primary_take_id"]

    # Remove non-master take 1 -> track revision bumps to 5 with ["takes"]
    assert custom_storage.delete_take(user_id, track.id, t1.id) is True
    trk_5 = custom_storage.get_track(user_id, track.id)
    assert trk_5.revision == 5
    assert trk_5.revisions[-1].changed_fields == ["takes"]


def _make_pcm_wav_bytes(duration_sec: float = 0.5, sample_rate: int = 44100) -> bytes:
    """Build valid uncompressed mono PCM WAV bytes of the requested duration."""
    import io
    import wave
    import struct
    num_frames = int(duration_sec * sample_rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        wf.writeframes(struct.pack("<h", 0) * num_frames)
    return out.getvalue()


def test_direct_take_creation_probes_duration_and_preserves_existing_takes(custom_storage):
    """Verify create_take & update_take_audio probe duration_seconds and isolate sibling audio files."""
    user_id = "user_duration_probe"
    track = custom_storage.create_track(user_id=user_id, title="Duration Track")

    wav_half = _make_pcm_wav_bytes(0.5)
    wav_one = _make_pcm_wav_bytes(1.0)
    wav_two = _make_pcm_wav_bytes(2.0)

    # First take auto-mastered with probed 0.5s duration
    t1 = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=wav_half,
        original_filename="half_sec.wav",
        extension=".wav",
        label="Half Sec",
        is_master=False,
    )
    assert t1.duration_seconds == pytest.approx(0.5, abs=0.02)
    assert t1.is_master is True

    # Second non-master take gets 1.0s duration, does not alter t1 audio or master flag
    t2 = custom_storage.create_take(
        user_id=user_id,
        track_id=track.id,
        audio_bytes=wav_one,
        original_filename="one_sec.wav",
        extension=".wav",
        label="One Sec",
        is_master=False,
    )
    assert t2.duration_seconds == pytest.approx(1.0, abs=0.02)
    assert t2.is_master is False
    assert custom_storage.get_track(user_id, track.id).primary_take_id == t1.id
    assert custom_storage.get_take_audio_path(user_id, track.id, t1.id).read_bytes() == wav_half
    assert custom_storage.get_take_audio_path(user_id, track.id, t2.id).read_bytes() == wav_one

    # Replace t2 audio with 2.0s WAV -> duration_seconds updates to 2.0
    t2_updated = custom_storage.update_take_audio(
        user_id=user_id,
        track_id=track.id,
        take_id=t2.id,
        audio_bytes=wav_two,
        extension=".wav",
        is_master=True,
    )
    assert t2_updated.duration_seconds == pytest.approx(2.0, abs=0.02)
    assert t2_updated.is_master is True
    assert custom_storage.get_take(user_id, track.id, t1.id).is_master is False
    assert custom_storage.get_track(user_id, track.id).primary_take_id == t2.id
    assert custom_storage.get_take_audio_path(user_id, track.id, t1.id).read_bytes() == wav_half


def test_album_entry_opaque_and_legacy_ids():
    """Verify ale_ entry ID generation, validation, hashing, and deterministic legacy assignment."""
    eid = generate_album_entry_id()
    assert eid.startswith("ale_")
    assert is_valid_project_id(eid, expected_prefix="ale")
    assert not is_valid_project_id(eid, expected_prefix="trk")
    assert not is_valid_project_id("ale_../evil", expected_prefix="ale")

    dir_hash = get_album_entry_dir_name(eid)
    assert len(dir_hash) == 32
    assert eid not in dir_hash

    legacy_1 = generate_deterministic_legacy_entry_id("alb_abc", 0, "trk_1", None)
    legacy_1_again = generate_deterministic_legacy_entry_id("alb_abc", 0, "trk_1", None)
    legacy_2 = generate_deterministic_legacy_entry_id("alb_abc", 1, "trk_1", "tak_9")
    assert legacy_1 == legacy_1_again
    assert is_valid_project_id(legacy_1, expected_prefix="ale")
    assert is_valid_project_id(legacy_2, expected_prefix="ale")
    assert legacy_1 != legacy_2


def test_album_placeholder_and_explicit_take_ownership(custom_storage):
    """Verify 0-take placeholders and explicit non-master take copies without mutating track masters."""
    user_id = "user_album_ownership"
    empty_track = custom_storage.create_track(user_id=user_id, title="Sketch Zero")
    recorded_track = custom_storage.create_track(user_id=user_id, title="Recorded Song", artist="Tester")

    wav_master = _make_pcm_wav_bytes(0.4)
    wav_alt = _make_pcm_wav_bytes(0.8)
    t_master = custom_storage.create_take(
        user_id=user_id,
        track_id=recorded_track.id,
        audio_bytes=wav_master,
        original_filename="master.wav",
        extension=".wav",
        label="Master Take",
        is_master=True,
    )
    t_alt = custom_storage.create_take(
        user_id=user_id,
        track_id=recorded_track.id,
        audio_bytes=wav_alt,
        original_filename="alt.wav",
        extension=".wav",
        label="Acoustic Alt",
        notes="Stripped vocal take",
        prompt="acoustic guitar ballad",
        style_tags="acoustic, stripped",
        is_master=False,
    )
    custom_storage.save_take_lyrics(user_id, recorded_track.id, t_alt.id, "Alt verse one", LyricsFormat.LRC)

    album = custom_storage.create_album(user_id=user_id, title="Draft LP")

    # 1. Add 0-take track as placeholder
    album = custom_storage.add_album_entry(user_id, album.id, empty_track.id, expected_revision=album.revision, take_id=None)
    assert len(album.tracks) == 1
    p_entry = album.tracks[0]
    assert is_valid_project_id(p_entry.id, expected_prefix="ale")
    assert p_entry.is_placeholder is True
    assert p_entry.has_owned_audio is False
    assert p_entry.take_id is None
    assert p_entry.snapshot is None
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, p_entry.id) is None
    assert custom_storage.get_track(user_id, empty_track.id).primary_take_id is None

    # 2. Add explicit non-master Take t_alt -> owns copy and snapshot, preserves t_master as track master
    album = custom_storage.add_album_entry(user_id, album.id, recorded_track.id, expected_revision=album.revision, take_id=t_alt.id)
    assert len(album.tracks) == 2
    r_entry = album.tracks[1]
    assert is_valid_project_id(r_entry.id, expected_prefix="ale")
    assert r_entry.is_placeholder is False
    assert r_entry.has_owned_audio is True
    assert r_entry.take_id == t_alt.id
    assert r_entry.snapshot is not None
    assert r_entry.snapshot.source_take_id == t_alt.id
    assert r_entry.snapshot.source_take_label == "Acoustic Alt"
    assert r_entry.snapshot.format == "wav"
    assert r_entry.snapshot.size_bytes == len(wav_alt)
    assert r_entry.snapshot.duration_seconds == pytest.approx(0.8, abs=0.02)
    assert r_entry.snapshot.embedded_lyrics == "Alt verse one"
    assert r_entry.snapshot.take_notes == "Stripped vocal take"
    assert r_entry.snapshot.take_prompt == "acoustic guitar ballad"
    assert r_entry.snapshot.take_style_tags == "acoustic, stripped"

    owned_audio = custom_storage.get_album_entry_audio_path(user_id, album.id, r_entry.id)
    assert owned_audio is not None and owned_audio.read_bytes() == wav_alt
    assert (owned_audio.parent / "snapshot.json").is_file()
    assert stat.S_IMODE(os.stat(owned_audio.parent).st_mode) == 0o700

    # Master selection on Track Workspace must remain untouched
    refreshed_trk = custom_storage.get_track(user_id, recorded_track.id)
    assert refreshed_trk.primary_take_id == t_master.id
    assert custom_storage.get_take(user_id, recorded_track.id, t_master.id).is_master is True
    assert custom_storage.get_take(user_id, recorded_track.id, t_alt.id).is_master is False


def test_album_owned_audio_independence_multi_entry_and_identity(custom_storage):
    """Verify multi-entry support, stable ale_ identity, and isolation from source take edits/deletion."""
    user_id = "user_isolation_multi"
    trk = custom_storage.create_track(user_id=user_id, title="Suite")
    wav_1 = _make_pcm_wav_bytes(0.3)
    wav_2 = _make_pcm_wav_bytes(0.6)
    wav_3 = _make_pcm_wav_bytes(0.9)

    t1 = custom_storage.create_take(user_id, trk.id, wav_1, "t1.wav", ".wav", label="Part I", is_master=True)
    t2 = custom_storage.create_take(user_id, trk.id, wav_2, "t2.wav", ".wav", label="Part II", is_master=False)

    album = custom_storage.create_album(user_id=user_id, title="Double Cut")
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, expected_revision=album.revision, take_id=t1.id)
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, expected_revision=album.revision, take_id=t2.id)
    album = custom_storage.add_album_entry(
        user_id, album.id, trk.id, expected_revision=album.revision, take_id=t2.id, custom_title="Suite (Reprise)"
    )

    assert len(album.tracks) == 3
    e1, e2, e3 = album.tracks
    assert len({e1.id, e2.id, e3.id}) == 3
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1.id).read_bytes() == wav_1
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e3.id).read_bytes() == wav_2

    # Mutate t2 audio & delete t2 -> e2 and e3 keep wav_2 untouched
    custom_storage.update_take_audio(user_id, trk.id, t2.id, wav_3, extension=".wav")
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2
    custom_storage.delete_take(user_id, trk.id, t2.id)
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e3.id).read_bytes() == wav_2

    # Reordering keeps same ale_ entry IDs and owned audio paths
    reordered = custom_storage.update_album(
        user_id,
        album.id,
        {"expected_revision": album.revision, "tracks": [e3.model_dump(), e1.model_dump(), e2.model_dump()]},
    )
    assert [t.id for t in reordered.tracks] == [e3.id, e1.id, e2.id]
    assert [t.track_number for t in reordered.tracks] == [1, 2, 3]
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2

    # Phase 1A blocks clearing or removing a recorded entry, preserving e3's owned audio
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.update_album_entry(
            user_id, album.id, e3.id, expected_revision=reordered.revision, clear_take_to_placeholder=True
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e3.id).read_bytes() == wav_2

    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.remove_album_entry(user_id, album.id, e3.id, expected_revision=reordered.revision)
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e3.id).read_bytes() == wav_2

    # Placeholder entries can still be added and removed cleanly without affecting e1/e2/e3
    with_ph = custom_storage.add_album_entry(user_id, album.id, trk.id, expected_revision=reordered.revision, take_id=None)
    p_id = with_ph.tracks[-1].id
    after_remove = custom_storage.remove_album_entry(user_id, album.id, p_id, expected_revision=with_ph.revision)
    assert [t.id for t in after_remove.tracks] == [e3.id, e1.id, e2.id]
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2


def test_album_entry_staging_rollback_on_failure(custom_storage, monkeypatch):
    """Verify transactional rollback on initial staging failure and Phase 1A block on replacing existing owned audio."""
    import shutil
    import backend.project_storage as ps_mod

    user_id = "user_rollback"
    trk = custom_storage.create_track(user_id=user_id, title="Rollback Song")
    wav_1 = _make_pcm_wav_bytes(0.4)
    wav_2 = _make_pcm_wav_bytes(0.7)
    t1 = custom_storage.create_take(user_id, trk.id, wav_1, "v1.wav", ".wav", label="V1", is_master=True)
    t2 = custom_storage.create_take(user_id, trk.id, wav_2, "v2.wav", ".wav", label="V2", is_master=False)

    album = custom_storage.create_album(user_id=user_id, title="Rollback LP")
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, expected_revision=album.revision, take_id=None)
    entry_id = album.tracks[0].id
    entry_dir = custom_storage._get_album_entry_dir(user_id, album.id, entry_id)

    # 1. Fail mid-staging copy during placeholder -> initial take assignment
    def broken_copy(*args, **kwargs):
        raise OSError("Simulated disk full during copy")

    monkeypatch.setattr(shutil, "copyfileobj", broken_copy)
    with pytest.raises(OSError, match="Simulated disk full"):
        custom_storage.update_album_entry(user_id, album.id, entry_id, expected_revision=album.revision, take_id=t1.id)
    monkeypatch.undo()

    leftover_mid = [p.name for p in entry_dir.iterdir() if p.name.startswith((".staging_", ".backup_"))] if entry_dir.exists() else []
    assert leftover_mid == []
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id) is None

    # 2. Fail BEFORE os.replace during album.json publication (NOT_PUBLISHED) -> rollback removes promoted dest_audio & snapshot
    orig_fchmod = ps_mod.os.fchmod
    orig_publish_in_dir = ps_mod._publish_manifest_atomic_in_dir
    in_manifest_publish = False
    promoted_observed = False

    def wrap_publish_for_fchmod(dir_fd, filename, data):
        nonlocal in_manifest_publish, promoted_observed
        # By the time _publish_manifest_atomic_in_dir is called, commit_staged() has already promoted audio.wav & snapshot.json
        promoted_observed = (entry_dir / "audio.wav").is_file() and (entry_dir / "snapshot.json").is_file()
        in_manifest_publish = True
        try:
            return orig_publish_in_dir(dir_fd, filename, data)
        finally:
            in_manifest_publish = False

    def failing_pre_replace_fchmod(fd, mode, *args, **kwargs):
        if in_manifest_publish:
            raise OSError("Simulated pre-replace manifest failure")
        return orig_fchmod(fd, mode, *args, **kwargs)

    monkeypatch.setattr(ps_mod, "_publish_manifest_atomic_in_dir", wrap_publish_for_fchmod)
    monkeypatch.setattr(ps_mod.os, "fchmod", failing_pre_replace_fchmod)
    with pytest.raises(OSError, match="Simulated pre-replace manifest failure"):
        custom_storage.update_album_entry(user_id, album.id, entry_id, expected_revision=album.revision, take_id=t1.id)
    monkeypatch.undo()

    assert promoted_observed is True
    assert not (entry_dir / "snapshot.json").exists()
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id) is None
    assert custom_storage.get_album(user_id, album.id).tracks[0].is_placeholder is True

    # 3. Initial assignment succeeds, then Phase 1A blocks replacing existing owned audio with t2
    assigned = custom_storage.update_album_entry(user_id, album.id, entry_id, expected_revision=album.revision, take_id=t1.id)
    assert assigned.tracks[0].has_owned_audio is True
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_1

    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.update_album_entry(user_id, album.id, entry_id, expected_revision=assigned.revision, take_id=t2.id)
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_1
    assert custom_storage.get_album(user_id, album.id).tracks[0].take_id == t1.id

    # 4. Fail when manifest publication state is UNCERTAIN (os.replace boundary) -> preserve promoted audio & snapshot
    ph_album = custom_storage.add_album_entry(user_id, album.id, trk.id, expected_revision=assigned.revision, take_id=None)
    ph_entry_id = ph_album.tracks[-1].id
    orig_replace = ps_mod.os.replace

    def uncertain_replace(src, dst, *args, **kwargs):
        if Path(dst).name == "album.json":
            orig_replace(src, dst, *args, **kwargs)
            raise OSError("Simulated post-replace I/O uncertainty")
        return orig_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(ps_mod.os, "replace", uncertain_replace)
    with pytest.raises(OSError, match="Simulated post-replace I/O uncertainty"):
        custom_storage.update_album_entry(
            user_id, album.id, ph_entry_id, expected_revision=ph_album.revision, take_id=t1.id
        )
    monkeypatch.undo()

    preserved_audio = custom_storage.get_album_entry_audio_path(user_id, album.id, ph_entry_id)
    assert preserved_audio is not None and preserved_audio.read_bytes() == wav_1
    assert (preserved_audio.parent / "snapshot.json").is_file()


def test_legacy_album_manifest_compatibility(custom_storage):
    """Verify legacy manifests without id/snapshot/has_owned_audio or using track_ids load safely without blind resolution."""
    import json
    import time

    user_id = "user_legacy"
    trk_a = custom_storage.create_track(user_id=user_id, title="Legacy A")
    custom_storage.create_take(user_id, trk_a.id, _make_pcm_wav_bytes(0.4), "a.wav", ".wav", label="Master A", is_master=True)

    trk_b = custom_storage.create_track(user_id=user_id, title="Legacy B")
    tb1 = custom_storage.create_take(user_id, trk_b.id, _make_pcm_wav_bytes(0.5), "b.wav", ".wav", label="Master B", is_master=True)

    album = custom_storage.create_album(user_id=user_id, title="Legacy Manifest")
    album_dir = custom_storage._get_album_dir(user_id, album.id)
    manifest_path = album_dir / "album.json"

    legacy_payload = {
        "id": album.id,
        "title": "Legacy Manifest",
        "status": "draft",
        "revision": 1,
        "revisions": [],
        "tracks": [
            {"track_number": 1, "disc_number": 1, "track_id": trk_a.id, "take_id": None, "custom_title": None},
            {"track_number": 2, "disc_number": 1, "track_id": trk_b.id, "take_id": tb1.id, "custom_title": None},
        ],
        "track_ids": [trk_a.id, trk_b.id],
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    manifest_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = custom_storage.get_album(user_id, album.id)
    assert loaded is not None
    assert loaded.tracks[0].id.startswith("ale_")
    assert loaded.tracks[0].take_id is None
    assert loaded.tracks[0].is_placeholder is True
    assert loaded.tracks[0].has_owned_audio is False

    assert loaded.tracks[1].id.startswith("ale_")
    assert loaded.tracks[1].take_id == tb1.id
    assert loaded.tracks[1].is_placeholder is False
    assert loaded.tracks[1].has_owned_audio is False

    # Unrelated header save must preserve take_id=None (no auto-resolve to master) and not crash/auto-copy unowned legacy refs
    updated = custom_storage.update_album(user_id, album.id, {"expected_revision": 1, "title": "Renamed Legacy"})
    assert updated.tracks[0].take_id is None
    assert updated.tracks[0].is_placeholder is True
    assert updated.tracks[1].take_id == tb1.id
    assert updated.tracks[1].has_owned_audio is False

    # Pure legacy track_ids list without tracks field
    legacy_ids_only = {
        "id": album.id,
        "title": "Very Old",
        "status": "draft",
        "revision": 1,
        "track_ids": [trk_a.id],
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    manifest_path.write_text(json.dumps(legacy_ids_only), encoding="utf-8")
    loaded_ids = custom_storage.get_album(user_id, album.id)
    assert len(loaded_ids.tracks) == 1
    assert loaded_ids.tracks[0].track_id == trk_a.id
    assert loaded_ids.tracks[0].is_placeholder is True


def test_album_entry_refresh_preview_confirm_and_conflict(custom_storage):
    """Verify read-only refresh preview works while Phase 1A blocks destructive refresh replacement cleanly."""
    user_id = "user_refresh"
    trk = custom_storage.create_track(user_id=user_id, title="Refresh Song")
    wav_v1 = _make_pcm_wav_bytes(0.4)
    wav_v2 = _make_pcm_wav_bytes(0.8)

    t1 = custom_storage.create_take(user_id, trk.id, wav_v1, "v1.wav", ".wav", label="Take 1", notes="Raw mix", is_master=True)
    custom_storage.save_take_lyrics(user_id, trk.id, t1.id, "Line one\nLine two", LyricsFormat.LRC)
    album = custom_storage.create_album(user_id=user_id, title="Refresh LP")
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, expected_revision=album.revision, take_id=t1.id)
    entry_id = album.tracks[0].id

    clean_preview = custom_storage.preview_album_entry_refresh(user_id, album.id, entry_id)
    assert clean_preview.source_available is True
    assert clean_preview.has_changes is False

    # Update source take audio, notes, and lyrics
    custom_storage.update_take_audio(user_id, trk.id, t1.id, wav_v2, extension=".wav")
    custom_storage.update_take(user_id, trk.id, t1.id, {"notes": "Mastered mix"})
    custom_storage.save_take_lyrics(user_id, trk.id, t1.id, "Line one\nLine two revised", LyricsFormat.LRC)

    # Owned album copy stays v1
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_v1

    preview = custom_storage.preview_album_entry_refresh(user_id, album.id, entry_id)
    assert preview.source_available is True
    assert preview.has_changes is True
    assert preview.audio_changed is True
    assert preview.lyrics_changed is True
    assert "-Line two" in preview.lyrics_diff
    assert "+Line two revised" in preview.lyrics_diff
    assert any(c["field"] == "take_notes" and c["old"] == "Raw mix" and c["new"] == "Mastered mix" for c in preview.metadata_changes)

    # Phase 1A blocks confirm_album_entry_refresh and preserves wav_v1 + snapshot
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.confirm_album_entry_refresh(
            user_id,
            album.id,
            entry_id,
            expected_source_revision=preview.expected_source_revision,
            expected_source_audio_sha256=preview.expected_source_audio_sha256,
            expected_source_updated_at=preview.expected_source_updated_at,
            expected_revision=album.revision,
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_v1
    assert custom_storage.get_album(user_id, album.id).tracks[0].snapshot.embedded_lyrics == "Line one\nLine two"

    # Deleting source take -> source_available=False on preview, album copy intact
    custom_storage.delete_take(user_id, trk.id, t1.id)
    gone_preview = custom_storage.preview_album_entry_refresh(user_id, album.id, entry_id)
    assert gone_preview.source_available is False
    assert gone_preview.album_snapshot is not None
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_v1


def test_album_finalization_validation_locking_and_completion(custom_storage):
    """Verify finalization validation checks, completion transition, and strict post-finalization immutability."""
    user_id = "user_finalize"
    with pytest.raises(ProjectValidationError):
        custom_storage.create_album(user_id=user_id, title="Bad Init", status=WorkspaceStatus.COMPLETED)

    trk_empty = custom_storage.create_track(user_id=user_id, title="Placeholder Song")
    trk_ok = custom_storage.create_track(user_id=user_id, title="Final Song")
    wav_ok = _make_pcm_wav_bytes(0.5)
    t_ok = custom_storage.create_take(user_id, trk_ok.id, wav_ok, "ok.wav", ".wav", label="Master", is_master=True)

    album = custom_storage.create_album(user_id=user_id, title="Finalize Test LP")

    # 1. Empty album rejected
    with pytest.raises(AlbumFinalizationError) as exc_empty:
        custom_storage.finalize_album(user_id, album.id, expected_revision=album.revision)
    assert len(exc_empty.value.errors) == 1

    # 2. Placeholder entry rejected
    album = custom_storage.add_album_entry(user_id, album.id, trk_empty.id, expected_revision=album.revision, take_id=None)
    album = custom_storage.add_album_entry(user_id, album.id, trk_ok.id, expected_revision=album.revision, take_id=t_ok.id)
    p_id = album.tracks[0].id
    r_id = album.tracks[1].id

    with pytest.raises(AlbumFinalizationError) as exc_ph:
        custom_storage.finalize_album(user_id, album.id, expected_revision=album.revision)
    assert any("placeholder" in err["reason"].lower() for err in exc_ph.value.errors)

    # Remove placeholder so only recorded entry remains
    album = custom_storage.remove_album_entry(user_id, album.id, p_id, expected_revision=album.revision)

    # 3. Corrupt audio fails checksum/container verification
    owned_path = custom_storage.get_album_entry_audio_path(user_id, album.id, r_id)
    owned_path.write_bytes(b"NOT_AUDIO_CORRUPT_DATA_BYTES_1234567890")
    with pytest.raises(AlbumFinalizationError) as exc_corrupt:
        custom_storage.finalize_album(user_id, album.id, expected_revision=album.revision)
    assert any(r_id == err["entry_id"] for err in exc_corrupt.value.errors)

    # Restore valid audio, verify incomplete .staging_ file fails finalization
    owned_path.write_bytes(wav_ok)
    staging_junk = owned_path.parent / ".staging_orphan_audio.wav"
    staging_junk.write_bytes(b"partial")
    with pytest.raises(AlbumFinalizationError) as exc_staging:
        custom_storage.finalize_album(user_id, album.id, expected_revision=album.revision)
    assert any("incomplete" in err["reason"].lower() for err in exc_staging.value.errors)
    staging_junk.unlink()

    # 4. Orphan directory under entries/ fails finalization
    orphan_dir = custom_storage._get_album_dir(user_id, album.id) / "entries" / "deadbeefdeadbeefdeadbeefdeadbeef"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "junk.bin").write_bytes(b"123")
    with pytest.raises(AlbumFinalizationError) as exc_orphan:
        custom_storage.finalize_album(user_id, album.id, expected_revision=album.revision)
    assert any("inconsistent" in err["reason"].lower() for err in exc_orphan.value.errors)
    (orphan_dir / "junk.bin").unlink()
    orphan_dir.rmdir()

    # 5. Passing finalization sets is_finalized, completed status, and locks all mutations
    finalized = custom_storage.finalize_album(user_id, album.id, expected_revision=album.revision)
    assert finalized.is_finalized is True
    assert finalized.status == WorkspaceStatus.COMPLETED
    assert finalized.finalized_at is not None

    with pytest.raises(AlbumFinalizedError):
        custom_storage.update_album(user_id, album.id, {"expected_revision": finalized.revision, "title": "Locked Edit"})
    with pytest.raises(AlbumFinalizedError):
        custom_storage.add_album_entry(user_id, album.id, trk_ok.id, expected_revision=finalized.revision, take_id=t_ok.id)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.update_album_entry(
            user_id, album.id, r_id, expected_revision=finalized.revision, clear_take_to_placeholder=True
        )
    with pytest.raises(AlbumFinalizedError):
        custom_storage.remove_album_entry(user_id, album.id, r_id, expected_revision=finalized.revision)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.preview_album_entry_refresh(user_id, album.id, r_id)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.confirm_album_entry_refresh(user_id, album.id, r_id, 1, "sha", expected_revision=finalized.revision)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.save_album_cover(
            user_id, album.id, b"\xff\xd8\xff\xe0", expected_revision=finalized.revision, extension=".jpg"
        )
    with pytest.raises(AlbumFinalizedError):
        custom_storage.finalize_album(user_id, album.id, expected_revision=finalized.revision)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.delete_album(user_id, album.id, expected_revision=finalized.revision)
    assert custom_storage.get_album(user_id, album.id) is not None
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, r_id).read_bytes() == wav_ok


def test_phase1a_legacy_track_ids_and_unknown_entry_ids(custom_storage):
    """Verify legacy track_ids reorder preserves owned recordings and unknown/ambiguous IDs are rejected."""
    user_id = "user_legacy_reorder"
    trk_1 = custom_storage.create_track(user_id=user_id, title="Track One")
    trk_2 = custom_storage.create_track(user_id=user_id, title="Track Two")
    wav_1a = _make_pcm_wav_bytes(0.3)
    wav_1b = _make_pcm_wav_bytes(0.5)
    wav_2 = _make_pcm_wav_bytes(0.7)

    t1a = custom_storage.create_take(user_id, trk_1.id, wav_1a, "1a.wav", ".wav", label="Take 1A", is_master=True)
    t1b = custom_storage.create_take(user_id, trk_1.id, wav_1b, "1b.wav", ".wav", label="Take 1B", is_master=False)
    t2 = custom_storage.create_take(user_id, trk_2.id, wav_2, "2.wav", ".wav", label="Take 2", is_master=True)

    album = custom_storage.create_album(user_id=user_id, title="Multi Take LP")
    album = custom_storage.add_album_entry(user_id, album.id, trk_1.id, expected_revision=album.revision, take_id=t1a.id)
    album = custom_storage.add_album_entry(user_id, album.id, trk_1.id, expected_revision=album.revision, take_id=t1b.id)
    album = custom_storage.add_album_entry(user_id, album.id, trk_2.id, expected_revision=album.revision, take_id=t2.id)
    e1a, e1b, e2 = album.tracks

    # 1. Legacy track_ids reorder with duplicate track references preserves all IDs, takes, audio, and snapshots
    reordered = custom_storage.update_album(
        user_id,
        album.id,
        {"expected_revision": album.revision, "track_ids": [trk_2.id, trk_1.id, trk_1.id]},
    )
    assert [t.id for t in reordered.tracks] == [e2.id, e1a.id, e1b.id]
    assert [t.take_id for t in reordered.tracks] == [t2.id, t1a.id, t1b.id]
    assert all(t.has_owned_audio for t in reordered.tracks)
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1a.id).read_bytes() == wav_1a
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1b.id).read_bytes() == wav_1b
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2

    # 2. Ambiguous legacy track_ids dropping one duplicate of trk_1 is rejected without touching recordings
    with pytest.raises((ProjectValidationError, AlbumReplacementDisabledError)):
        custom_storage.update_album(user_id, album.id, {"expected_revision": reordered.revision, "track_ids": [trk_2.id, trk_1.id]})
    assert [t.id for t in custom_storage.get_album(user_id, album.id).tracks] == [e2.id, e1a.id, e1b.id]

    # 3. Unknown explicit ale_ entry ID is rejected and never hijacks/deletes existing owned audio
    unknown_id = generate_album_entry_id()
    with pytest.raises(ProjectValidationError, match="Unknown album entry identifier"):
        custom_storage.update_album(
            user_id,
            album.id,
            {
                "expected_revision": reordered.revision,
                "tracks": [
                    {"id": unknown_id, "track_number": 1, "disc_number": 1, "track_id": trk_2.id, "take_id": t2.id},
                    e1a.model_dump(),
                    e1b.model_dump(),
                ],
            },
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2

    # 4. Ambiguous tracks payload omitting id for duplicate track references is rejected
    with pytest.raises((ProjectValidationError, AlbumReplacementDisabledError)):
        custom_storage.update_album(
            user_id,
            album.id,
            {
                "expected_revision": reordered.revision,
                "tracks": [
                    {"track_number": 1, "disc_number": 1, "track_id": trk_1.id, "take_id": None},
                ],
            },
        )
    assert len(custom_storage.get_album(user_id, album.id).tracks) == 3

    # 5. Attempting to replace owned audio via update_album is blocked with AlbumReplacementDisabledError
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.update_album(
            user_id,
            album.id,
            {
                "expected_revision": reordered.revision,
                "tracks": [
                    e2.model_dump(),
                    {**e1a.model_dump(), "take_id": t1b.id},
                    e1b.model_dump(),
                ],
            },
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1a.id).read_bytes() == wav_1a


def _mp_worker_add_entry(root_dir_str: str, user_id: str, album_id: str, track_id: str, take_id: str, title: str, barrier, queue) -> None:
    mgr = ProjectStorageManager(root_dir=Path(root_dir_str))
    barrier.wait(timeout=10)
    while True:
        current = mgr.get_album(user_id, album_id)
        try:
            updated = mgr.add_album_entry(
                user_id,
                album_id,
                track_id,
                expected_revision=current.revision,
                take_id=take_id,
                custom_title=title,
            )
            queue.put(f"added:{len(updated.tracks)}" if updated else "none")
            return
        except AlbumRevisionConflictError:
            continue


def _mp_worker_finalize(root_dir_str: str, user_id: str, album_id: str, expected_revision: int, barrier, queue) -> None:
    mgr = ProjectStorageManager(root_dir=Path(root_dir_str))
    barrier.wait(timeout=10)
    try:
        fin = mgr.finalize_album(user_id, album_id, expected_revision=expected_revision)
        queue.put(("finalize", "finalized" if (fin and fin.is_finalized) else "missing"))
    except AlbumFinalizedError:
        queue.put(("finalize", "already_finalized"))
    except AlbumRevisionConflictError:
        queue.put(("finalize", "revision_conflict"))


def _mp_worker_update_title(root_dir_str: str, user_id: str, album_id: str, new_title: str, expected_revision: int, barrier, queue) -> None:
    mgr = ProjectStorageManager(root_dir=Path(root_dir_str))
    barrier.wait(timeout=10)
    try:
        res = mgr.update_album(user_id, album_id, {"expected_revision": expected_revision, "title": new_title})
        queue.put(("update", "updated" if res else "missing"))
    except AlbumFinalizedError:
        queue.put(("update", "blocked_finalized"))
    except AlbumRevisionConflictError:
        queue.put(("update", "revision_conflict"))


def _mp_worker_delete_album(root_dir_str: str, user_id: str, album_id: str, expected_revision: int, barrier, queue) -> None:
    mgr = ProjectStorageManager(root_dir=Path(root_dir_str))
    barrier.wait(timeout=10)
    try:
        deleted = mgr.delete_album(user_id, album_id, expected_revision=expected_revision)
        queue.put(("delete", "deleted" if deleted else "missing"))
    except AlbumFinalizedError:
        queue.put(("delete", "blocked_finalized"))
    except AlbumReplacementDisabledError:
        queue.put(("delete", "blocked_non_empty"))
    except AlbumRevisionConflictError:
        queue.put(("delete", "revision_conflict"))


def test_phase1a_cross_process_album_locking_and_deletion_race(tmp_path):
    """Verify cross-process serialization and actual worker outcomes for entries, finalization, and deletion races."""
    import multiprocessing as mp

    root_dir = tmp_path / "mp_tenants"
    mgr = ProjectStorageManager(root_dir=root_dir)
    user_id = "user_mp"

    trk = mgr.create_track(user_id=user_id, title="Concurrent Track")
    wav_bytes = _make_pcm_wav_bytes(0.4)
    take = mgr.create_take(user_id, trk.id, wav_bytes, "c.wav", ".wav", label="Master", is_master=True)

    # 1. Four processes concurrently add entries to the same album -> all 4 entries and audio copies survive
    album = mgr.create_album(user_id=user_id, title="Concurrent Entries LP")
    ctx = mp.get_context("spawn")
    barrier_add = ctx.Barrier(4)
    q_add = ctx.Queue()
    procs_add = [
        ctx.Process(target=_mp_worker_add_entry, args=(str(root_dir), user_id, album.id, trk.id, take.id, f"Cut {i}", barrier_add, q_add))
        for i in range(4)
    ]
    for p in procs_add:
        p.start()
    for p in procs_add:
        p.join(timeout=15)
        assert p.exitcode == 0

    add_outcomes = [q_add.get(timeout=5) for _ in range(4)]
    assert sorted(add_outcomes) == ["added:1", "added:2", "added:3", "added:4"]

    after_add = mgr.get_album(user_id, album.id)
    assert len(after_add.tracks) == 4
    assert len({e.id for e in after_add.tracks}) == 4
    for entry in after_add.tracks:
        assert mgr.get_album_entry_audio_path(user_id, album.id, entry.id).read_bytes() == wav_bytes

    # 2. Concurrent finalize + update_album across processes -> assert exact worker outcomes
    barrier_race = ctx.Barrier(2)
    q_race = ctx.Queue()
    p_fin = ctx.Process(target=_mp_worker_finalize, args=(str(root_dir), user_id, album.id, after_add.revision, barrier_race, q_race))
    p_upd = ctx.Process(target=_mp_worker_update_title, args=(str(root_dir), user_id, album.id, "Raced Title", after_add.revision, barrier_race, q_race))
    p_fin.start()
    p_upd.start()
    p_fin.join(timeout=15)
    p_upd.join(timeout=15)
    assert p_fin.exitcode == 0 and p_upd.exitcode == 0

    race_outcomes = dict(q_race.get(timeout=5) for _ in range(2))
    assert race_outcomes["finalize"] in ("finalized", "revision_conflict")
    assert race_outcomes["update"] in ("updated", "blocked_finalized", "revision_conflict")
    assert ("finalized" in race_outcomes.values()) or ("updated" in race_outcomes.values())

    # If update won first, finalize cleanly at the new revision
    cur_album = mgr.get_album(user_id, album.id)
    if not cur_album.is_finalized:
        mgr.finalize_album(user_id, album.id, expected_revision=cur_album.revision)

    final_album = mgr.get_album(user_id, album.id)
    assert final_album.is_finalized is True
    assert final_album.status == WorkspaceStatus.COMPLETED
    assert final_album.finalized_at is not None

    # 3. Concurrent finalize + delete_album on another valid draft album -> recorded album cannot be deleted in Phase 1A
    race_del_album = mgr.create_album(user_id=user_id, title="Finalize vs Delete LP")
    race_del_album = mgr.add_album_entry(user_id, race_del_album.id, trk.id, expected_revision=race_del_album.revision, take_id=take.id)
    barrier_fd = ctx.Barrier(2)
    q_fd = ctx.Queue()
    p_fin2 = ctx.Process(target=_mp_worker_finalize, args=(str(root_dir), user_id, race_del_album.id, race_del_album.revision, barrier_fd, q_fd))
    p_del2 = ctx.Process(target=_mp_worker_delete_album, args=(str(root_dir), user_id, race_del_album.id, race_del_album.revision, barrier_fd, q_fd))
    p_fin2.start()
    p_del2.start()
    p_fin2.join(timeout=15)
    p_del2.join(timeout=15)
    assert p_fin2.exitcode == 0 and p_del2.exitcode == 0

    fd_outcomes = dict(q_fd.get(timeout=5) for _ in range(2))
    assert fd_outcomes["finalize"] == "finalized"
    assert fd_outcomes["delete"] in ("blocked_finalized", "blocked_non_empty")
    assert mgr.get_album(user_id, race_del_album.id).is_finalized is True

    # 4. Concurrent delete_album vs update_album on an empty unfinalized album -> lockfile stays stable, no phantom dir
    draft_album = mgr.create_album(user_id=user_id, title="Delete Race LP")
    barrier_del = ctx.Barrier(2)
    q_del = ctx.Queue()
    p_del = ctx.Process(target=_mp_worker_delete_album, args=(str(root_dir), user_id, draft_album.id, draft_album.revision, barrier_del, q_del))
    p_upd2 = ctx.Process(target=_mp_worker_update_title, args=(str(root_dir), user_id, draft_album.id, "After Delete", draft_album.revision, barrier_del, q_del))
    p_del.start()
    p_upd2.start()
    p_del.join(timeout=15)
    p_upd2.join(timeout=15)
    assert p_del.exitcode == 0 and p_upd2.exitcode == 0

    del_outcomes = dict(q_del.get(timeout=5) for _ in range(2))
    assert del_outcomes["delete"] in ("deleted", "revision_conflict")
    if del_outcomes["delete"] == "deleted":
        assert del_outcomes["update"] == "missing"
        assert mgr.get_album(user_id, draft_album.id) is None
        assert not mgr._get_album_dir(user_id, draft_album.id).exists()
    else:
        assert del_outcomes["update"] == "updated"
        updated_draft = mgr.get_album(user_id, draft_album.id)
        assert updated_draft is not None
        assert mgr.delete_album(user_id, draft_album.id, expected_revision=updated_draft.revision) is True
        assert mgr.get_album(user_id, draft_album.id) is None
    assert mgr._get_album_lock_path(user_id, draft_album.id).is_file()


def test_phase1a1_implicit_deletion_and_allow_new_entry_id_blocked(custom_storage):
    """Verify omitted/null take_id cannot clear owned recordings, untrusted _allow_new_entry_id is ignored, and stale revisions fail."""
    user_id = "user_p1a1"
    trk = custom_storage.create_track(user_id=user_id, title="Preserved Track")
    wav_1 = _make_pcm_wav_bytes(0.4)
    t1 = custom_storage.create_take(user_id, trk.id, wav_1, "p1.wav", ".wav", label="Take 1", is_master=True)

    album = custom_storage.create_album(user_id=user_id, title="Preservation LP")
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, expected_revision=album.revision, take_id=t1.id)
    e1 = album.tracks[0]

    # 1. Omitted take_id in bulk update preserves e1's recording and owned audio
    renamed = custom_storage.update_album(
        user_id,
        album.id,
        {
            "expected_revision": album.revision,
            "tracks": [{"id": e1.id, "track_id": trk.id, "track_number": 1, "disc_number": 1, "custom_title": "New Title"}],
        },
    )
    assert renamed.tracks[0].id == e1.id
    assert renamed.tracks[0].take_id == t1.id
    assert renamed.tracks[0].has_owned_audio is True
    assert renamed.tracks[0].custom_title == "New Title"
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1.id).read_bytes() == wav_1

    # 2. Explicit take_id: None on recorded entry in bulk update is rejected
    with pytest.raises((ProjectValidationError, AlbumReplacementDisabledError)):
        custom_storage.update_album(
            user_id,
            album.id,
            {
                "expected_revision": renamed.revision,
                "tracks": [{"id": e1.id, "track_id": trk.id, "take_id": None, "track_number": 1, "disc_number": 1}],
            },
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1.id).read_bytes() == wav_1

    # 3. Untrusted _allow_new_entry_id flag in update dict is ignored -> unknown ale_ ID rejected
    forged_id = generate_album_entry_id()
    with pytest.raises(ProjectValidationError, match="Unknown album entry identifier"):
        custom_storage.update_album(
            user_id,
            album.id,
            {
                "expected_revision": renamed.revision,
                "tracks": [
                    {"id": forged_id, "_allow_new_entry_id": True, "track_id": trk.id, "take_id": t1.id, "track_number": 1}
                ],
            },
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1.id).read_bytes() == wav_1

    # 4. Stale expected_revision raises AlbumRevisionConflictError without removing entries/audio
    stale_rev = renamed.revision - 1
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.update_album(
            user_id,
            album.id,
            {"expected_revision": stale_rev, "title": "Stale Title", "tracks": []},
        )
    assert len(custom_storage.get_album(user_id, album.id).tracks) == 1
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1.id).read_bytes() == wav_1

    # 5. Omitting expected_revision in update_album always raises ProjectValidationError
    with pytest.raises(ProjectValidationError, match="expected_revision"):
        custom_storage.update_album(user_id, album.id, {"tracks": []})
    assert len(custom_storage.get_album(user_id, album.id).tracks) == 1
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e1.id).read_bytes() == wav_1


def test_phase1a1_fail_closed_corrupt_manifest_and_partial_delete_error(custom_storage, monkeypatch):
    """Verify corrupt manifests fail closed (never deleted), malformed finalized manifests stay protected, and partial delete errors raise."""
    import json

    user_id = "user_corrupt"
    trk = custom_storage.create_track(user_id=user_id, title="Safe Track")
    wav_ok = _make_pcm_wav_bytes(0.4)
    take = custom_storage.create_take(user_id, trk.id, wav_ok, "safe.wav", ".wav", label="Master", is_master=True)

    # 1. Corrupt JSON on draft album -> get_album and delete_album fail closed, preserving entries/
    draft = custom_storage.create_album(user_id=user_id, title="Corrupt Draft")
    draft = custom_storage.add_album_entry(user_id, draft.id, trk.id, expected_revision=draft.revision, take_id=take.id)
    e_id = draft.tracks[0].id
    draft_dir = custom_storage._get_album_dir(user_id, draft.id)
    (draft_dir / "album.json").write_text("{corrupt_json", encoding="utf-8")

    with pytest.raises(AlbumManifestCorruptError):
        custom_storage.get_album(user_id, draft.id)
    with pytest.raises(AlbumManifestCorruptError):
        custom_storage.delete_album(user_id, draft.id, expected_revision=1)
    assert custom_storage.get_album_entry_audio_path(user_id, draft.id, e_id).read_bytes() == wav_ok

    # 2. Finalized album with malformed unrelated field ('year': 'bad_int') -> delete_album still raises AlbumFinalizedError
    fin = custom_storage.create_album(user_id=user_id, title="Malformed Finalized")
    fin = custom_storage.add_album_entry(user_id, fin.id, trk.id, expected_revision=fin.revision, take_id=take.id)
    fin_e_id = fin.tracks[0].id
    fin = custom_storage.finalize_album(user_id, fin.id, expected_revision=fin.revision)
    fin_manifest = custom_storage._get_album_dir(user_id, fin.id) / "album.json"
    raw_fin = json.loads(fin_manifest.read_text(encoding="utf-8"))
    raw_fin["year"] = "not_a_number"
    fin_manifest.write_text(json.dumps(raw_fin), encoding="utf-8")

    with pytest.raises(AlbumFinalizedError):
        custom_storage.delete_album(user_id, fin.id, expected_revision=fin.revision)
    assert custom_storage.get_album_entry_audio_path(user_id, fin.id, fin_e_id).read_bytes() == wav_ok

    # 3. Fault injection on empty-album deletion: failure after album.json is unlinked before os.rmdir(album_dir_name)
    #    must raise ProjectStorageError, preserve remaining directory evidence, and never report success.
    import backend.project_storage as ps_mod
    draft2 = custom_storage.create_album(user_id=user_id, title="Partial Delete LP")
    draft2_dir = custom_storage._get_album_dir(user_id, draft2.id)
    draft2_name = draft2_dir.name
    orig_rmdir = ps_mod.os.rmdir

    def fail_rmdir(path, *args, **kwargs):
        if Path(path).name == draft2_name:
            raise PermissionError("Simulated EACCES on os.rmdir(album_dir)")
        return orig_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(ps_mod.os, "rmdir", fail_rmdir)
    with pytest.raises(ProjectStorageError, match="Failed to completely delete album"):
        custom_storage.delete_album(user_id, draft2.id, expected_revision=draft2.revision)
    monkeypatch.undo()
    assert draft2_dir.exists()
    assert not (draft2_dir / "album.json").exists()


def test_phase1a_final_predictive_storage_safeguards(custom_storage, tmp_path):
    """Verify mandatory revisions, allowlist empty-album deletion, standalone snapshot checks, raw contradiction fail-closed, and read-only path purity."""
    import json

    user_id = "user_p1a_final"
    trk = custom_storage.create_track(user_id=user_id, title="Guard Track")
    wav_ok = _make_pcm_wav_bytes(0.4)
    take = custom_storage.create_take(user_id, trk.id, wav_ok, "guard.wav", ".wav", label="Master", is_master=True)

    # 1. Read-only operations on missing tenant/album create zero directories
    missing_user = "never_created_tenant"
    assert custom_storage.list_albums(missing_user) == []
    assert custom_storage.get_album(missing_user, "alb_missing000001") is None
    assert custom_storage.get_album_entry_audio_path(missing_user, "alb_missing000001", "ale_missing000001") is None
    assert custom_storage.get_album_cover_path(missing_user, "alb_missing000001") is None
    assert not custom_storage._get_tenant_dir_path(missing_user).exists()

    # 2. Storage-layer missing/stale expected_revision rejected across all mutators
    alb = custom_storage.create_album(user_id=user_id, title="Revision Guard LP")
    with pytest.raises(ProjectValidationError):
        custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=0, take_id=None)
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision + 5, take_id=None)

    alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=None)
    ph_id = alb.tracks[0].id
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.update_album_entry(user_id, alb.id, ph_id, expected_revision=alb.revision - 1, custom_title="Stale")
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.remove_album_entry(user_id, alb.id, ph_id, expected_revision=alb.revision - 1)
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.save_album_cover(user_id, alb.id, b"\xff\xd8\xff\xe0", expected_revision=alb.revision - 1, extension=".jpg")
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.finalize_album(user_id, alb.id, expected_revision=alb.revision - 1)
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.delete_album(user_id, alb.id, expected_revision=alb.revision - 1)

    # 3. Empty-album deletion allowlist rejects unknown files, hidden files, and symlinks
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    stray = alb_dir / ".hidden_artifact"
    stray.write_bytes(b"keep")
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.delete_album(user_id, alb.id, expected_revision=alb.revision)
    assert (alb_dir / "album.json").is_file()
    stray.unlink()

    ext_target = tmp_path / "outside.txt"
    ext_target.write_text("outside", encoding="utf-8")
    symlink_path = alb_dir / "entries" / "symlink_probe"
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(ext_target)
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.delete_album(user_id, alb.id, expected_revision=alb.revision)
    assert ext_target.read_text(encoding="utf-8") == "outside"
    symlink_path.unlink()
    symlink_path.parent.rmdir()

    # Empty placeholder-only album (with no unknown files) deletes cleanly
    assert custom_storage.delete_album(user_id, alb.id, expected_revision=alb.revision) is True
    assert not alb_dir.exists()

    # 4. Standalone snapshot.json validation during finalize_album
    fin_alb = custom_storage.create_album(user_id=user_id, title="Snapshot Check LP")
    fin_alb = custom_storage.add_album_entry(user_id, fin_alb.id, trk.id, expected_revision=fin_alb.revision, take_id=take.id)
    e_id = fin_alb.tracks[0].id
    snap_path = custom_storage.get_album_entry_audio_path(user_id, fin_alb.id, e_id).parent / "snapshot.json"
    orig_snap = snap_path.read_text(encoding="utf-8")
    tampered_snap = json.loads(orig_snap)
    tampered_snap["source_take_label"] = "Tampered"
    snap_path.write_text(json.dumps(tampered_snap), encoding="utf-8")
    with pytest.raises(AlbumFinalizationError) as exc_snap:
        custom_storage.finalize_album(user_id, fin_alb.id, expected_revision=fin_alb.revision)
    assert any("snapshot" in err["reason"].lower() for err in exc_snap.value.errors)
    snap_path.write_text(orig_snap, encoding="utf-8")

    # 5. Raw contradictory manifest (take_id: null with embedded snapshot) fails closed
    bad_alb = custom_storage.create_album(user_id=user_id, title="Contradiction LP")
    bad_manifest = custom_storage._get_album_dir(user_id, bad_alb.id) / "album.json"
    raw = json.loads(bad_manifest.read_text(encoding="utf-8"))
    raw["tracks"] = [
        {
            "id": generate_album_entry_id(),
            "track_number": 1,
            "disc_number": 1,
            "track_id": trk.id,
            "take_id": None,
            "is_placeholder": True,
            "has_owned_audio": False,
            "snapshot": {"source_take_id": take.id, "audio_sha256": "abc"},
        }
    ]
    bad_manifest.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AlbumManifestCorruptError):
        custom_storage.get_album(user_id, bad_alb.id)


def _mp_worker_same_rev_writer(root_dir_str: str, user_id: str, album_id: str, expected_rev: int, title: str, barrier, queue) -> None:
    mgr = ProjectStorageManager(root_dir=Path(root_dir_str))
    barrier.wait(timeout=10)
    try:
        updated = mgr.update_album(user_id, album_id, {"expected_revision": expected_rev, "title": title})
        queue.put(("ok", updated.revision if updated else None))
    except AlbumRevisionConflictError:
        queue.put(("conflict", None))


def test_phase1a2_artwork_safety_containment_finalization_and_concurrency(custom_storage, tmp_path, monkeypatch):
    """Verify Phase 1A.2 fixes: artwork symlink/rollback/preservation, ancestor symlinks, finalization layout & source_track_revision, bulk PATCH placeholder block, malformed revisions, and no-retry concurrent writers."""
    import json
    import multiprocessing as mp
    import backend.project_storage as ps_mod

    user_id = "user_p1a2"
    trk = custom_storage.create_track(user_id=user_id, title="Artwork Safe Track")
    wav_ok = _make_pcm_wav_bytes(0.4)
    take = custom_storage.create_take(user_id, trk.id, wav_ok, "safe.wav", ".wav", label="Master", is_master=True)

    # 1. Pre-existing .tmp_cover.jpg symlink must NOT truncate recording; unknown artifact preserved
    alb = custom_storage.create_album(user_id=user_id, title="Cover Safety LP")
    alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=take.id)
    entry_id = alb.tracks[0].id
    audio_path = custom_storage.get_album_entry_audio_path(user_id, alb.id, entry_id)
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    tmp_symlink = alb_dir / ".tmp_cover.jpg"
    tmp_symlink.symlink_to(audio_path)

    # 1a. Pre-replace manifest failure (NOT_PUBLISHED) rolls back newly promoted cover, preserves recording
    orig_fchmod = ps_mod.os.fchmod
    call_count = 0

    def fail_pre_replace_cover(fd, mode, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        # 1st fchmod is staging cover; 2nd fchmod is .tmp_manifest_
        if call_count == 2:
            raise OSError("Simulated pre-replace cover failure")
        return orig_fchmod(fd, mode, *args, **kwargs)

    monkeypatch.setattr(ps_mod.os, "fchmod", fail_pre_replace_cover)
    with pytest.raises(OSError, match="Simulated pre-replace cover failure"):
        custom_storage.save_album_cover(user_id, alb.id, b"FAKE_JPG_1", expected_revision=alb.revision, extension=".jpg")
    monkeypatch.undo()
    assert not (alb_dir / "cover.jpg").exists()
    assert audio_path.read_bytes() == wav_ok
    assert tmp_symlink.is_symlink()

    # 1b. Uncertain manifest failure (UNCERTAIN) preserves promoted cover.jpg
    orig_replace = ps_mod.os.replace

    def fail_uncertain_cover(src, dst, *args, **kwargs):
        if Path(dst).name == "album.json":
            orig_replace(src, dst, *args, **kwargs)
            raise OSError("Simulated uncertain cover publish")
        return orig_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(ps_mod.os, "replace", fail_uncertain_cover)
    with pytest.raises(OSError, match="Simulated uncertain cover publish"):
        custom_storage.save_album_cover(user_id, alb.id, b"FAKE_JPG_COMMITTED", expected_revision=alb.revision, extension=".jpg")
    monkeypatch.undo()
    assert (alb_dir / "cover.jpg").read_bytes() == b"FAKE_JPG_COMMITTED"

    # 1c. Replacing existing cover is disabled (409) and preserves existing cover bytes
    refreshed = custom_storage.get_album(user_id, alb.id)
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.save_album_cover(user_id, alb.id, b"FAKE_JPG_NEW", expected_revision=refreshed.revision, extension=".jpg")
    assert (alb_dir / "cover.jpg").read_bytes() == b"FAKE_JPG_COMMITTED"
    tmp_symlink.unlink()

    # 2. Finalization validates source_track_revision and complete root/entry layout
    snap_path = audio_path.parent / "snapshot.json"
    orig_snap = snap_path.read_text(encoding="utf-8")
    tampered = json.loads(orig_snap)
    tampered["source_track_revision"] = 999
    snap_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(AlbumFinalizationError) as exc_rev:
        custom_storage.finalize_album(user_id, alb.id, expected_revision=refreshed.revision)
    assert any("snapshot" in err["reason"].lower() for err in exc_rev.value.errors)
    snap_path.write_text(orig_snap, encoding="utf-8")

    stray_root = alb_dir / "stray_root.txt"
    stray_root.write_text("keep", encoding="utf-8")
    with pytest.raises(AlbumFinalizationError) as exc_root:
        custom_storage.finalize_album(user_id, alb.id, expected_revision=refreshed.revision)
    assert any("unexpected album root artifact" in err["reason"].lower() for err in exc_root.value.errors)
    assert stray_root.is_file()
    stray_root.unlink()

    extra_entry_file = audio_path.parent / "extra.bin"
    extra_entry_file.write_bytes(b"extra")
    with pytest.raises(AlbumFinalizationError) as exc_entry:
        custom_storage.finalize_album(user_id, alb.id, expected_revision=refreshed.revision)
    assert any("unexpected artifact" in err["reason"].lower() for err in exc_entry.value.errors)
    assert extra_entry_file.is_file()
    extra_entry_file.unlink()

    # 3. Bulk update_album rejects placeholder-to-Take assignment
    ph_alb = custom_storage.create_album(user_id=user_id, title="Bulk Placeholder LP")
    ph_alb = custom_storage.add_album_entry(user_id, ph_alb.id, trk.id, expected_revision=ph_alb.revision, take_id=None)
    ph_entry = ph_alb.tracks[0]
    with pytest.raises(ProjectValidationError, match="Track/Take assignment"):
        custom_storage.update_album(
            user_id,
            ph_alb.id,
            {
                "expected_revision": ph_alb.revision,
                "tracks": [{**ph_entry.model_dump(), "take_id": take.id}],
            },
        )
    assert custom_storage.get_album(user_id, ph_alb.id).tracks[0].is_placeholder is True

    # 4. Malformed expected_revision types rejected at storage boundary
    for bad_rev in (True, False, 1.5, 1.0, "1", 0, -1, None):
        with pytest.raises(ProjectValidationError):
            custom_storage.update_album(user_id, ph_alb.id, {"expected_revision": bad_rev, "title": "Bad"})

    # 5. Ancestor symlink containment: symlinked entries/ dir fails closed without reading outside
    outside_dir = tmp_path / "outside_entries"
    outside_dir.mkdir()
    sym_alb = custom_storage.create_album(user_id=user_id, title="Symlink Ancestor LP")
    sym_alb = custom_storage.add_album_entry(user_id, sym_alb.id, trk.id, expected_revision=sym_alb.revision, take_id=take.id)
    sym_entry_id = sym_alb.tracks[0].id
    sym_dir = custom_storage._get_album_dir(user_id, sym_alb.id)
    import shutil as _shutil
    _shutil.rmtree(sym_dir / "entries")
    (sym_dir / "entries").symlink_to(outside_dir)
    assert custom_storage.get_album_entry_audio_path(user_id, sym_alb.id, sym_entry_id) is None
    with pytest.raises(AlbumManifestCorruptError):
        custom_storage.open_album_entry_audio_for_read(user_id, sym_alb.id, sym_entry_id)
    (sym_dir / "entries").unlink()

    # 6. Two concurrent writers retaining the same original revision (no auto-retry): 1 succeeds, 1 conflicts
    conc_root = tmp_path / "conc_tenants"
    conc_mgr = ProjectStorageManager(root_dir=conc_root)
    conc_alb = conc_mgr.create_album(user_id=user_id, title="Same Rev Race")
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    q = ctx.Queue()
    p1 = ctx.Process(target=_mp_worker_same_rev_writer, args=(str(conc_root), user_id, conc_alb.id, conc_alb.revision, "Writer 1", barrier, q))
    p2 = ctx.Process(target=_mp_worker_same_rev_writer, args=(str(conc_root), user_id, conc_alb.id, conc_alb.revision, "Writer 2", barrier, q))
    p1.start()
    p2.start()
    p1.join(timeout=15)
    p2.join(timeout=15)
    assert p1.exitcode == 0 and p2.exitcode == 0
    outcomes = sorted([q.get(timeout=5) for _ in range(2)], key=lambda x: x[0])
    assert outcomes == [("conflict", None), ("ok", 2)]


def test_phase1a3_post_pub_artwork_non_finite_snapshots_and_dir_fd_containment(custom_storage, tmp_path, monkeypatch):
    """Verify Phase 1A.3: post-publication artwork preservation on reload error, non-finite float rejection, and dir_fd write containment."""
    import json
    import backend.project_storage as ps_mod
    from backend.project_models import AlbumEntrySnapshot

    user_id = "user_p1a3"
    trk = custom_storage.create_track(user_id=user_id, title="P1A3 Track")
    wav_ok = _make_pcm_wav_bytes(0.4)
    take = custom_storage.create_take(user_id, trk.id, wav_ok, "p1a3.wav", ".wav", label="Master", is_master=True)

    # 1. Post-publication reload failure in save_album_cover must NOT delete committed cover.jpg
    alb = custom_storage.create_album(user_id=user_id, title="PostPub Cover LP")
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    orig_read = custom_storage._read_album_unlocked
    read_calls = 0

    def fail_post_pub_reload(uid, aid, *, protect_finalized=False):
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            raise OSError("Simulated post-publication reload failure")
        return orig_read(uid, aid, protect_finalized=protect_finalized)

    monkeypatch.setattr(custom_storage, "_read_album_unlocked", fail_post_pub_reload)
    with pytest.raises(OSError, match="Simulated post-publication reload failure"):
        custom_storage.save_album_cover(user_id, alb.id, b"COMMITTED_COVER_BYTES", expected_revision=alb.revision, extension=".jpg")
    monkeypatch.undo()

    assert (alb_dir / "cover.jpg").read_bytes() == b"COMMITTED_COVER_BYTES"
    reloaded = custom_storage.get_album(user_id, alb.id)
    assert reloaded.has_cover is True
    assert reloaded.revision == 2

    # 2. Non-finite float rejection in _snapshots_semantically_equal across all 3 float fields
    base_snap = AlbumEntrySnapshot(
        entry_id="ale_0000000000000001",
        source_track_id=trk.id,
        source_track_title="T",
        source_track_revision=1,
        source_take_id=take.id,
        source_take_label="L",
        source_take_revision=1,
        source_take_updated_at=100.0,
        source_filename="f.wav",
        format="wav",
        size_bytes=100,
        duration_seconds=1.5,
        audio_sha256="a" * 64,
        copied_at=200.0,
    )
    for float_fld in ("source_take_updated_at", "duration_seconds", "copied_at"):
        # Finite within tolerance (<= 1e-6) -> equal
        within = base_snap.model_copy(update={float_fld: getattr(base_snap, float_fld) + 5e-7})
        assert custom_storage._snapshots_semantically_equal(base_snap, within) is True
        # Finite outside tolerance (> 1e-6) -> not equal
        outside = base_snap.model_copy(update={float_fld: getattr(base_snap, float_fld) + 2e-6})
        assert custom_storage._snapshots_semantically_equal(base_snap, outside) is False
        # NaN / +inf / -inf -> rejected even when both sides match
        for non_finite in (float("nan"), float("inf"), float("-inf")):
            bad_a = base_snap.model_copy(update={float_fld: non_finite})
            bad_b = base_snap.model_copy(update={float_fld: non_finite})
            assert custom_storage._snapshots_semantically_equal(bad_a, bad_b) is False
            assert custom_storage._snapshots_semantically_equal(base_snap, bad_b) is False

    # 3. Write-time dir_fd containment: swapping album_dir path to an external symlink after album_dir_fd is opened
    #    must keep manifest publication strictly inside the original album_dir inode.
    cont_alb = custom_storage.create_album(user_id=user_id, title="DirFd Containment LP")
    cont_dir = custom_storage._get_album_dir(user_id, cont_alb.id)
    orig_inode = os.stat(cont_dir).st_ino
    external_dir = tmp_path / "external_redirect_target"
    external_dir.mkdir()
    backup_dir = tmp_path / "moved_original_album_dir"

    orig_publish_in_dir = ps_mod._publish_manifest_atomic_in_dir

    def swap_path_before_publish(dir_fd, filename, data):
        cont_dir.rename(backup_dir)
        cont_dir.symlink_to(external_dir)
        return orig_publish_in_dir(dir_fd, filename, data)

    monkeypatch.setattr(ps_mod, "_publish_manifest_atomic_in_dir", swap_path_before_publish)
    updated = custom_storage.update_album(
        user_id, cont_alb.id, {"expected_revision": cont_alb.revision, "title": "Confined Write"}
    )
    monkeypatch.undo()

    assert updated is not None and updated.title == "Confined Write"
    assert not (external_dir / "album.json").exists()
    assert os.stat(backup_dir).st_ino == orig_inode
    assert json.loads((backup_dir / "album.json").read_text(encoding="utf-8"))["title"] == "Confined Write"


def test_phase1a4_transaction_ownership_and_dir_fd_consistency(custom_storage, tmp_path, monkeypatch):
    """Verify Phase 1A.4 dir_fd ownership: entry swap before promotion/rollback, inode-checked rollback, finalized manifest swap, and empty-entry/album cleanup."""
    import json
    import backend.project_storage as ps_mod

    user_id = "user_p1a4"
    trk = custom_storage.create_track(user_id=user_id, title="P1A4 Track")
    wav_1 = _make_pcm_wav_bytes(0.4)
    wav_2 = _make_pcm_wav_bytes(0.7)
    take = custom_storage.create_take(user_id, trk.id, wav_1, "p1a4.wav", ".wav", label="Master", is_master=True)

    # Protected victim album whose files must NEVER be touched by another transaction's swap/rollback
    victim_alb = custom_storage.create_album(user_id=user_id, title="Victim LP")
    victim_alb = custom_storage.add_album_entry(user_id, victim_alb.id, trk.id, expected_revision=victim_alb.revision, take_id=take.id)
    victim_entry_id = victim_alb.tracks[0].id
    victim_entry_dir = custom_storage._get_album_entry_dir(user_id, victim_alb.id, victim_entry_id)
    (victim_entry_dir / "audio.wav").write_bytes(wav_2)

    # 1. Directory replacement after staging (before promotion) on NEW-ENTRY creation (add_album_entry)
    alb_new = custom_storage.create_album(user_id=user_id, title="New Entry Swap LP")
    alb_new_dir = custom_storage._get_album_dir(user_id, alb_new.id)
    moved_new_dir = tmp_path / "moved_new_entry_alb"
    orig_link = ps_mod.os.link
    swapped_new = False

    def swap_before_new_promote(src, dst, *args, **kwargs):
        nonlocal swapped_new
        if not swapped_new and str(dst) == "audio.wav":
            swapped_new = True
            alb_new_dir.rename(moved_new_dir)
            alb_new_dir.symlink_to(custom_storage._get_album_dir(user_id, victim_alb.id))
        return orig_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(ps_mod.os, "link", swap_before_new_promote)
    res_new = custom_storage.add_album_entry(user_id, alb_new.id, trk.id, expected_revision=alb_new.revision, take_id=take.id)
    monkeypatch.undo()
    assert res_new is not None and len(res_new.tracks) == 1
    assert (victim_entry_dir / "audio.wav").read_bytes() == wav_2
    assert (moved_new_dir / "entries" / ps_mod.get_album_entry_dir_name(res_new.tracks[0].id) / "audio.wav").read_bytes() == wav_1

    # 2. Directory replacement between promotion steps + rollback encountering another transaction's inode on PLACEHOLDER assignment
    alb_ph = custom_storage.create_album(user_id=user_id, title="Placeholder Swap LP")
    alb_ph = custom_storage.add_album_entry(user_id, alb_ph.id, trk.id, expected_revision=alb_ph.revision, take_id=None)
    ph_id = alb_ph.tracks[0].id
    ph_entry_dir = custom_storage._get_album_entry_dir(user_id, alb_ph.id, ph_id)
    moved_ph_dir = tmp_path / "moved_ph_alb"
    alb_ph_dir = custom_storage._get_album_dir(user_id, alb_ph.id)

    orig_publish = ps_mod._publish_manifest_atomic_in_dir
    link_calls = 0

    def swap_between_promote_and_fail(src, dst, *args, **kwargs):
        nonlocal link_calls
        link_calls += 1
        res = orig_link(src, dst, *args, **kwargs)
        if link_calls == 1:
            # Swap album_dir path between audio promotion and snapshot promotion
            alb_ph_dir.rename(moved_ph_dir)
            alb_ph_dir.symlink_to(custom_storage._get_album_dir(user_id, victim_alb.id))
        return res

    def fail_before_manifest_replace(dir_fd, filename, data):
        # Replace promoted audio.wav in moved_ph_dir with a different file/inode before rollback runs!
        moved_entry = moved_ph_dir / "entries" / ps_mod.get_album_entry_dir_name(ph_id)
        (moved_entry / "audio.wav").unlink()
        (moved_entry / "audio.wav").write_bytes(b"FOREIGN_TX_FILE")
        raise ps_mod.ManifestPublishError("Simulated pre-publish failure", publish_state=ps_mod.ManifestPublishState.NOT_PUBLISHED)

    monkeypatch.setattr(ps_mod.os, "link", swap_between_promote_and_fail)
    monkeypatch.setattr(ps_mod, "_publish_manifest_atomic_in_dir", fail_before_manifest_replace)
    with pytest.raises(ps_mod.ManifestPublishError, match="Simulated pre-publish failure"):
        custom_storage.update_album_entry(user_id, alb_ph.id, ph_id, expected_revision=alb_ph.revision, take_id=take.id)
    monkeypatch.undo()

    moved_entry = moved_ph_dir / "entries" / ps_mod.get_album_entry_dir_name(ph_id)
    # Rollback MUST preserve FOREIGN_TX_FILE (different inode!) and unlink only our snapshot.json
    assert (moved_entry / "audio.wav").read_bytes() == b"FOREIGN_TX_FILE"
    assert not (moved_entry / "snapshot.json").exists()
    assert (victim_entry_dir / "audio.wav").read_bytes() == wav_2

    # 3. Manifest replacement before descriptor acquisition: swapping draft dir to finalized album dir -> AlbumFinalizedError
    draft_fin = custom_storage.create_album(user_id=user_id, title="SwapToFinalized LP")
    draft_fin = custom_storage.add_album_entry(user_id, draft_fin.id, trk.id, expected_revision=draft_fin.revision, take_id=take.id)
    fin_target = custom_storage.create_album(user_id=user_id, title="AlreadyFinalized LP")
    fin_target = custom_storage.add_album_entry(user_id, fin_target.id, trk.id, expected_revision=fin_target.revision, take_id=take.id)
    fin_target = custom_storage.finalize_album(user_id, fin_target.id, expected_revision=fin_target.revision)

    draft_fin_dir = custom_storage._get_album_dir(user_id, draft_fin.id)
    fin_target_dir = custom_storage._get_album_dir(user_id, fin_target.id)
    draft_fin_backup = tmp_path / "draft_fin_backup"
    draft_fin_dir.rename(draft_fin_backup)
    draft_fin_dir.symlink_to(fin_target_dir)
    with pytest.raises(AlbumManifestCorruptError):
        custom_storage.finalize_album(user_id, draft_fin.id, expected_revision=draft_fin.revision)
    draft_fin_dir.unlink()
    draft_fin_backup.rename(draft_fin_dir)

    # 4. Empty-entry removal and empty-album deletion succeed via dir_fd even when sibling/unrelated paths exist
    empty_alb = custom_storage.create_album(user_id=user_id, title="Empty Cleanup LP")
    empty_alb = custom_storage.add_album_entry(user_id, empty_alb.id, trk.id, expected_revision=empty_alb.revision, take_id=None)
    e_ph_id = empty_alb.tracks[0].id
    empty_alb = custom_storage.remove_album_entry(user_id, empty_alb.id, e_ph_id, expected_revision=empty_alb.revision)
    assert empty_alb.tracks == []
    assert custom_storage.delete_album(user_id, empty_alb.id, expected_revision=empty_alb.revision) is True
    assert not custom_storage._get_album_dir_path(user_id, empty_alb.id).exists()


def test_phase1a5_manifest_validation_dir_cleanup_and_fd_ownership(custom_storage, tmp_path, monkeypatch):
    """Verify Phase 1A.5: pre-publication AlbumMetadata validation (1000th entry & invalid metadata), newly created entry_dir cleanup, source-open/snapshot fd cleanup, rejected manifest fd closure, and ro_fd snapshot construction."""
    import builtins
    import hashlib
    import json
    import backend.project_storage as ps_mod

    user_id = "user_p1a5"
    trk = custom_storage.create_track(user_id=user_id, title="P1A5 Track")
    wav_ok = _make_pcm_wav_bytes(0.4)
    take = custom_storage.create_take(user_id, trk.id, wav_ok, "p1a5.wav", ".wav", label="Master", is_master=True)

    # 1. Pre-publication validation: 1,000th entry exceeds AlbumTrackEntry(track_number le=999)
    #    Must fail BEFORE audio promotion or album.json publication, and clean up newly created entry_dir.
    alb_999 = custom_storage.create_album(user_id=user_id, title="Max Tracks LP")
    alb_999_dir = custom_storage._get_album_dir(user_id, alb_999.id)
    alb_999 = custom_storage.add_album_entry(user_id, alb_999.id, trk.id, expected_revision=alb_999.revision, take_id=take.id)
    rec_entry_id = alb_999.tracks[0].id

    # Seed 998 placeholders in manifest (total 999) so the next add_album_entry creates the 1000th entry
    raw_999 = json.loads((alb_999_dir / "album.json").read_text(encoding="utf-8"))
    for i in range(2, 1000):
        raw_999["tracks"].append({
            "id": generate_album_entry_id(),
            "track_number": i,
            "disc_number": 1,
            "track_id": trk.id,
            "take_id": None,
            "is_placeholder": True,
            "has_owned_audio": False,
            "snapshot": None,
        })
    (alb_999_dir / "album.json").write_text(json.dumps(raw_999), encoding="utf-8")
    before_entry_dirs = {p.name for p in (alb_999_dir / "entries").iterdir()}

    with pytest.raises(ProjectValidationError, match="Invalid album manifest candidate"):
        custom_storage.add_album_entry(user_id, alb_999.id, trk.id, expected_revision=alb_999.revision, take_id=take.id)

    after_entry_dirs = {p.name for p in (alb_999_dir / "entries").iterdir()}
    assert after_entry_dirs == before_entry_dirs
    assert len(custom_storage.get_album(user_id, alb_999.id).tracks) == 999
    assert custom_storage.get_album_entry_audio_path(user_id, alb_999.id, rec_entry_id).read_bytes() == wav_ok

    # 1b. Invalid metadata update (empty title violates min_length=1) fails before publication
    alb_meta = custom_storage.create_album(user_id=user_id, title="Valid Title")
    with pytest.raises(ProjectValidationError, match="Invalid album manifest candidate"):
        custom_storage.update_album(user_id, alb_meta.id, {"expected_revision": alb_meta.revision, "title": ""})
    assert custom_storage.get_album(user_id, alb_meta.id).title == "Valid Title"

    # 2. Newly created entry_dir cleanup + audio_fd closure on source-open PermissionError (no orphan blocking finalization!)
    alb_fin = custom_storage.create_album(user_id=user_id, title="No Orphan LP")
    alb_fin = custom_storage.add_album_entry(user_id, alb_fin.id, trk.id, expected_revision=alb_fin.revision, take_id=take.id)
    src_audio_path = custom_storage.get_take_audio_path(user_id, trk.id, take.id)
    orig_open = builtins.open
    fd_count_before = len(os.listdir("/proc/self/fd"))

    def fail_source_open(file, mode="r", *args, **kwargs):
        if Path(file) == src_audio_path and "rb" in str(mode):
            raise PermissionError("Simulated EACCES opening source audio")
        return orig_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_source_open)
    with pytest.raises(PermissionError, match="Simulated EACCES"):
        custom_storage.add_album_entry(user_id, alb_fin.id, trk.id, expected_revision=alb_fin.revision, take_id=take.id)
    monkeypatch.undo()

    fd_count_after = len(os.listdir("/proc/self/fd"))
    assert fd_count_after <= fd_count_before
    # Only the 1st entry's dir exists; failed 2nd entry dir was cleaned up so finalization succeeds!
    assert len(list((custom_storage._get_album_dir(user_id, alb_fin.id) / "entries").iterdir())) == 1
    finalized = custom_storage.finalize_album(user_id, alb_fin.id, expected_revision=alb_fin.revision)
    assert finalized.is_finalized is True

    # 3. Snapshot staging failure (json.dump / flush / fsync / fchmod) immediately cleans up staging_snap & created entry_dir
    alb_snap_fail = custom_storage.create_album(user_id=user_id, title="Snap Fail LP")
    orig_json_dump = ps_mod.json.dump

    def fail_snap_dump(obj, fp, *args, **kwargs):
        if isinstance(obj, dict) and "audio_sha256" in obj:
            raise OSError("Simulated json.dump failure")
        return orig_json_dump(obj, fp, *args, **kwargs)

    monkeypatch.setattr(ps_mod.json, "dump", fail_snap_dump)
    with pytest.raises(OSError, match="Simulated json.dump failure"):
        custom_storage.add_album_entry(user_id, alb_snap_fail.id, trk.id, expected_revision=alb_snap_fail.revision, take_id=take.id)
    monkeypatch.undo()
    snap_entries_dir = custom_storage._get_album_dir(user_id, alb_snap_fail.id) / "entries"
    assert not snap_entries_dir.exists() or list(snap_entries_dir.iterdir()) == []

    # 4. Rejected nonregular manifest in _read_album_from_dir_fd closes mfd without leaking descriptors
    alb_bad_m = custom_storage.create_album(user_id=user_id, title="Bad Manifest Type LP")
    bad_m_dir = custom_storage._get_album_dir(user_id, alb_bad_m.id)
    (bad_m_dir / "album.json").unlink()
    (bad_m_dir / "album.json").mkdir()
    fd_before_m = len(os.listdir("/proc/self/fd"))
    with pytest.raises(AlbumManifestCorruptError, match="not a regular file"):
        custom_storage.get_album(user_id, alb_bad_m.id)
    assert len(os.listdir("/proc/self/fd")) <= fd_before_m

    # 5. Snapshot construction uses retained ro_fd (directory swap before _build_take_snapshot cannot inspect wrong file)
    alb_ro = custom_storage.create_album(user_id=user_id, title="RoFd Snap LP")
    alb_ro_dir = custom_storage._get_album_dir(user_id, alb_ro.id)
    moved_ro_dir = tmp_path / "moved_ro_dir"
    fake_ext_dir = tmp_path / "fake_ext_dir"
    fake_ext_dir.mkdir()
    orig_build_snap = custom_storage._build_take_snapshot

    def swap_before_build_snap(*args, **kwargs):
        if alb_ro_dir.exists() and not alb_ro_dir.is_symlink():
            alb_ro_dir.rename(moved_ro_dir)
            alb_ro_dir.symlink_to(fake_ext_dir)
        return orig_build_snap(*args, **kwargs)

    monkeypatch.setattr(custom_storage, "_build_take_snapshot", swap_before_build_snap)
    res_ro = custom_storage.add_album_entry(user_id, alb_ro.id, trk.id, expected_revision=alb_ro.revision, take_id=take.id)
    monkeypatch.undo()
    assert res_ro.tracks[0].snapshot.audio_sha256 == hashlib.sha256(wav_ok).hexdigest()
    assert res_ro.tracks[0].snapshot.size_bytes == len(wav_ok)


def test_phase1a6_staging_setup_failures_cleanup_created_dirs(custom_storage: ProjectStorageManager, tmp_path: Path, monkeypatch):
    """Phase 1A.6 P2-1: Failures after entry_dir/entries creation clean up newly created empty dirs."""
    import io
    import wave
    import backend.project_storage as ps_mod

    user_id = "u_1a6_stage_cleanup"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)
    wav_ok = buf.getvalue()

    trk = custom_storage.create_track(user_id=user_id, title="Stage Cleanup Track")
    take = custom_storage.create_take(
        user_id=user_id, track_id=trk.id, audio_bytes=wav_ok, original_filename="take.wav", extension=".wav"
    )

    # 1. os.open failing when creating .staging_*_audio* cleans up newly created entry_dir
    alb1 = custom_storage.create_album(user_id=user_id, title="Open Staging Audio Fail")
    alb1_dir = custom_storage._get_album_dir(user_id, alb1.id)
    orig_os_open = ps_mod.os.open

    def fail_staging_audio_open(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".staging_") and "_audio" in path:
            raise OSError("Simulated EACCES creating staging audio file")
        return orig_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(ps_mod.os, "open", fail_staging_audio_open)
    with pytest.raises(OSError, match="Simulated EACCES creating staging audio file"):
        custom_storage.add_album_entry(user_id, alb1.id, trk.id, expected_revision=alb1.revision, take_id=take.id)
    monkeypatch.undo()
    assert list((alb1_dir / "entries").iterdir()) == []
    assert custom_storage.get_album(user_id, alb1.id).revision == alb1.revision

    # 2. os.listdir(entry_dir_fd) failure cleans up newly created entry_dir, preserving foreign dir
    alb2 = custom_storage.create_album(user_id=user_id, title="Listdir Fail")
    alb2_dir = custom_storage._get_album_dir(user_id, alb2.id)
    foreign_dir = alb2_dir / "foreign_keep"
    foreign_dir.mkdir()
    (foreign_dir / "keep.txt").write_text("keep", encoding="utf-8")
    orig_listdir = ps_mod.os.listdir
    listdir_calls = {"n": 0}

    def fail_entry_listdir(arg):
        if isinstance(arg, int):
            listdir_calls["n"] += 1
            if listdir_calls["n"] == 1:
                raise OSError("Simulated EIO listing entry_dir_fd")
        return orig_listdir(arg)

    monkeypatch.setattr(ps_mod.os, "listdir", fail_entry_listdir)
    with pytest.raises(OSError, match="Simulated EIO listing entry_dir_fd"):
        custom_storage.add_album_entry(user_id, alb2.id, trk.id, expected_revision=alb2.revision, take_id=take.id)
    monkeypatch.undo()
    assert list((alb2_dir / "entries").iterdir()) == []
    assert (foreign_dir / "keep.txt").read_text(encoding="utf-8") == "keep"

    # 3. Entry-directory acquisition failure cleans up newly created entries dir (when entries did not exist)
    alb3 = custom_storage.create_album(user_id=user_id, title="Entry Dir Acquire Fail")
    alb3_dir = custom_storage._get_album_dir(user_id, alb3.id)
    (alb3_dir / "entries").rmdir()
    orig_tracked = custom_storage._open_or_create_subdir_fd_tracked
    call_count = {"n": 0}

    def fail_on_entry_subdir(parent_fd, name):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("Simulated failure acquiring entry_dir")
        return orig_tracked(parent_fd, name)

    monkeypatch.setattr(custom_storage, "_open_or_create_subdir_fd_tracked", fail_on_entry_subdir)
    with pytest.raises(OSError, match="Simulated failure acquiring entry_dir"):
        custom_storage.add_album_entry(user_id, alb3.id, trk.id, expected_revision=alb3.revision, take_id=take.id)
    monkeypatch.undo()
    assert not (alb3_dir / "entries").exists()


def test_phase1a6_snapshot_metadata_fidelity_and_duration_rounding(custom_storage: ProjectStorageManager, tmp_path: Path):
    """Phase 1A.6 P2-2 & P2-3: Descriptor snapshot extracts full MP3/WAV/M4A tags & rounds duration to 2 decimals."""
    import io
    import wave
    import shutil
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TPE2, TCOM, TCON, TDRC, TRCK, TPOS, TBPM, COMM, USLT
    from mutagen.wave import WAVE
    from mutagen.mp4 import MP4
    from backend.metadata_engine import extract_metadata_and_artwork, extract_metadata_from_fd

    user_id = "u_1a6_snap_fidelity"

    # 1. Tagged WAV (ID3 + 1.234s duration -> rounds to 1.23s consistently)
    wav_path = tmp_path / "tagged_3dec.wav"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 9872)  # 9872 / 8000 = 1.234s
    wav_path.write_bytes(buf.getvalue())
    wv = WAVE(wav_path)
    wv.add_tags()
    wv.tags.add(TIT2(encoding=3, text="Wave Title"))
    wv.tags.add(TPE1(encoding=3, text="Wave Artist"))
    wv.tags.add(TALB(encoding=3, text="Wave Album"))
    wv.tags.add(TPE2(encoding=3, text="Wave AlbumArtist"))
    wv.tags.add(TCOM(encoding=3, text="Wave Composer"))
    wv.tags.add(TCON(encoding=3, text="Electronic"))
    wv.tags.add(TDRC(encoding=3, text="2026"))
    wv.tags.add(TRCK(encoding=3, text="2/10"))
    wv.tags.add(TPOS(encoding=3, text="1/2"))
    wv.tags.add(TBPM(encoding=3, text="124"))
    wv.tags.add(COMM(encoding=3, lang="eng", desc="", text="Wave Comment"))
    wv.tags.add(USLT(encoding=3, lang="eng", desc="", text="Embedded wave lyrics"))
    wv.save()

    # 2. Tagged M4A (from tests/fixtures/tone.m4a)
    m4a_src = Path(__file__).parent / "fixtures" / "tone.m4a"
    m4a_path = tmp_path / "tagged.m4a"
    shutil.copy2(m4a_src, m4a_path)
    mp4 = MP4(m4a_path)
    mp4["\xa9nam"] = ["M4A Title"]
    mp4["\xa9ART"] = ["M4A Artist"]
    mp4["\xa9alb"] = ["M4A Album"]
    mp4["aART"] = ["M4A AlbumArtist"]
    mp4["\xa9wrt"] = ["M4A Composer"]
    mp4["\xa9gen"] = ["Synthwave"]
    mp4["\xa9day"] = ["2025"]
    mp4["trkn"] = [(3, 8)]
    mp4["disk"] = [(1, 1)]
    mp4["tmpo"] = [118]
    mp4["\xa9cmt"] = ["M4A Comment"]
    mp4["\xa9lyr"] = ["M4A Lyrics"]
    mp4.save()

    # 3. Tagged MP3 (from tests/fixtures/tone.mp3 if present, else minimal ID3 MP3)
    mp3_fixture = Path(__file__).parent / "fixtures" / "tone.mp3"
    mp3_path = tmp_path / "tagged.mp3"
    if mp3_fixture.exists():
        shutil.copy2(mp3_fixture, mp3_path)
    else:
        mp3_path.write_bytes(b"\xff\xfb\x90\x64" + b"\x00" * 413)
    id3 = ID3()
    id3.add(TIT2(encoding=3, text="MP3 Title"))
    id3.add(TPE1(encoding=3, text="MP3 Artist"))
    id3.add(TALB(encoding=3, text="MP3 Album"))
    id3.add(TPE2(encoding=3, text="MP3 AlbumArtist"))
    id3.add(TCOM(encoding=3, text="MP3 Composer"))
    id3.add(TCON(encoding=3, text="Ambient"))
    id3.add(TDRC(encoding=3, text="2024"))
    id3.add(TRCK(encoding=3, text="1/5"))
    id3.add(TPOS(encoding=3, text="1/1"))
    id3.add(TBPM(encoding=3, text="95"))
    id3.add(COMM(encoding=3, lang="eng", desc="", text="MP3 Comment"))
    id3.add(USLT(encoding=3, lang="eng", desc="", text="MP3 Embedded Lyrics"))
    id3.save(mp3_path)

    # Compare extract_metadata_from_fd vs extract_metadata_and_artwork for all 3 formats
    for p in (wav_path, m4a_path, mp3_path):
        by_path = extract_metadata_and_artwork(p)
        fd = os.open(p, os.O_RDONLY)
        try:
            by_fd = extract_metadata_from_fd(fd, p.suffix)
        finally:
            os.close(fd)
        assert by_fd["metadata"] == by_path["metadata"]
        assert by_fd["duration"] == by_path["audio_info"]["duration"]

    # Verify snapshot parity, USLT vs attached lyrics precedence, and 2-decimal duration refresh parity
    trk_wav = custom_storage.create_track(user_id=user_id, title="WAV Track")
    take_wav = custom_storage.create_take(
        user_id=user_id, track_id=trk_wav.id, audio_bytes=wav_path.read_bytes(), original_filename="tagged_3dec.wav", extension=".wav"
    )
    custom_storage.save_take_lyrics(user_id, trk_wav.id, take_wav.id, "Attached fallback lyrics")

    alb = custom_storage.create_album(user_id=user_id, title="Snapshot Fidelity LP")
    alb = custom_storage.add_album_entry(user_id, alb.id, trk_wav.id, expected_revision=alb.revision, take_id=take_wav.id)
    snap_wav = alb.tracks[0].snapshot
    assert snap_wav.duration_seconds == 1.23
    assert snap_wav.embedded_metadata["title"] == "Wave Title"
    assert snap_wav.embedded_metadata["artist"] == "Wave Artist"
    assert snap_wav.embedded_metadata["track_number"] == "2"
    assert snap_wav.embedded_metadata["total_tracks"] == "10"
    assert snap_wav.embedded_metadata["bpm"] == "124"
    # Embedded USLT lyrics must take precedence over attached take lyrics
    assert snap_wav.embedded_lyrics == "Embedded wave lyrics"

    # Refresh preview on untouched source must report has_changes=False (1.23s == 1.23s)
    preview = custom_storage.preview_album_entry_refresh(user_id, alb.id, alb.tracks[0].id)
    assert preview.has_changes is False


def test_phase1a6_create_album_validation_and_publish_cleanup(custom_storage: ProjectStorageManager, monkeypatch):
    """Phase 1A.6 P2-4: create_album validates before mkdir and cleans up only on confirmed NOT_PUBLISHED."""
    import backend.project_storage as ps_mod
    from backend.project_storage import ManifestPublishError, ManifestPublishState

    user_id = "u_1a6_create_album"
    albums_root = custom_storage._get_albums_root(user_id)

    # 1. Whitespace-only title raises ProjectValidationError with zero directories created
    before_dirs = {p for p in albums_root.iterdir() if p.is_dir()}
    with pytest.raises(ProjectValidationError):
        custom_storage.create_album(user_id=user_id, title="   ")
    assert {p for p in albums_root.iterdir() if p.is_dir()} == before_dirs

    # 2. Pre-replace NOT_PUBLISHED failure cleans up newly created album_dir and entries
    def fail_not_published(dir_fd, filename, data):
        raise ManifestPublishError("Simulated pre-replace failure", publish_state=ManifestPublishState.NOT_PUBLISHED)

    monkeypatch.setattr(ps_mod, "_publish_manifest_atomic_in_dir", fail_not_published)
    with pytest.raises(ManifestPublishError, match="Simulated pre-replace failure"):
        custom_storage.create_album(user_id=user_id, title="Pre-Replace Fail LP")
    monkeypatch.undo()
    assert {p for p in albums_root.iterdir() if p.is_dir()} == before_dirs

    # 3. UNCERTAIN publication preserves album.json and album_dir without deleting
    orig_replace = ps_mod.os.replace

    def uncertain_after_replace(src, dst, *args, **kwargs):
        orig_replace(src, dst, *args, **kwargs)
        raise OSError("Simulated post-replace uncertain error")

    monkeypatch.setattr(ps_mod.os, "replace", uncertain_after_replace)
    with pytest.raises(ManifestPublishError) as exc_info:
        custom_storage.create_album(user_id=user_id, title="Uncertain Create LP")
    monkeypatch.undo()
    assert exc_info.value.publish_state == ManifestPublishState.UNCERTAIN
    after_dirs = {p for p in albums_root.iterdir() if p.is_dir()} - before_dirs
    assert len(after_dirs) == 1
    created_dir = next(iter(after_dirs))
    assert (created_dir / "album.json").is_file()
    assert (created_dir / "entries").is_dir()


def test_phase1a6_snapshot_staging_faults_separate(custom_storage: ProjectStorageManager, monkeypatch):
    """Phase 1A.6 Test Integrity: Separate fault-injection tests for json.dump, flush, fsync, and fchmod."""
    import io
    import wave
    import backend.project_storage as ps_mod

    user_id = "u_1a6_snap_faults"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)
    wav_ok = buf.getvalue()

    trk = custom_storage.create_track(user_id=user_id, title="Fault Track")
    take = custom_storage.create_take(
        user_id=user_id, track_id=trk.id, audio_bytes=wav_ok, original_filename="take.wav", extension=".wav"
    )

    def _setup_album_with_existing_entry(title: str):
        alb = custom_storage.create_album(user_id=user_id, title=title)
        alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=take.id)
        alb_dir = custom_storage._get_album_dir(user_id, alb.id)
        foreign = alb_dir / "foreign_artifact.txt"
        foreign.write_text("untouched", encoding="utf-8")
        return alb, alb_dir, foreign

    def _assert_preserved(alb, alb_dir: Path, foreign: Path, expected_entry_dirs: set):
        cur = custom_storage.get_album(user_id, alb.id)
        assert cur.revision == alb.revision
        assert len(cur.tracks) == 1
        assert custom_storage.get_album_entry_audio_path(user_id, alb.id, cur.tracks[0].id).read_bytes() == wav_ok
        assert foreign.read_text(encoding="utf-8") == "untouched"
        assert {p.name for p in (alb_dir / "entries").iterdir()} == expected_entry_dirs

    # A. json.dump failure
    alb_a, dir_a, foreign_a = _setup_album_with_existing_entry("Fault Dump LP")
    exp_a = {p.name for p in (dir_a / "entries").iterdir()}
    orig_dump = ps_mod.json.dump
    monkeypatch.setattr(
        ps_mod.json,
        "dump",
        lambda obj, fp, *a, **kw: (_ for _ in ()).throw(OSError("Simulated json.dump fault"))
        if isinstance(obj, dict) and "audio_sha256" in obj
        else orig_dump(obj, fp, *a, **kw),
    )
    with pytest.raises(OSError, match="Simulated json.dump fault"):
        custom_storage.add_album_entry(user_id, alb_a.id, trk.id, expected_revision=alb_a.revision, take_id=take.id)
    monkeypatch.undo()
    _assert_preserved(alb_a, dir_a, foreign_a, exp_a)

    # B. flush failure on snapshot file object
    alb_b, dir_b, foreign_b = _setup_album_with_existing_entry("Fault Flush LP")
    exp_b = {p.name for p in (dir_b / "entries").iterdir()}
    orig_fdopen = ps_mod.os.fdopen

    class _FailFlushWrapper:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

        def write(self, data):
            return self._inner.write(data)

        def flush(self):
            raise OSError("Simulated flush fault")

        def fileno(self):
            return self._inner.fileno()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def wrap_snap_flush(fd, mode="r", *args, **kwargs):
        f = orig_fdopen(fd, mode, *args, **kwargs)
        if mode == "w":
            return _FailFlushWrapper(f)
        return f

    monkeypatch.setattr(ps_mod.os, "fdopen", wrap_snap_flush)
    with pytest.raises(OSError, match="Simulated flush fault"):
        custom_storage.add_album_entry(user_id, alb_b.id, trk.id, expected_revision=alb_b.revision, take_id=take.id)
    monkeypatch.undo()
    _assert_preserved(alb_b, dir_b, foreign_b, exp_b)

    # C. os.fsync failure on snapshot staging fd (fail on 2nd fsync: 1st is audio, 2nd is snapshot)
    alb_c, dir_c, foreign_c = _setup_album_with_existing_entry("Fault Fsync LP")
    exp_c = {p.name for p in (dir_c / "entries").iterdir()}
    orig_fsync = ps_mod.os.fsync
    fsync_calls = {"n": 0}

    def fail_second_fsync(fd):
        fsync_calls["n"] += 1
        if fsync_calls["n"] == 2:
            raise OSError("Simulated fsync fault")
        return orig_fsync(fd)

    monkeypatch.setattr(ps_mod.os, "fsync", fail_second_fsync)
    with pytest.raises(OSError, match="Simulated fsync fault"):
        custom_storage.add_album_entry(user_id, alb_c.id, trk.id, expected_revision=alb_c.revision, take_id=take.id)
    monkeypatch.undo()
    _assert_preserved(alb_c, dir_c, foreign_c, exp_c)

    # D. os.fchmod failure specifically on the staged snapshot descriptor (no global call counting)
    alb_d, dir_d, foreign_d = _setup_album_with_existing_entry("Fault Fchmod LP")
    exp_d = {p.name for p in (dir_d / "entries").iterdir()}
    orig_open_d = ps_mod.os.open
    orig_close_d = ps_mod.os.close
    orig_fchmod_d = ps_mod.os.fchmod
    snap_fds: set[int] = set()
    fchmod_injected = {"hit": False}

    def track_snap_open(path, flags, *args, **kwargs):
        fd = orig_open_d(path, flags, *args, **kwargs)
        if isinstance(path, str) and path.startswith(".staging_") and path.endswith("_snapshot.json"):
            snap_fds.add(fd)
        return fd

    def track_snap_close(fd):
        snap_fds.discard(fd)
        return orig_close_d(fd)

    def fail_snap_fchmod(fd, mode):
        if fd in snap_fds:
            fchmod_injected["hit"] = True
            raise OSError("Simulated fchmod fault on staged snapshot")
        return orig_fchmod_d(fd, mode)

    monkeypatch.setattr(ps_mod.os, "open", track_snap_open)
    monkeypatch.setattr(ps_mod.os, "close", track_snap_close)
    monkeypatch.setattr(ps_mod.os, "fchmod", fail_snap_fchmod)
    with pytest.raises(OSError, match="Simulated fchmod fault on staged snapshot"):
        custom_storage.add_album_entry(user_id, alb_d.id, trk.id, expected_revision=alb_d.revision, take_id=take.id)
    monkeypatch.undo()
    assert fchmod_injected["hit"] is True
    _assert_preserved(alb_d, dir_d, foreign_d, exp_d)


def test_phase1a7_subdir_acquisition_exception_safety(custom_storage: ProjectStorageManager, tmp_path: Path, monkeypatch):
    """Phase 1A.7: _open_or_create_subdir_fd_tracked cleans up newly created empty dir and closes fds on post-mkdir failures."""
    import backend.project_storage as ps_mod
    from backend.project_storage import AlbumManifestCorruptError

    parent_dir = tmp_path / "subdir_parent"
    parent_dir.mkdir(mode=0o700)
    foreign_existing = parent_dir / "existing_dir"
    foreign_existing.mkdir(mode=0o700)
    (foreign_existing / "keep.txt").write_text("keep", encoding="utf-8")

    parent_fd = os.open(parent_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        orig_os_open = ps_mod.os.open
        orig_os_fstat = ps_mod.os.fstat

        # 1. mkdir succeeds, subsequent os.open fails -> identity cannot be established, dir is preserved, exception propagates
        open_calls = {"n": 0}

        def fail_second_open(path, flags, *args, **kwargs):
            if path == "new_child_open_fail":
                open_calls["n"] += 1
                if open_calls["n"] == 1:
                    raise FileNotFoundError("not yet")
                raise OSError("Simulated EACCES after mkdir")
            return orig_os_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(ps_mod.os, "open", fail_second_open)
        with pytest.raises(AlbumManifestCorruptError, match="Ambiguous or symlinked directory"):
            ProjectStorageManager._open_or_create_subdir_fd_tracked(parent_fd, "new_child_open_fail")
        monkeypatch.undo()
        assert (parent_dir / "new_child_open_fail").is_dir()
        assert (foreign_existing / "keep.txt").read_text(encoding="utf-8") == "keep"

        # 2. mkdir + os.open succeed, subsequent os.fstat fails -> fd is closed and dir is preserved (no identity)
        fd_count_before = len(os.listdir("/proc/self/fd"))

        def fail_fstat(fd):
            raise OSError("Simulated fstat failure after mkdir+open")

        monkeypatch.setattr(ps_mod.os, "fstat", fail_fstat)
        with pytest.raises(OSError, match="Simulated fstat failure after mkdir\\+open"):
            ProjectStorageManager._open_or_create_subdir_fd_tracked(parent_fd, "new_child_fstat_fail")
        monkeypatch.undo()
        assert len(os.listdir("/proc/self/fd")) <= fd_count_before
        assert (parent_dir / "new_child_fstat_fail").is_dir()

        # 3. Pre-existing dir: os.open succeeds, os.fstat fails -> fd is closed, pre-existing dir is NOT removed
        fd_count_before_existing = len(os.listdir("/proc/self/fd"))
        monkeypatch.setattr(ps_mod.os, "fstat", fail_fstat)
        with pytest.raises(OSError, match="Simulated fstat failure after mkdir\\+open"):
            ProjectStorageManager._open_or_create_subdir_fd_tracked(parent_fd, "existing_dir")
        monkeypatch.undo()
        assert len(os.listdir("/proc/self/fd")) <= fd_count_before_existing
        assert (foreign_existing / "keep.txt").read_text(encoding="utf-8") == "keep"
    finally:
        os.close(parent_fd)


def test_phase1a7_deterministic_legacy_album_entry_identity(custom_storage: ProjectStorageManager):
    """Phase 1A.7: Legacy album normalization uses deterministic IDs consistently across GET, list, and mutations."""
    import io
    import json
    import wave

    user_id = "u_1a7_legacy"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)
    wav_ok = buf.getvalue()

    trk1 = custom_storage.create_track(user_id=user_id, title="Track One")
    take1 = custom_storage.create_take(
        user_id=user_id, track_id=trk1.id, audio_bytes=wav_ok, original_filename="t1.wav", extension=".wav"
    )
    trk2 = custom_storage.create_track(user_id=user_id, title="Track Two")
    take2 = custom_storage.create_take(
        user_id=user_id, track_id=trk2.id, audio_bytes=wav_ok, original_filename="t2.wav", extension=".wav"
    )

    alb = custom_storage.create_album(user_id=user_id, title="Legacy LP")
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    manifest_path = alb_dir / "album.json"

    # Write a legacy manifest lacking entry 'id's, with duplicate references to trk1 and one trk2 placeholder
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["tracks"] = [
        {"track_number": 1, "disc_number": 1, "track_id": trk1.id, "take_id": None, "custom_title": None},
        {"track_number": 2, "disc_number": 1, "track_id": trk1.id, "take_id": None, "custom_title": None},
        {"track_number": 3, "disc_number": 1, "track_id": trk2.id, "take_id": None, "custom_title": None},
    ]
    raw["track_ids"] = [trk1.id, trk1.id, trk2.id]
    raw_bytes_before = json.dumps(raw, indent=2).encode("utf-8")
    manifest_path.write_bytes(raw_bytes_before)

    # 1. Repeated GETs return identical deterministic IDs, duplicate Track refs stay distinct, and manifest is not rewritten
    get1 = custom_storage.get_album(user_id, alb.id)
    get2 = custom_storage.get_album(user_id, alb.id)
    assert manifest_path.read_bytes() == raw_bytes_before
    ids1 = [e.id for e in get1.tracks]
    ids2 = [e.id for e in get2.tracks]
    assert ids1 == ids2
    assert len(set(ids1)) == 3  # Duplicate trk1 entries retain distinct identities

    # 2. Listing and detail views agree on entry identity
    listed = [a for a in custom_storage.list_albums(user_id) if a.id == alb.id][0]
    assert [e.id for e in listed.tracks] == ids1
    assert manifest_path.read_bytes() == raw_bytes_before

    # 3. Direct initial Take assignment on a legacy entry WITHOUT first saving Album headers
    assigned = custom_storage.update_album_entry(
        user_id,
        alb.id,
        ids1[0],
        expected_revision=get1.revision,
        take_id=take1.id,
    )
    assert [e.id for e in assigned.tracks] == ids1
    assert assigned.tracks[0].has_owned_audio is True
    assert assigned.tracks[0].snapshot is not None
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, ids1[0]).read_bytes() == wav_ok

    # 4. Entry metadata change + reordering/sequencing preserves unrelated entries and existing recording
    reordered = custom_storage.update_album(
        user_id,
        alb.id,
        {
            "expected_revision": assigned.revision,
            "tracks": [
                {"id": ids1[2], "track_id": trk2.id, "take_id": None, "track_number": 1, "disc_number": 1, "custom_title": "Renamed Intro"},
                {"id": ids1[0], "track_id": trk1.id, "take_id": take1.id, "track_number": 2, "disc_number": 1},
                {"id": ids1[1], "track_id": trk1.id, "take_id": None, "track_number": 3, "disc_number": 1},
            ],
        },
    )
    assert [e.id for e in reordered.tracks] == [ids1[2], ids1[0], ids1[1]]
    assert reordered.tracks[0].custom_title == "Renamed Intro"
    assert reordered.tracks[1].has_owned_audio is True
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, ids1[0]).read_bytes() == wav_ok

    # 5. Placeholder removal preserves unrelated entries and existing recording
    removed = custom_storage.remove_album_entry(user_id, alb.id, ids1[1], expected_revision=reordered.revision)
    assert [e.id for e in removed.tracks] == [ids1[2], ids1[0]]
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, ids1[0]).read_bytes() == wav_ok

    # 6. Adding a new entry to the legacy Album preserves existing entries and recordings
    added = custom_storage.add_album_entry(
        user_id, alb.id, trk2.id, expected_revision=removed.revision, take_id=take2.id
    )
    assert len(added.tracks) == 3
    assert added.tracks[0].id == ids1[2]
    assert added.tracks[1].id == ids1[0]
    assert added.tracks[2].id not in set(ids1)
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, ids1[0]).read_bytes() == wav_ok
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, added.tracks[2].id).read_bytes() == wav_ok


def test_phase1a8_persisted_entry_id_protection_and_duplicate_rejection(custom_storage: ProjectStorageManager):
    """Phase 1A.8: Persisted entry IDs are reserved before legacy ID generation; duplicate persisted IDs fail closed."""
    import io
    import json
    import wave
    from backend.project_storage import AlbumManifestCorruptError

    user_id = "u_1a8_persisted_ids"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)
    wav_ok = buf.getvalue()

    trk1 = custom_storage.create_track(user_id=user_id, title="Track One")
    take1 = custom_storage.create_take(
        user_id=user_id, track_id=trk1.id, audio_bytes=wav_ok, original_filename="t1.wav", extension=".wav"
    )
    trk2 = custom_storage.create_track(user_id=user_id, title="Track Two")
    take2 = custom_storage.create_take(
        user_id=user_id, track_id=trk2.id, audio_bytes=wav_ok, original_filename="t2.wav", extension=".wav"
    )

    alb = custom_storage.create_album(user_id=user_id, title="Collision Protection LP")
    alb = custom_storage.add_album_entry(user_id, alb.id, trk1.id, expected_revision=alb.revision, take_id=take1.id)
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    manifest_path = alb_dir / "album.json"

    # Force persisted entry's ID to equal the deterministic candidate of a preceding missing-ID entry at idx=0
    colliding_id = generate_deterministic_legacy_entry_id(alb.id, 0, trk1.id, None)
    old_entry_dir = alb_dir / "entries" / get_album_entry_dir_name(alb.tracks[0].id)
    new_entry_dir = alb_dir / "entries" / get_album_entry_dir_name(colliding_id)
    old_entry_dir.rename(new_entry_dir)

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_entry = dict(raw["tracks"][0])
    recorded_entry["id"] = colliding_id
    recorded_entry["track_number"] = 2
    if recorded_entry.get("snapshot"):
        recorded_entry["snapshot"]["entry_id"] = colliding_id
    raw["tracks"] = [
        {"track_number": 1, "disc_number": 1, "track_id": trk1.id, "take_id": None, "custom_title": None},
        recorded_entry,
    ]
    raw["track_ids"] = [trk1.id, trk1.id]
    raw_bytes = json.dumps(raw, indent=2).encode("utf-8")
    manifest_path.write_bytes(raw_bytes)

    # 1. Preceding missing-ID entry must NOT claim colliding_id; persisted entry keeps colliding_id & owned audio
    g1 = custom_storage.get_album(user_id, alb.id)
    g2 = custom_storage.get_album(user_id, alb.id)
    assert manifest_path.read_bytes() == raw_bytes
    assert [e.id for e in g1.tracks] == [e.id for e in g2.tracks]
    assert g1.tracks[1].id == colliding_id
    assert g1.tracks[1].has_owned_audio is True
    assert g1.tracks[0].id != colliding_id
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, colliding_id).read_bytes() == wav_ok
    assert (new_entry_dir / "snapshot.json").is_file()

    # 2. Listing and detail views agree on entry identities
    listed = [a for a in custom_storage.list_albums(user_id) if a.id == alb.id][0]
    assert [e.id for e in listed.tracks] == [e.id for e in g1.tracks]

    # 3. Adding a new entry before any legacy header save preserves both entries and colliding_id's recording
    added = custom_storage.add_album_entry(
        user_id, alb.id, trk2.id, expected_revision=g1.revision, take_id=take2.id
    )
    assert added.tracks[0].id == g1.tracks[0].id
    assert added.tracks[1].id == colliding_id
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, colliding_id).read_bytes() == wav_ok
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, added.tracks[2].id).read_bytes() == wav_ok

    # 4. Duplicate persisted entry IDs fail closed without rewriting manifest or deleting recordings
    dup_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    dup_entry = dict(dup_raw["tracks"][1])
    dup_raw["tracks"].append(dup_entry)
    dup_bytes = json.dumps(dup_raw, indent=2).encode("utf-8")
    manifest_path.write_bytes(dup_bytes)

    with pytest.raises(AlbumManifestCorruptError, match="failed validation"):
        custom_storage.get_album(user_id, alb.id)
    assert manifest_path.read_bytes() == dup_bytes
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, colliding_id).read_bytes() == wav_ok


def test_phase1a8_verified_directory_ownership_cleanup(custom_storage: ProjectStorageManager, tmp_path: Path, monkeypatch):
    """Phase 1A.8: Directory cleanup requires verified (st_dev, st_ino); replaced/unverified dirs are preserved."""
    import backend.project_storage as ps_mod

    parent_dir = tmp_path / "ownership_parent"
    parent_dir.mkdir(mode=0o700)
    unverified_dir = parent_dir / "unverified_empty"
    unverified_dir.mkdir(mode=0o700)
    unrelated_dir = parent_dir / "unrelated_keep"
    unrelated_dir.mkdir(mode=0o700)
    (unrelated_dir / "recording.wav").write_bytes(b"RIFFKEEP")

    parent_fd = os.open(parent_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        # 1. expected_id=None never authorizes removal
        ProjectStorageManager._cleanup_newly_created_subdir(parent_fd, "unverified_empty", None)
        assert unverified_dir.is_dir()

        # 2. Replacing newly created dir with a different empty dir before cleanup preserves the replacement dir
        orig_fchmod = ps_mod.os.fchmod
        fd_count_before = len(os.listdir("/proc/self/fd"))

        def replace_dir_then_abort(fd, mode):
            target = parent_dir / "swapped_dir"
            if target.exists():
                target.rename(parent_dir / "old_created_backup")
                target.mkdir(mode=0o700)
                raise KeyboardInterrupt("Simulated abort after dir replacement")
            return orig_fchmod(fd, mode)

        monkeypatch.setattr(ps_mod.os, "fchmod", replace_dir_then_abort)
        with pytest.raises(KeyboardInterrupt, match="Simulated abort after dir replacement"):
            ProjectStorageManager._open_or_create_subdir_fd_tracked(parent_fd, "swapped_dir")
        monkeypatch.undo()
        assert len(os.listdir("/proc/self/fd")) <= fd_count_before
        assert (parent_dir / "swapped_dir").is_dir()
        assert (unrelated_dir / "recording.wav").read_bytes() == b"RIFFKEEP"

        # 3. Confirmed transaction-owned empty dir (matching st_dev, st_ino) IS cleaned up on post-fstat failure
        def abort_after_fstat(fd, mode):
            raise KeyboardInterrupt("Simulated abort with verified ownership")

        monkeypatch.setattr(ps_mod.os, "fchmod", abort_after_fstat)
        with pytest.raises(KeyboardInterrupt, match="Simulated abort with verified ownership"):
            ProjectStorageManager._open_or_create_subdir_fd_tracked(parent_fd, "verified_owned_dir")
        monkeypatch.undo()
        assert len(os.listdir("/proc/self/fd")) <= fd_count_before
        assert not (parent_dir / "verified_owned_dir").exists()
        assert (unrelated_dir / "recording.wav").read_bytes() == b"RIFFKEEP"
    finally:
        os.close(parent_fd)


def test_phase1b_stage1_1_journal_recovery_and_fsync(custom_storage: ProjectStorageManager, tmp_path: Path, monkeypatch):
    """Phase 1B Stage 1.1: Durable journal, dir fsync UNCERTAIN semantics, recovery classification, and verified staging cleanup."""
    import hashlib
    import json
    import backend.project_storage as ps_mod
    from backend.project_models import (
        ActiveTxRecord,
        AlbumJournal,
        JournalTxState,
        RecoveryConsistency,
        StagedArtifactRef,
    )
    from backend.project_storage import (
        AlbumManifestCorruptError,
        ManifestPublishError,
        ManifestPublishState,
    )

    user_id = "u_1b_stage1_1"
    alb = custom_storage.create_album(user_id=user_id, title="Stage 1.1 Foundation LP")
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    manifest_path = alb_dir / "album.json"
    raw_manifest_before = manifest_path.read_bytes()

    # 1. GET / list_albums must never create journal.json or mutate album.json
    assert custom_storage.get_album(user_id, alb.id).id == alb.id
    assert any(a.id == alb.id for a in custom_storage.list_albums(user_id))
    assert not (alb_dir / "journal.json").exists()
    assert manifest_path.read_bytes() == raw_manifest_before

    album_dir_fd = os.open(alb_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        assert ProjectStorageManager._read_album_journal_from_dir_fd(album_dir_fd) is None

        # 2. _fsync_dir_fd failure after os.replace keeps publish_state == UNCERTAIN (never infers non-publication)
        orig_fsync_dir = ps_mod._fsync_dir_fd

        def fail_dir_fsync(dfd):
            raise OSError("Simulated EIO on directory fsync")

        monkeypatch.setattr(ps_mod, "_fsync_dir_fd", fail_dir_fsync)
        manifest_obj = json.loads(raw_manifest_before.decode("utf-8"))
        manifest_obj["last_tx_id"] = "tx_dir_fsync_test"
        with pytest.raises(ManifestPublishError) as mpe:
            ps_mod._publish_manifest_atomic_in_dir(album_dir_fd, "album.json", manifest_obj)
        monkeypatch.undo()
        assert mpe.value.publish_state == ManifestPublishState.UNCERTAIN
        # Verify actual post-replace manifest content on disk before restoring
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["last_tx_id"] == "tx_dir_fsync_test"
        ps_mod._publish_manifest_atomic_in_dir(album_dir_fd, "album.json", json.loads(raw_manifest_before.decode("utf-8")))

        # 3. Journal write/read round-trip and corrupt/symlink fail-closed checks
        staged_file = alb_dir / ".staging_tx1_audio.wav"
        staged_file.write_bytes(b"STAGED_AUDIO")
        st_s = os.stat(staged_file)
        staged_ref = StagedArtifactRef(
            rel_path=".staging_tx1_audio.wav",
            st_dev=st_s.st_dev,
            st_ino=st_s.st_ino,
            sha256=hashlib.sha256(b"STAGED_AUDIO").hexdigest(),
        )
        tx = ActiveTxRecord(
            tx_id="tx_001",
            op="replace_entry_audio",
            state=JournalTxState.PREPARED,
            expected_revision=1,
            target_revision=2,
            payload_sha256="a" * 64,
            idempotency_key="idem_001",
            staging_refs=[staged_ref],
            new_refs=["entries/e1/audio_g2.wav"],
            superseded_refs=["entries/e1/audio_g1.wav"],
        )
        journal = AlbumJournal(album_id=alb.id, active_tx=tx)
        ProjectStorageManager._write_album_journal_atomic_in_dir(album_dir_fd, journal)
        loaded_j = ProjectStorageManager._read_album_journal_from_dir_fd(album_dir_fd)
        assert loaded_j is not None and loaded_j.active_tx.tx_id == "tx_001"

        # 4. Recovery classification matrix (strictly read-only, zero cleanup)
        base_data = json.loads(manifest_path.read_text(encoding="utf-8"))

        # 4a. PREPARED + tx_id absent + rev == expected_revision -> PROVISIONAL_ABORT_CANDIDATE (staged file untouched)
        assert ProjectStorageManager._classify_album_consistency(base_data, loaded_j) == RecoveryConsistency.PROVISIONAL_ABORT_CANDIDATE
        assert staged_file.is_file()

        # 4b. PREPARED + tx_id in manifest -> CORRUPT
        corrupt_prep_data = dict(base_data, last_tx_id="tx_001")
        assert ProjectStorageManager._classify_album_consistency(corrupt_prep_data, loaded_j) == RecoveryConsistency.CORRUPT

        # 4c. PUBLISH_INTENT + tx_id absent (even when rev == expected_revision!) -> UNCERTAIN
        intent_j = loaded_j.model_copy(update={"active_tx": tx.model_copy(update={"state": JournalTxState.PUBLISH_INTENT})})
        assert ProjectStorageManager._classify_album_consistency(base_data, intent_j) == RecoveryConsistency.UNCERTAIN

        # 4d. PUBLISH_INTENT + tx_id in last_tx_id or tx_history -> PROVISIONAL_COMMIT_CANDIDATE (never authoritative COMMITTED)
        committed_data = dict(base_data, revision=2, last_tx_id="tx_001")
        assert ProjectStorageManager._classify_album_consistency(committed_data, intent_j) == RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE
        hist_data = dict(base_data, revision=3, last_tx_id="tx_002", tx_history=["tx_001", "tx_002"])
        assert ProjectStorageManager._classify_album_consistency(hist_data, intent_j) == RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE

        # 4e. UNCERTAIN state transitions
        unc_j = loaded_j.model_copy(update={"active_tx": tx.model_copy(update={"state": JournalTxState.UNCERTAIN})})
        assert ProjectStorageManager._classify_album_consistency(base_data, unc_j) == RecoveryConsistency.UNCERTAIN
        assert ProjectStorageManager._classify_album_consistency(committed_data, unc_j) == RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE

        # 4f. Missing manifest -> CORRUPT
        assert ProjectStorageManager._classify_album_consistency(None, intent_j) == RecoveryConsistency.CORRUPT

        # 5. Physical staging cleanup is disabled in Stage 1.1: preserves ALL files regardless of supplied refs
        staged_keep_2 = alb_dir / ".staging_tx1_keep2.wav"
        staged_keep_2.write_bytes(b"STAGED_KEEP_2")
        sk2_st = os.stat(staged_keep_2)
        valid_staged_ref_2 = StagedArtifactRef(
            rel_path=".staging_tx1_keep2.wav",
            st_dev=sk2_st.st_dev,
            st_ino=sk2_st.st_ino,
            sha256=hashlib.sha256(b"STAGED_KEEP_2").hexdigest(),
        )
        cleaned = ProjectStorageManager._cleanup_verified_staging_refs(
            album_dir_fd, [staged_ref, valid_staged_ref_2]
        )
        assert cleaned == []
        assert staged_file.read_bytes() == b"STAGED_AUDIO"
        assert staged_keep_2.read_bytes() == b"STAGED_KEEP_2"

        # 6. Corrupt or symlinked journal.json fails closed without deleting files
        (alb_dir / "journal.json").write_text("{not valid json", encoding="utf-8")
        with pytest.raises(AlbumManifestCorruptError, match="failed validation"):
            ProjectStorageManager._read_album_journal_from_dir_fd(album_dir_fd)
        assert staged_file.is_file()

        ext_journal = tmp_path / "external_journal.json"
        ext_journal.write_text("{}", encoding="utf-8")
        (alb_dir / "journal.json").unlink()
        (alb_dir / "journal.json").symlink_to(ext_journal)
        with pytest.raises(AlbumManifestCorruptError, match="inaccessible or a symlink"):
            ProjectStorageManager._read_album_journal_from_dir_fd(album_dir_fd)
        assert staged_file.is_file()
    finally:
        os.close(album_dir_fd)


def test_phase1b_stage1_1_adversarial_schema_and_recovery(custom_storage: ProjectStorageManager):
    """Phase 1B Stage 1.1: Adversarial schema validation, cross-album mismatches, strict types, and recovery rules."""
    import json
    from pydantic import ValidationError
    from backend.project_models import (
        ActiveTxRecord,
        AlbumJournal,
        AlbumMetadata,
        AlbumTrackEntry,
        IdempotencyRecord,
        JournalTxState,
        RecoveryConsistency,
        RetainedArtifactRecord,
        StagedArtifactRef,
    )
    from backend.project_storage import AlbumManifestCorruptError

    user_id = "u_1b_stage1_1_adv"
    alb = custom_storage.create_album(user_id=user_id, title="Adversarial Stage 1.1 LP")
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    base_data = json.loads((alb_dir / "album.json").read_text(encoding="utf-8"))

    # 1. Cross-album journal/manifest mismatch -> CORRUPT and read-time rejection
    other_tx = ActiveTxRecord(
        tx_id="tx_cross_01",
        op="replace_entry_audio",
        state=JournalTxState.PUBLISH_INTENT,
        expected_revision=1,
        target_revision=2,
        payload_sha256="a" * 64,
    )
    other_journal = AlbumJournal(album_id="alb_0000000000000000", active_tx=other_tx)
    assert ProjectStorageManager._classify_album_consistency(base_data, other_journal) == RecoveryConsistency.CORRUPT

    album_dir_fd = os.open(alb_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        ProjectStorageManager._write_album_journal_atomic_in_dir(album_dir_fd, other_journal)
        with pytest.raises(AlbumManifestCorruptError, match="identity mismatch"):
            ProjectStorageManager._read_album_journal_from_dir_fd(album_dir_fd, expected_album_id=alb.id)
    finally:
        os.close(album_dir_fd)

    # 2. Contradictory markers & revisions -> CORRUPT (never COMMITTED or ABORTED)
    own_tx = other_tx
    own_journal = AlbumJournal(album_id=alb.id, active_tx=own_tx)

    # 2a. tx_id present in manifest but manifest_rev < target_revision
    bad_rev_data = dict(base_data, revision=1, last_tx_id="tx_cross_01")
    assert ProjectStorageManager._classify_album_consistency(bad_rev_data, own_journal) == RecoveryConsistency.CORRUPT

    # 2b. PUBLISH_INTENT with tx_id absent but revision advanced unexpectedly
    moved_no_tx = dict(base_data, revision=2)
    assert ProjectStorageManager._classify_album_consistency(moved_no_tx, own_journal) == RecoveryConsistency.CORRUPT

    # 2c. delete_album committed marker missing is_deleted / delete_tx_id
    del_tx = own_tx.model_copy(update={"op": "delete_album", "state": JournalTxState.COMMITTED})
    del_journal = AlbumJournal(album_id=alb.id, active_tx=del_tx)
    del_bad_data = dict(base_data, revision=2, last_tx_id="tx_cross_01", is_deleted=False)
    assert ProjectStorageManager._classify_album_consistency(del_bad_data, del_journal) == RecoveryConsistency.CORRUPT

    # 2d. Terminal COMMITTED without tx_id, and ABORTED with tx_id
    comm_j = AlbumJournal(album_id=alb.id, active_tx=own_tx.model_copy(update={"state": JournalTxState.COMMITTED}))
    assert ProjectStorageManager._classify_album_consistency(dict(base_data, revision=2), comm_j) == RecoveryConsistency.CORRUPT
    abort_j = AlbumJournal(album_id=alb.id, active_tx=own_tx.model_copy(update={"state": JournalTxState.ABORTED}))
    assert ProjectStorageManager._classify_album_consistency(dict(base_data, revision=1, last_tx_id="tx_cross_01"), abort_j) == RecoveryConsistency.CORRUPT

    # 3. Boolean and string revision values in manifest -> CORRUPT
    for bad_rev in (True, False, "1", "2", 1.5, 0):
        assert ProjectStorageManager._classify_album_consistency(dict(base_data, revision=bad_rev), own_journal) == RecoveryConsistency.CORRUPT
        assert ProjectStorageManager._classify_album_consistency(dict(base_data, revision=bad_rev), None) == RecoveryConsistency.CORRUPT

    # 4. Schema validation: strict types, SHA-256, path traversal, unsafe basenames, ref conflicts, idempotency
    for bad_int in (True, "2"):
        with pytest.raises(ValidationError):
            ActiveTxRecord(
                tx_id="tx_1", op="replace_entry_audio", state=JournalTxState.PREPARED,
                expected_revision=bad_int, target_revision=3, payload_sha256="a" * 64,
            )
        with pytest.raises(ValidationError):
            AlbumTrackEntry(track_number=1, track_id="trk_1234567890abcdef", active_generation=bad_int)

    with pytest.raises(ValidationError):
        ActiveTxRecord(
            tx_id="tx_1", op="replace_entry_audio", state=JournalTxState.PREPARED,
            expected_revision=2, target_revision=2, payload_sha256="a" * 64,
        )

    for bad_sha in ("z" * 64, "a" * 63):
        with pytest.raises(ValidationError):
            RetainedArtifactRecord(
                rel_path="entries/e1/audio_g1.wav", sha256=bad_sha, kind="audio",
                superseded_by_tx_id="tx_1", superseded_at_rev=2,
            )

    for bad_path in ("../escape.wav", "/abs/path.wav", "entries/../bad.wav", "entries\\win.wav"):
        with pytest.raises(ValidationError):
            RetainedArtifactRecord(
                rel_path=bad_path, sha256="a" * 64, kind="audio",
                superseded_by_tx_id="tx_1", superseded_at_rev=2,
            )

    for bad_staged in (".staging_../evil.wav", "entries/not_staging.wav"):
        with pytest.raises(ValidationError):
            StagedArtifactRef(rel_path=bad_staged, st_dev=1, st_ino=2, sha256="a" * 64)

    for bad_active_name in ("../cover.jpg", "sub/cover.jpg", ".hidden.jpg", ".."):
        with pytest.raises(ValidationError):
            AlbumMetadata(id=alb.id, title="Test", active_cover_filename=bad_active_name)
        with pytest.raises(ValidationError):
            AlbumTrackEntry(track_number=1, track_id="trk_1234567890abcdef", active_audio_filename=bad_active_name)

    with pytest.raises(ValidationError):
        ActiveTxRecord(
            tx_id="tx_1", op="replace_entry_audio", state=JournalTxState.PREPARED,
            expected_revision=1, target_revision=2, payload_sha256="a" * 64,
            new_refs=["entries/e1/audio_g2.wav"], superseded_refs=["entries/e1/audio_g2.wav"],
        )

    with pytest.raises(ValidationError):
        IdempotencyRecord(
            key="k1", op="replace_entry_audio", expected_revision=1,
            payload_sha256="a" * 64, result_revision=None, tx_id="tx_1",
            outcome=JournalTxState.COMMITTED,
        )

    # 5. Legacy manifest without optional Phase 1B fields still loads cleanly
    legacy_meta = AlbumMetadata(id=alb.id, title="Legacy Clean", tracks=[AlbumTrackEntry(track_number=1, track_id="trk_1234567890abcdef")])
    assert legacy_meta.last_tx_id is None
    assert legacy_meta.tracks[0].active_generation is None


@pytest.mark.parametrize(
    "manifest_patch,expected_verdict",
    [
        # Valid historical X (target_rev=2) followed by Y (rev=3) -> PROVISIONAL_COMMIT_CANDIDATE
        ({"revision": 3, "last_tx_id": "tx_y", "tx_history": ["tx_x", "tx_y"]}, "provisional_commit_candidate"),
        # Contradictory X/Y marker ordering at same revision (rev=2 < 2 + 1) -> CORRUPT
        ({"revision": 2, "last_tx_id": "tx_y", "tx_history": ["tx_x", "tx_y"]}, "corrupt"),
        # Contradictory last_tx_id != tx_history[-1] -> CORRUPT
        ({"revision": 3, "last_tx_id": "tx_x", "tx_history": ["tx_x", "tx_y"]}, "corrupt"),
        # Non-journaled Phase 1A revision advancement after tx_x (rev=4 >= 2) -> PROVISIONAL_COMMIT_CANDIDATE
        ({"revision": 4, "last_tx_id": "tx_x", "tx_history": ["tx_x"]}, "provisional_commit_candidate"),
        # Duplicate tx_history -> CORRUPT
        ({"revision": 3, "last_tx_id": "tx_x", "tx_history": ["tx_x", "tx_x"]}, "corrupt"),
        # Non-deletion tx_x appearing only as delete_tx_id -> CORRUPT
        ({"revision": 2, "is_deleted": True, "delete_tx_id": "tx_x"}, "corrupt"),
        # is_deleted=False with populated delete_tx_id -> CORRUPT
        ({"revision": 2, "last_tx_id": "tx_x", "is_deleted": False, "delete_tx_id": "tx_x"}, "corrupt"),
    ],
)
def test_phase1b_stage1_1_marker_provenance_matrix(
    custom_storage: ProjectStorageManager, manifest_patch, expected_verdict
):
    """Phase 1B Stage 1.1: Explicit current vs. historical marker provenance and deletion marker checks."""
    import json
    from backend.project_models import ActiveTxRecord, AlbumJournal, JournalTxState, RecoveryConsistency

    user_id = "u_1b_s1_1_matrix"
    alb = custom_storage.create_album(user_id=user_id, title="Provenance Matrix LP")
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    base_data = json.loads((alb_dir / "album.json").read_text(encoding="utf-8"))

    tx_x = ActiveTxRecord(
        tx_id="tx_x",
        op="replace_entry_audio",
        state=JournalTxState.PUBLISH_INTENT,
        expected_revision=1,
        target_revision=2,
        payload_sha256="a" * 64,
    )
    journal = AlbumJournal(album_id=alb.id, active_tx=tx_x)
    patched = dict(base_data, **manifest_patch)
    verdict = ProjectStorageManager._classify_album_consistency(patched, journal)
    assert verdict == RecoveryConsistency(expected_verdict)


def test_phase1b_stage1_1_no_journal_markers_tokens_conflicts_and_phase1a_dir_fsync(
    custom_storage: ProjectStorageManager, monkeypatch
):
    """Phase 1B Stage 1.1: No-journal validation, journal conflicts, fullmatch tokens, and Phase 1A dir fsync."""
    import json
    from pydantic import ValidationError
    import backend.project_storage as ps_mod
    from backend.project_models import (
        ActiveTxRecord,
        AlbumJournal,
        IdempotencyRecord,
        JournalTxState,
        RecoveryConsistency,
        RetainedArtifactRecord,
        StagedArtifactRef,
    )
    from backend.project_storage import ManifestPublishError, ManifestPublishState

    user_id = "u_1b_s1_1_extra"
    alb = custom_storage.create_album(user_id=user_id, title="Extra Invariants LP")
    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    base_data = json.loads((alb_dir / "album.json").read_text(encoding="utf-8"))

    # 1. Missing journal: legacy manifest (both tracks=[] and absent tracks) -> CONSISTENT_IDLE
    assert ProjectStorageManager._classify_album_consistency(base_data, None) == RecoveryConsistency.CONSISTENT_IDLE
    legacy_only = dict(base_data)
    legacy_only.pop("last_tx_id", None)
    legacy_only.pop("tx_history", None)
    legacy_only.pop("is_deleted", None)
    legacy_only.pop("delete_tx_id", None)
    legacy_only.pop("tracks", None)
    legacy_only["track_ids"] = ["trk_1234567890abcdef"]
    assert ProjectStorageManager._classify_album_consistency(legacy_only, None) == RecoveryConsistency.CONSISTENT_IDLE

    valid_tx = ActiveTxRecord(
        tx_id="tx_malformed_chk", op="replace_entry_audio", state=JournalTxState.PREPARED,
        expected_revision=1, target_revision=2, payload_sha256="a" * 64,
    )
    valid_journal = AlbumJournal(album_id=alb.id, active_tx=valid_tx)
    assert ProjectStorageManager._classify_album_consistency(
        legacy_only, valid_journal
    ) == RecoveryConsistency.PROVISIONAL_ABORT_CANDIDATE

    for bad_manifest in (
        dict(base_data, is_deleted=False, delete_tx_id="tx_orphan"),
        dict(base_data, tx_history=["tx_a", "tx_a"], last_tx_id="tx_a"),
        dict(base_data, tx_history=["tx_a"], last_tx_id=None),
        dict(base_data, tx_history=["tx_a", "tx_b"], last_tx_id="tx_a"),
        dict(base_data, last_tx_id="tx_bad\n"),
        dict(base_data, title=""),  # Malformed AlbumMetadata structure
        dict(base_data, tracks="not_a_list"),  # Non-list tracks
        dict(base_data, tracks={"0": {"track_id": "trk_1234567890abcdef"}}),  # Non-list tracks dict
        dict(base_data, tracks=["non_dict_entry"]),  # Non-object track entry
        dict(base_data, tracks=[123]),  # Non-object track entry int
        dict(base_data, tracks=[{  # Contradictory placeholder + snapshot/has_owned_audio
            "track_id": "trk_1234567890abcdef",
            "take_id": None,
            "snapshot": {"entry_id": "ale_1"},
            "has_owned_audio": True,
        }]),
    ):
        assert ProjectStorageManager._classify_album_consistency(bad_manifest, None) == RecoveryConsistency.CORRUPT
        assert ProjectStorageManager._classify_album_consistency(bad_manifest, valid_journal) == RecoveryConsistency.CORRUPT

    # 1c. Historical deletion without restoration evidence -> CORRUPT
    hist_del_tx = ActiveTxRecord(
        tx_id="tx_del_hist", op="delete_album", state=JournalTxState.COMMITTED,
        expected_revision=1, target_revision=2, payload_sha256="a" * 64,
    )
    assert ProjectStorageManager._classify_album_consistency(
        dict(base_data, revision=3, last_tx_id="tx_later", tx_history=["tx_del_hist", "tx_later"], is_deleted=False),
        AlbumJournal(album_id=alb.id, active_tx=hist_del_tx),
    ) == RecoveryConsistency.CORRUPT

    # 2. Trailing-newline identifiers rejected by fullmatch()
    with pytest.raises(ValidationError):
        ActiveTxRecord(
            tx_id="tx_nl\n", op="replace_entry_audio", state=JournalTxState.PREPARED,
            expected_revision=1, target_revision=2, payload_sha256="a" * 64,
        )
    with pytest.raises(ValidationError):
        ActiveTxRecord(
            tx_id="tx_ok", op="replace_entry_audio", state=JournalTxState.PREPARED,
            expected_revision=1, target_revision=2, payload_sha256=("a" * 63) + "a\n",
        )
    with pytest.raises(ValidationError):
        IdempotencyRecord(
            key="idem_nl\n", op="replace_entry_audio", expected_revision=1,
            payload_sha256="a" * 64, result_revision=2, tx_id="tx_1",
            outcome=JournalTxState.COMMITTED,
        )

    # 3. Duplicate staging paths with conflicting inode/digest rejected
    with pytest.raises(ValidationError):
        ActiveTxRecord(
            tx_id="tx_dup_stg", op="replace_entry_audio", state=JournalTxState.PREPARED,
            expected_revision=1, target_revision=2, payload_sha256="a" * 64,
            staging_refs=[
                StagedArtifactRef(rel_path=".staging_same.wav", st_dev=1, st_ino=10, sha256="a" * 64),
                StagedArtifactRef(rel_path=".staging_same.wav", st_dev=1, st_ino=11, sha256="b" * 64),
            ],
        )

    # 4. Duplicate idempotency keys, outcome contradictions, result_revision mismatch, & retained/active overlap rejected
    with pytest.raises(ValidationError):
        AlbumJournal(
            album_id=alb.id,
            idempotency_records=[
                IdempotencyRecord(
                    key="k_dup", op="replace_entry_audio", expected_revision=1,
                    payload_sha256="a" * 64, result_revision=2, tx_id="tx_1", outcome=JournalTxState.COMMITTED,
                ),
                IdempotencyRecord(
                    key="k_dup", op="replace_entry_audio", expected_revision=1,
                    payload_sha256="b" * 64, result_revision=2, tx_id="tx_2", outcome=JournalTxState.COMMITTED,
                ),
            ],
        )
    with pytest.raises(ValidationError):
        AlbumJournal(
            album_id=alb.id,
            active_tx=ActiveTxRecord(
                tx_id="tx_1", op="replace_entry_audio", state=JournalTxState.PUBLISH_INTENT,
                expected_revision=1, target_revision=2, payload_sha256="a" * 64, idempotency_key="k_1",
            ),
            idempotency_records=[
                IdempotencyRecord(
                    key="k_1", op="replace_entry_audio", expected_revision=1,
                    payload_sha256="a" * 64, result_revision=2, tx_id="tx_1", outcome=JournalTxState.COMMITTED,
                ),
            ],
        )
    with pytest.raises(ValidationError):
        AlbumJournal(
            album_id=alb.id,
            active_tx=ActiveTxRecord(
                tx_id="tx_1", op="replace_entry_audio", state=JournalTxState.COMMITTED,
                expected_revision=1, target_revision=2, payload_sha256="a" * 64, idempotency_key="k_1",
            ),
            idempotency_records=[
                IdempotencyRecord(
                    key="k_1", op="replace_entry_audio", expected_revision=1,
                    payload_sha256="a" * 64, result_revision=3, tx_id="tx_1", outcome=JournalTxState.COMMITTED,
                ),
            ],
        )
    with pytest.raises(ValidationError):
        AlbumJournal(
            album_id=alb.id,
            active_tx=ActiveTxRecord(
                tx_id="tx_1", op="replace_entry_audio", state=JournalTxState.PREPARED,
                expected_revision=1, target_revision=2, payload_sha256="a" * 64,
                new_refs=["entries/e1/audio_g2.wav"],
            ),
            retained_artifacts=[
                RetainedArtifactRecord(
                    rel_path="entries/e1/audio_g2.wav", sha256="a" * 64, kind="audio",
                    superseded_by_tx_id="tx_0", superseded_at_rev=1,
                ),
            ],
        )

    # 5. Directory fsync failure through Phase 1A mutation callers: visible marker yields PROVISIONAL_COMMIT_CANDIDATE only
    monkeypatch.setattr(ps_mod, "_fsync_dir_fd", lambda _dfd: (_ for _ in ()).throw(OSError("Simulated Phase 1A dir fsync EIO")))
    with pytest.raises(OSError, match="Simulated Phase 1A dir fsync EIO"):
        custom_storage.update_album(user_id, alb.id, {"expected_revision": alb.revision, "title": "After Dir Fsync Fail"})
    monkeypatch.undo()
    updated_alb = custom_storage.get_album(user_id, alb.id)
    assert updated_alb.title == "After Dir Fsync Fail"

    post_fsync_data = json.loads((alb_dir / "album.json").read_text(encoding="utf-8"))
    post_fsync_data["last_tx_id"] = "tx_vis_after_fsync_fail"
    vis_tx = ActiveTxRecord(
        tx_id="tx_vis_after_fsync_fail", op="replace_entry_audio", state=JournalTxState.PUBLISH_INTENT,
        expected_revision=1, target_revision=2, payload_sha256="a" * 64,
    )
    assert ProjectStorageManager._classify_album_consistency(
        post_fsync_data, AlbumJournal(album_id=alb.id, active_tx=vis_tx)
    ) == RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE

    monkeypatch.setattr(ps_mod, "_fsync_dir_fd", lambda _dfd: (_ for _ in ()).throw(OSError("Simulated cover dir fsync EIO")))
    with pytest.raises(OSError, match="Simulated cover dir fsync EIO"):
        custom_storage.save_album_cover(user_id, alb.id, b"\xff\xd8\xff\xe0", expected_revision=updated_alb.revision, extension=".jpg")
    monkeypatch.undo()
    # Cover and updated manifest are both preserved on UNCERTAIN post-replace dir fsync failure
    assert (alb_dir / "cover.jpg").is_file()
    assert custom_storage.get_album(user_id, alb.id).has_cover is True


def test_phase1b_stage1_2_refresh_confirm_lock_revision_and_safety(custom_storage: ProjectStorageManager, monkeypatch):
    """Phase 1B Stage 1.2: confirm_album_entry_refresh enforces lock, strict expected_revision, dir_fd read, and entry safety."""
    import io
    import json
    import wave
    from backend.project_storage import (
        AlbumFinalizedError,
        AlbumManifestCorruptError,
        AlbumReplacementDisabledError,
        AlbumRevisionConflictError,
    )

    user_id = "u_1b_s1_2_refresh"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)
    wav_ok = buf.getvalue()

    trk = custom_storage.create_track(user_id=user_id, title="Stage 1.2 Track")
    take = custom_storage.create_take(
        user_id=user_id, track_id=trk.id, audio_bytes=wav_ok, original_filename="s12.wav", extension=".wav"
    )
    alb = custom_storage.create_album(user_id=user_id, title="Stage 1.2 LP")
    # Duplicate Track references with distinct entry IDs + one placeholder
    alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=take.id)
    alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=take.id)
    alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=None)
    entry_a, entry_b, placeholder_c = alb.tracks[0].id, alb.tracks[1].id, alb.tracks[2].id
    assert entry_a != entry_b

    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    manifest_path = alb_dir / "album.json"
    raw_before = manifest_path.read_bytes()

    # 1. Invalid expected_revision types/values rejected without touching disk or lock
    for bad_rev in (None, 0, -1, True, False, "1", 1.5):
        with pytest.raises(ProjectValidationError):
            custom_storage.confirm_album_entry_refresh(
                user_id, alb.id, entry_a, 1, "a" * 64, expected_revision=bad_rev
            )
    assert manifest_path.read_bytes() == raw_before

    # 2. Stale revision rejected with AlbumRevisionConflictError
    with pytest.raises(AlbumRevisionConflictError):
        custom_storage.confirm_album_entry_refresh(
            user_id, alb.id, entry_a, 1, "a" * 64, expected_revision=alb.revision - 1
        )
    assert manifest_path.read_bytes() == raw_before

    # 3. Missing or invalid entry IDs rejected cleanly
    with pytest.raises(ProjectValidationError):
        custom_storage.confirm_album_entry_refresh(
            user_id, alb.id, "not_an_entry_id", 1, "a" * 64, expected_revision=alb.revision
        )
    with pytest.raises(ProjectValidationError, match="not found"):
        custom_storage.confirm_album_entry_refresh(
            user_id, alb.id, "ale_0000000000000000", 1, "a" * 64, expected_revision=alb.revision
        )
    with pytest.raises(ProjectValidationError, match="placeholder"):
        custom_storage.confirm_album_entry_refresh(
            user_id, alb.id, placeholder_c, 1, "a" * 64, expected_revision=alb.revision
        )

    # 4. Lock coverage: authoritative read occurs inside _album_lock via _read_album_from_dir_fd (never _read_album_unlocked)
    events = []
    orig_lock = custom_storage._album_lock
    orig_read_fd = custom_storage._read_album_from_dir_fd

    from contextlib import contextmanager
    @contextmanager
    def tracked_lock(uid, aid):
        events.append("lock_enter")
        try:
            with orig_lock(uid, aid) as lp:
                yield lp
        finally:
            events.append("lock_exit")

    def tracked_read_fd(*args, **kwargs):
        events.append("read_fd")
        return orig_read_fd(*args, **kwargs)

    monkeypatch.setattr(custom_storage, "_album_lock", tracked_lock)
    monkeypatch.setattr(custom_storage, "_read_album_from_dir_fd", tracked_read_fd)
    monkeypatch.setattr(
        custom_storage,
        "_read_album_unlocked",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("_read_album_unlocked must not be called")),
    )
    with pytest.raises(AlbumReplacementDisabledError):
        custom_storage.confirm_album_entry_refresh(
            user_id, alb.id, entry_a, 1, "a" * 64, expected_revision=alb.revision
        )
    monkeypatch.undo()
    assert events == ["lock_enter", "read_fd", "lock_exit"]
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, entry_a).read_bytes() == wav_ok
    assert custom_storage.get_album_entry_audio_path(user_id, alb.id, entry_b).read_bytes() == wav_ok
    assert manifest_path.read_bytes() == raw_before

    # 5. Ambiguous duplicate entry ID in manifest fails closed with AlbumManifestCorruptError
    dup_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    dup_raw["tracks"][1]["id"] = entry_a
    manifest_path.write_text(json.dumps(dup_raw, indent=2), encoding="utf-8")
    with pytest.raises(AlbumManifestCorruptError):
        custom_storage.confirm_album_entry_refresh(
            user_id, alb.id, entry_a, 1, "a" * 64, expected_revision=alb.revision
        )
    manifest_path.write_bytes(raw_before)

    # 6. Finalized Album rejection
    alb_clean = custom_storage.remove_album_entry(user_id, alb.id, placeholder_c, expected_revision=alb.revision)
    fin = custom_storage.finalize_album(user_id, alb.id, expected_revision=alb_clean.revision)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.confirm_album_entry_refresh(
            user_id, alb.id, entry_a, 1, "a" * 64, expected_revision=fin.revision
        )


def test_phase1b_stage1_3a_v2_schema_and_validation(tmp_path):
    """Phase 1B Stage 1.3A: strict v1/v2 journal schemas, key ownership, entry-ID & v1 timestamp compatibility."""
    import json
    from pydantic import ValidationError
    from backend.project_models import (
        ActiveTxRecord,
        AlbumJournal,
        ArtifactRefRecord,
        ArtifactRole,
        IdempotencyRecord,
        JournalProtocol,
        JournalTxState,
        StagedArtifactRef,
        TerminalTxReceipt,
    )
    from backend.security import get_album_entry_dir_name

    album_id = "alb_13a0000000000001"
    entry_id = "ale_13a0000000000001"
    ehash = get_album_entry_dir_name(entry_id)
    other_entry_id = "ale_13a0000000000002"
    other_ehash = get_album_entry_dir_name(other_entry_id)
    sha_a = "a" * 64
    sha_b = "b" * 64
    sha_c = "c" * 64
    sha_m = "d" * 64

    # 1. Existing v1 defaults + serialized v1 journal with created_at=0 remain valid
    v1_raw_json = json.dumps({
        "album_id": album_id,
        "active_tx": {
            "tx_id": "tx_v1_01",
            "op": "replace_entry_audio",
            "state": "prepared",
            "expected_revision": 1,
            "target_revision": 2,
            "payload_sha256": sha_a,
            "new_refs": [f"entries/{ehash}/audio.wav"],
            "created_at": 0,
        },
    })
    v1_journal = AlbumJournal.model_validate_json(v1_raw_json)
    assert v1_journal.schema_version == 1
    assert v1_journal.protocol == JournalProtocol.LEGACY_PROVISIONAL
    assert v1_journal.tx_receipts == []
    assert v1_journal.active_tx is not None and v1_journal.active_tx.created_at == 0.0

    # 2. Explicit v2 construction with structured refs, staged refs, keyed + unkeyed terminal receipts
    new_audio_ref = ArtifactRefRecord(
        rel_path=f"entries/{ehash}/audio.wav",
        role=ArtifactRole.ENTRY_AUDIO,
        sha256=sha_b,
        size_bytes=2048,
        entry_id=entry_id,
        generation=2,
    )
    sup_audio_ref = ArtifactRefRecord(
        rel_path=f"entries/{ehash}/retained/gen1_audio.wav",
        role=ArtifactRole.RETAINED_BACKUP,
        sha256=sha_c,
        size_bytes=1024,
        entry_id=entry_id,
        generation=1,
    )
    staged_ref = StagedArtifactRef(
        rel_path=f"entries/{ehash}/.staging_tx_v2_03_audio.wav",
        st_dev=1,
        st_ino=42,
        sha256=sha_b,
        size_bytes=2048,
        role=ArtifactRole.ENTRY_AUDIO,
    )
    unkeyed_receipt = TerminalTxReceipt(
        tx_id="tx_v2_01",
        op="add_entry",
        outcome=JournalTxState.COMMITTED,
        expected_revision=1,
        result_revision=2,
        payload_sha256=sha_a,
        intended_manifest_sha256=sha_m,
        new_refs=[new_audio_ref],
        idempotency_key=None,
    )
    keyed_abort_receipt = TerminalTxReceipt(
        tx_id="tx_v2_02",
        op="replace_entry_audio",
        outcome=JournalTxState.ABORTED,
        expected_revision=2,
        result_revision=2,
        payload_sha256=sha_a,
        intended_manifest_sha256=sha_m,
        idempotency_key="idem_v2_02",
    )
    keyed_abort_idem = IdempotencyRecord(
        key="idem_v2_02",
        op="replace_entry_audio",
        expected_revision=2,
        payload_sha256=sha_a,
        result_revision=None,
        tx_id="tx_v2_02",
        outcome=JournalTxState.ABORTED,
    )
    v2_active_tx = ActiveTxRecord(
        tx_id="tx_v2_03",
        op="replace_entry_audio",
        state=JournalTxState.PREPARED,
        expected_revision=2,
        target_revision=3,
        payload_sha256=sha_a,
        intended_manifest_sha256=sha_m,
        idempotency_key="idem_v2_03",
        staging_refs=[staged_ref],
        new_refs=[new_audio_ref],
        superseded_refs=[sup_audio_ref],
    )
    v2_active_idem = IdempotencyRecord(
        key="idem_v2_03",
        op="replace_entry_audio",
        expected_revision=2,
        payload_sha256=sha_a,
        result_revision=None,
        tx_id="tx_v2_03",
        outcome=JournalTxState.PREPARED,
    )
    v2_journal = AlbumJournal.model_validate({
        "schema_version": 2,
        "protocol": "enforced_v2",
        "album_id": album_id,
        "active_tx": v2_active_tx.model_dump(mode="json"),
        "tx_receipts": [unkeyed_receipt.model_dump(mode="json"), keyed_abort_receipt.model_dump(mode="json")],
        "idempotency_records": [keyed_abort_idem.model_dump(mode="json"), v2_active_idem.model_dump(mode="json")],
    })
    assert v2_journal.schema_version == 2
    assert v2_journal.protocol == JournalProtocol.ENFORCED_V2
    assert v2_journal.tx_receipts[0].idempotency_key is None

    # 3. Negative via real raw nested-dict / serialized JSON parsing: protocol / version mismatches
    with pytest.raises(ValidationError, match="protocol='enforced_v2'"):
        AlbumJournal.model_validate({"schema_version": 2, "album_id": album_id})
    with pytest.raises(ValidationError, match="protocol='legacy_provisional'"):
        AlbumJournal.model_validate({"schema_version": 1, "protocol": "enforced_v2", "album_id": album_id})
    with pytest.raises(ValidationError, match="tx_receipts"):
        AlbumJournal.model_validate({
            "schema_version": 1,
            "album_id": album_id,
            "tx_receipts": [unkeyed_receipt.model_dump(mode="json")],
        })

    # 4. Negative: mixed v1/v2 artifact refs & missing v2 manifest digest
    with pytest.raises(ValidationError, match="mix"):
        ActiveTxRecord(
            tx_id="tx_mix_01",
            op="op",
            state=JournalTxState.PREPARED,
            expected_revision=1,
            target_revision=2,
            payload_sha256=sha_a,
            intended_manifest_sha256=sha_m,
            new_refs=[new_audio_ref.model_dump(mode="json"), f"entries/{other_ehash}/audio.wav"],
        )
    with pytest.raises(ValidationError, match="intended_manifest_sha256"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "active_tx": {**v1_journal.active_tx.model_dump(mode="json"), "created_at": 1700000000.0},
        })
    with pytest.raises(ValidationError, match="size_bytes and role"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "active_tx": {
                **v2_active_tx.model_dump(mode="json"),
                "staging_refs": [{"rel_path": f"entries/{ehash}/.staging_x", "st_dev": 1, "st_ino": 2, "sha256": sha_b}],
            },
        })

    # 5. Negative: idempotency-key ownership across active_tx, tx_receipts, and idempotency_records
    # Receipt-only duplicate key (no IdempotencyRecord exists)
    with pytest.raises(ValidationError, match="claimed by multiple transactions"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "tx_receipts": [
                keyed_abort_receipt.model_dump(mode="json"),
                {**unkeyed_receipt.model_dump(mode="json"), "idempotency_key": "idem_v2_02"},
            ],
        })
    # Active-vs-receipt key conflict (no IdempotencyRecord exists)
    with pytest.raises(ValidationError, match="conflicts on idempotency key"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "active_tx": {**v2_active_tx.model_dump(mode="json"), "idempotency_key": "idem_v2_02"},
            "tx_receipts": [keyed_abort_receipt.model_dump(mode="json")],
        })

    # 6. Negative: duplicate/conflicting tx_id, invalid receipt revisions, contradictory idempotency
    with pytest.raises(ValidationError, match="must not also appear in tx_receipts"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "active_tx": {**v2_active_tx.model_dump(mode="json"), "tx_id": "tx_v2_01", "idempotency_key": None},
            "tx_receipts": [unkeyed_receipt.model_dump(mode="json")],
        })
    with pytest.raises(ValidationError, match="Duplicate terminal receipt"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "tx_receipts": [unkeyed_receipt.model_dump(mode="json"), unkeyed_receipt.model_dump(mode="json")],
        })
    with pytest.raises(ValidationError, match="result_revision > expected_revision"):
        TerminalTxReceipt(
            tx_id="tx_bad_rev",
            op="op",
            outcome=JournalTxState.COMMITTED,
            expected_revision=2,
            result_revision=2,
            payload_sha256=sha_a,
            intended_manifest_sha256=sha_m,
        )
    with pytest.raises(ValidationError, match="result_revision == expected_revision"):
        TerminalTxReceipt(
            tx_id="tx_bad_ab_rev",
            op="op",
            outcome=JournalTxState.ABORTED,
            expected_revision=2,
            result_revision=3,
            payload_sha256=sha_a,
            intended_manifest_sha256=sha_m,
        )
    with pytest.raises(ValidationError):
        TerminalTxReceipt.model_validate({
            "tx_id": "tx_non_term",
            "op": "op",
            "outcome": "prepared",
            "expected_revision": 1,
            "result_revision": 2,
            "payload_sha256": sha_a,
            "intended_manifest_sha256": sha_m,
        })
    with pytest.raises(ValidationError, match="requires a matching TerminalTxReceipt"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "idempotency_records": [keyed_abort_idem.model_dump(mode="json")],
        })
    with pytest.raises(ValidationError, match="contradicts its TerminalTxReceipt"):
        AlbumJournal.model_validate({
            "schema_version": 2,
            "protocol": "enforced_v2",
            "album_id": album_id,
            "tx_receipts": [keyed_abort_receipt.model_dump(mode="json")],
            "idempotency_records": [{**keyed_abort_idem.model_dump(mode="json"), "payload_sha256": sha_b}],
        })

    # 7. Negative: established entry_id validator, role/path ownership, and overlap rejections
    for weak_ale_id in ("ale_short", "ale_bad.dot"):
        with pytest.raises(ValidationError, match="valid 'ale_' entry identifier"):
            ArtifactRefRecord(
                rel_path=f"entries/{ehash}/audio.wav",
                role=ArtifactRole.ENTRY_AUDIO,
                sha256=sha_a,
                size_bytes=10,
                entry_id=weak_ale_id,
            )
    with pytest.raises(ValidationError, match="must reside directly under"):
        ArtifactRefRecord(
            rel_path=f"entries/{other_ehash}/audio.wav",
            role=ArtifactRole.ENTRY_AUDIO,
            sha256=sha_a,
            size_bytes=10,
            entry_id=entry_id,
        )
    with pytest.raises(ValidationError, match="supported audio extension"):
        ArtifactRefRecord(
            rel_path=f"entries/{ehash}/snapshot.json",
            role=ArtifactRole.ENTRY_AUDIO,
            sha256=sha_a,
            size_bytes=10,
            entry_id=entry_id,
        )
    with pytest.raises(ValidationError, match="'.json' snapshot"):
        ArtifactRefRecord(
            rel_path=f"entries/{ehash}/audio.wav",
            role=ArtifactRole.ENTRY_SNAPSHOT,
            sha256=sha_a,
            size_bytes=10,
            entry_id=entry_id,
        )
    with pytest.raises(ValidationError, match="requires entry_id"):
        ArtifactRefRecord(rel_path=f"entries/{ehash}/audio.wav", role=ArtifactRole.ENTRY_AUDIO, sha256=sha_a, size_bytes=10)
    with pytest.raises(ValidationError, match="must not specify entry_id"):
        ArtifactRefRecord(rel_path="cover.jpg", role=ArtifactRole.COVER_ART, sha256=sha_a, size_bytes=10, entry_id=entry_id)
    with pytest.raises(ValidationError, match="requires generation"):
        ArtifactRefRecord(rel_path="retained/bak.wav", role=ArtifactRole.RETAINED_BACKUP, sha256=sha_a, size_bytes=10)
    with pytest.raises(ValidationError, match="Path traversal"):
        ArtifactRefRecord(rel_path="../secret.wav", role=ArtifactRole.COVER_ART, sha256=sha_a, size_bytes=10)
    with pytest.raises(ValidationError):
        ArtifactRefRecord(rel_path="cover.jpg", role=ArtifactRole.COVER_ART, sha256="not_hex", size_bytes=10)
    with pytest.raises(ValidationError, match="64-character hexadecimal"):
        ArtifactRefRecord(rel_path="cover.jpg", role=ArtifactRole.COVER_ART, sha256="z" * 64, size_bytes=10)
    with pytest.raises(ValidationError, match="must not overlap"):
        TerminalTxReceipt(
            tx_id="tx_ovl",
            op="op",
            outcome=JournalTxState.COMMITTED,
            expected_revision=1,
            result_revision=2,
            payload_sha256=sha_a,
            intended_manifest_sha256=sha_m,
            new_refs=[new_audio_ref],
            superseded_refs=[new_audio_ref],
        )


def test_phase1b_stage1_3a_canonical_digest_and_provisional_v2_classification(custom_storage: ProjectStorageManager):
    """Phase 1B Stage 1.3A: canonical digest determinism, historical receipts, artifact-to-manifest binding, and zero migration."""
    import io
    import json
    import wave
    from backend.project_models import (
        ActiveTxRecord,
        AlbumJournal,
        ArtifactRefRecord,
        ArtifactRole,
        JournalProtocol,
        JournalTxState,
        RecoveryConsistency,
        TerminalTxReceipt,
    )
    from backend.security import get_album_entry_dir_name

    user_id = "u_1b_s1_3a_cls"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 400)
    wav_ok = buf.getvalue()

    trk = custom_storage.create_track(user_id=user_id, title="Stage 1.3A Track")
    take = custom_storage.create_take(
        user_id=user_id, track_id=trk.id, audio_bytes=wav_ok, original_filename="s13a.wav", extension=".wav"
    )
    alb = custom_storage.create_album(user_id=user_id, title="Stage 1.3A Album")
    alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=take.id)
    entry = alb.tracks[0]
    assert entry.id is not None and entry.snapshot is not None
    ehash = get_album_entry_dir_name(entry.id)

    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    base_data = json.loads((alb_dir / "album.json").read_text(encoding="utf-8"))

    # 1. Canonical digest determinism across key reordering
    reordered_data = {k: base_data[k] for k in reversed(list(base_data.keys()))}
    d1 = ProjectStorageManager._compute_canonical_manifest_sha256(base_data)
    d2 = ProjectStorageManager._compute_canonical_manifest_sha256(reordered_data)
    assert d1 == d2

    # Build target_data (rev=3) with explicit active_audio_filename & active_generation for artifact binding
    target_data = json.loads(json.dumps(base_data))
    target_data["revision"] = 3
    target_data["last_tx_id"] = "tx_v2_02"
    target_data["tx_history"] = ["tx_v2_01", "tx_v2_02"]
    target_data["title"] = "Updated V2 Title"
    target_data["tracks"][0]["active_generation"] = 1
    target_data["tracks"][0]["active_audio_filename"] = "audio.wav"
    target_data["tracks"][0]["active_snapshot_filename"] = "snapshot.json"
    target_sha = ProjectStorageManager._compute_canonical_manifest_sha256(target_data)

    audio_ref = ArtifactRefRecord(
        rel_path=f"entries/{ehash}/audio.wav",
        role=ArtifactRole.ENTRY_AUDIO,
        sha256=entry.snapshot.audio_sha256,
        size_bytes=entry.snapshot.size_bytes,
        entry_id=entry.id,
        generation=1,
    )
    snap_ref = ArtifactRefRecord(
        rel_path=f"entries/{ehash}/snapshot.json",
        role=ArtifactRole.ENTRY_SNAPSHOT,
        sha256="e" * 64,
        size_bytes=256,
        entry_id=entry.id,
        generation=1,
    )

    # 2. Historical COMMITTED receipt (rev=2, tx_v2_01) with different digest + later rev=3 manifest -> CONSISTENT_IDLE
    hist_rcp = TerminalTxReceipt(
        tx_id="tx_v2_01",
        op="add_entry",
        outcome=JournalTxState.COMMITTED,
        expected_revision=1,
        result_revision=2,
        payload_sha256="a" * 64,
        intended_manifest_sha256=d1,
    )
    curr_rcp = TerminalTxReceipt(
        tx_id="tx_v2_02",
        op="update_album",
        outcome=JournalTxState.COMMITTED,
        expected_revision=2,
        result_revision=3,
        payload_sha256="a" * 64,
        intended_manifest_sha256=target_sha,
        new_refs=[audio_ref, snap_ref],
    )
    idle_v2_j = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb.id,
        tx_receipts=[hist_rcp, curr_rcp],
    )
    assert ProjectStorageManager._classify_album_consistency(target_data, idle_v2_j) == RecoveryConsistency.CONSISTENT_IDLE

    # Incomplete lineage (hist_rcp not in tx_history) -> UNCERTAIN (not CORRUPT, not IDLE)
    no_hist_data = dict(target_data, tx_history=["tx_v2_02"])
    no_hist_rcp = curr_rcp.model_copy(
        update={"intended_manifest_sha256": ProjectStorageManager._compute_canonical_manifest_sha256(no_hist_data)}
    )
    assert ProjectStorageManager._classify_album_consistency(
        no_hist_data,
        AlbumJournal(schema_version=2, protocol=JournalProtocol.ENFORCED_V2, album_id=alb.id, tx_receipts=[hist_rcp, no_hist_rcp]),
    ) == RecoveryConsistency.UNCERTAIN

    # Contradictions: ABORTED receipt represented as published, or COMMITTED receipt beyond manifest_rev -> CORRUPT
    aborted_published_rcp = TerminalTxReceipt(
        tx_id="tx_v2_01",
        op="add_entry",
        outcome=JournalTxState.ABORTED,
        expected_revision=1,
        result_revision=1,
        payload_sha256="a" * 64,
        intended_manifest_sha256=d1,
    )
    assert ProjectStorageManager._classify_album_consistency(
        target_data,
        AlbumJournal(schema_version=2, protocol=JournalProtocol.ENFORCED_V2, album_id=alb.id, tx_receipts=[aborted_published_rcp, curr_rcp]),
    ) == RecoveryConsistency.CORRUPT

    future_rcp = TerminalTxReceipt(
        tx_id="tx_v2_99",
        op="update_album",
        outcome=JournalTxState.COMMITTED,
        expected_revision=3,
        result_revision=4,
        payload_sha256="a" * 64,
        intended_manifest_sha256="f" * 64,
    )
    assert ProjectStorageManager._classify_album_consistency(
        target_data,
        AlbumJournal(schema_version=2, protocol=JournalProtocol.ENFORCED_V2, album_id=alb.id, tx_receipts=[hist_rcp, curr_rcp, future_rcp]),
    ) == RecoveryConsistency.CORRUPT

    # 3. v2 active_tx classifications & artifact-to-manifest binding
    v2_intent_tx = ActiveTxRecord(
        tx_id="tx_v2_02",
        op="update_album",
        state=JournalTxState.PUBLISH_INTENT,
        expected_revision=2,
        target_revision=3,
        payload_sha256="a" * 64,
        intended_manifest_sha256=target_sha,
        new_refs=[audio_ref, snap_ref],
    )
    v2_intent_j = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb.id,
        active_tx=v2_intent_tx,
        tx_receipts=[hist_rcp],
    )
    assert ProjectStorageManager._classify_album_consistency(target_data, v2_intent_j) == RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE

    # Insufficient layout metadata (active_audio_filename omitted in manifest) -> UNCERTAIN
    no_fname_data = json.loads(json.dumps(target_data))
    del no_fname_data["tracks"][0]["active_audio_filename"]
    no_fname_sha = ProjectStorageManager._compute_canonical_manifest_sha256(no_fname_data)
    no_fname_j = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb.id,
        active_tx=v2_intent_tx.model_copy(update={"intended_manifest_sha256": no_fname_sha}),
        tx_receipts=[hist_rcp],
    )
    assert ProjectStorageManager._classify_album_consistency(no_fname_data, no_fname_j) == RecoveryConsistency.UNCERTAIN

    # Contradictory active_audio_filename -> CORRUPT
    wrong_fname_data = json.loads(json.dumps(target_data))
    wrong_fname_data["tracks"][0]["active_audio_filename"] = "other.wav"
    wrong_fname_sha = ProjectStorageManager._compute_canonical_manifest_sha256(wrong_fname_data)
    wrong_fname_j = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb.id,
        active_tx=v2_intent_tx.model_copy(update={"intended_manifest_sha256": wrong_fname_sha}),
        tx_receipts=[hist_rcp],
    )
    assert ProjectStorageManager._classify_album_consistency(wrong_fname_data, wrong_fname_j) == RecoveryConsistency.CORRUPT

    # Tampered manifest with wrong digest -> CORRUPT
    tampered_data = dict(target_data, title="Tampered Title")
    assert ProjectStorageManager._classify_album_consistency(tampered_data, v2_intent_j) == RecoveryConsistency.CORRUPT

    # 4. Ordinary reads remain side-effect-free (no journal.json created or migrated)
    assert not (alb_dir / "journal.json").exists()
    assert custom_storage.get_album(user_id, alb.id) is not None
    assert not (alb_dir / "journal.json").exists()

    # Existing v1 journal with created_at=0 on disk parses cleanly and is not auto-migrated during get_album
    (alb_dir / "journal.json").write_text(
        json.dumps({"schema_version": 1, "album_id": alb.id, "active_tx": {"tx_id": "tx_v1_0", "op": "op", "state": "prepared", "expected_revision": 1, "target_revision": 2, "payload_sha256": "a" * 64, "created_at": 0}}),
        encoding="utf-8",
    )
    v1_raw_before = (alb_dir / "journal.json").read_bytes()
    assert custom_storage.get_album(user_id, alb.id) is not None
    assert (alb_dir / "journal.json").read_bytes() == v1_raw_before


def test_phase1b_stage1_3a_advanced_revision_snapshot_identity_and_omissions(custom_storage: ProjectStorageManager):
    """Phase 1B Stage 1.3A: advanced-revision markers, snapshot identity checks, omitted metadata, and CORRUPT > UNCERTAIN precedence."""
    import io
    import json
    import wave
    from backend.project_models import (
        ActiveTxRecord,
        AlbumJournal,
        ArtifactRefRecord,
        ArtifactRole,
        JournalProtocol,
        JournalTxState,
        RecoveryConsistency,
        TerminalTxReceipt,
    )
    from backend.security import get_album_entry_dir_name

    user_id = "u_1b_s1_3a_adv"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 400)
    wav_ok = buf.getvalue()

    trk = custom_storage.create_track(user_id=user_id, title="Adv Rev Track")
    take = custom_storage.create_take(
        user_id=user_id, track_id=trk.id, audio_bytes=wav_ok, original_filename="adv.wav", extension=".wav"
    )
    alb = custom_storage.create_album(user_id=user_id, title="Adv Rev Album")
    alb = custom_storage.add_album_entry(user_id, alb.id, trk.id, expected_revision=alb.revision, take_id=take.id)
    entry = alb.tracks[0]
    assert entry.id is not None and entry.snapshot is not None
    ehash = get_album_entry_dir_name(entry.id)

    alb_dir = custom_storage._get_album_dir(user_id, alb.id)
    base_data = json.loads((alb_dir / "album.json").read_text(encoding="utf-8"))

    # Exact rev=2 manifest committed by tx_T
    rev2_data = json.loads(json.dumps(base_data))
    rev2_data["revision"] = 2
    rev2_data["last_tx_id"] = "tx_T"
    rev2_data["tx_history"] = ["tx_T"]
    rev2_data["tracks"][0]["active_generation"] = 1
    rev2_data["tracks"][0]["active_audio_filename"] = "audio.wav"
    rev2_data["tracks"][0]["active_snapshot_filename"] = "snapshot.json"
    rev2_sha = ProjectStorageManager._compute_canonical_manifest_sha256(rev2_data)

    audio_ref = ArtifactRefRecord(
        rel_path=f"entries/{ehash}/audio.wav",
        role=ArtifactRole.ENTRY_AUDIO,
        sha256=entry.snapshot.audio_sha256,
        size_bytes=entry.snapshot.size_bytes,
        entry_id=entry.id,
        generation=1,
    )
    snap_ref = ArtifactRefRecord(
        rel_path=f"entries/{ehash}/snapshot.json",
        role=ArtifactRole.ENTRY_SNAPSHOT,
        sha256="e" * 64,
        size_bytes=256,
        entry_id=entry.id,
        generation=1,
    )

    # 1. Later revision (rev=3) retaining last_tx_id="tx_T" after Phase 1A metadata update -> UNCERTAIN (not CORRUPT)
    rev3_data = json.loads(json.dumps(rev2_data))
    rev3_data["revision"] = 3
    rev3_data["title"] = "Phase 1A Metadata Update"
    rcp_T = TerminalTxReceipt(
        tx_id="tx_T",
        op="replace_entry_audio",
        outcome=JournalTxState.COMMITTED,
        expected_revision=1,
        result_revision=2,
        payload_sha256="a" * 64,
        intended_manifest_sha256=rev2_sha,
        new_refs=[audio_ref, snap_ref],
    )
    j_rcp_T = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb.id,
        tx_receipts=[rcp_T],
    )
    assert ProjectStorageManager._classify_album_consistency(rev2_data, j_rcp_T) == RecoveryConsistency.CONSISTENT_IDLE
    assert ProjectStorageManager._classify_album_consistency(rev3_data, j_rcp_T) == RecoveryConsistency.UNCERTAIN

    # Same advanced-revision condition with unresolved active_tx (PUBLISH_INTENT) -> UNCERTAIN (not CORRUPT)
    tx_T_active = ActiveTxRecord(
        tx_id="tx_T",
        op="replace_entry_audio",
        state=JournalTxState.PUBLISH_INTENT,
        expected_revision=1,
        target_revision=2,
        payload_sha256="a" * 64,
        intended_manifest_sha256=rev2_sha,
        new_refs=[audio_ref, snap_ref],
    )
    j_active_T = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb.id,
        active_tx=tx_T_active,
    )
    assert ProjectStorageManager._classify_album_consistency(rev2_data, j_active_T) == RecoveryConsistency.PROVISIONAL_COMMIT_CANDIDATE
    assert ProjectStorageManager._classify_album_consistency(rev3_data, j_active_T) == RecoveryConsistency.UNCERTAIN

    # Genuine contradictory marker/revision evidence -> CORRUPT
    rev1_bad = json.loads(json.dumps(rev2_data))
    rev1_bad["revision"] = 1
    assert ProjectStorageManager._classify_album_consistency(rev1_bad, j_rcp_T) == RecoveryConsistency.CORRUPT
    assert ProjectStorageManager._classify_album_consistency(rev1_bad, j_active_T) == RecoveryConsistency.CORRUPT
    j_prep_T = AlbumJournal(
        schema_version=2,
        protocol=JournalProtocol.ENFORCED_V2,
        album_id=alb.id,
        active_tx=ActiveTxRecord(
            tx_id="tx_T",
            op="replace_entry_audio",
            state=JournalTxState.PREPARED,
            expected_revision=1,
            target_revision=2,
            payload_sha256="a" * 64,
            intended_manifest_sha256=rev2_sha,
            new_refs=[audio_ref, snap_ref],
        ),
    )
    assert ProjectStorageManager._classify_album_consistency(rev3_data, j_prep_T) == RecoveryConsistency.CORRUPT

    # 2. Snapshot identity mismatches (with recomputed matching intended_manifest_sha256) -> CORRUPT
    def _classify_mutated(mutator, refs):
        m = json.loads(json.dumps(rev2_data))
        mutator(m)
        m_sha = ProjectStorageManager._compute_canonical_manifest_sha256(m)
        j = AlbumJournal(
            schema_version=2,
            protocol=JournalProtocol.ENFORCED_V2,
            album_id=alb.id,
            active_tx=ActiveTxRecord(
                tx_id="tx_T",
                op="replace_entry_audio",
                state=JournalTxState.PUBLISH_INTENT,
                expected_revision=1,
                target_revision=2,
                payload_sha256="a" * 64,
                intended_manifest_sha256=m_sha,
                new_refs=refs,
            ),
        )
        return ProjectStorageManager._classify_album_consistency(m, j)

    for ref_set in ([audio_ref], [snap_ref]):
        assert _classify_mutated(lambda m: m["tracks"][0]["snapshot"].update({"entry_id": "ale_0000000000000000"}), ref_set) == RecoveryConsistency.CORRUPT
        assert _classify_mutated(lambda m: m["tracks"][0]["snapshot"].update({"source_track_id": "trk_0000000000000000"}), ref_set) == RecoveryConsistency.CORRUPT
        assert _classify_mutated(lambda m: m["tracks"][0]["snapshot"].update({"source_take_id": "tak_0000000000000000"}), ref_set) == RecoveryConsistency.CORRUPT

    # 3. Missing optional metadata vs explicit conflicting values (with recomputed matching intended_manifest_sha256)
    assert _classify_mutated(lambda m: m["tracks"][0].pop("has_owned_audio", None), [audio_ref]) == RecoveryConsistency.UNCERTAIN
    assert _classify_mutated(lambda m: m["tracks"][0].update({"has_owned_audio": False}), [audio_ref]) == RecoveryConsistency.CORRUPT
    assert _classify_mutated(lambda m: m["tracks"][0]["snapshot"].pop("size_bytes", None), [audio_ref]) == RecoveryConsistency.UNCERTAIN
    assert _classify_mutated(lambda m: m["tracks"][0]["snapshot"].update({"size_bytes": entry.snapshot.size_bytes + 1}), [audio_ref]) == RecoveryConsistency.CORRUPT

    # 4. Precedence: earlier missing field (UNCERTAIN) + later explicit contradiction (CORRUPT) -> CORRUPT
    def _omit_owned_and_mismatch_take(m):
        m["tracks"][0].pop("has_owned_audio", None)
        m["tracks"][0]["snapshot"]["source_take_id"] = "tak_0000000000000000"

    assert _classify_mutated(_omit_owned_and_mismatch_take, [audio_ref]) == RecoveryConsistency.CORRUPT













