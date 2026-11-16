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
    ProjectStorageQuotaExceeded,
    ProjectValidationError,
    AlbumFinalizedError,
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

    # Sequence tracks
    track_entries = [
        {"track_number": 1, "disc_number": 1, "track_id": t1.id, "take_id": take1.id, "custom_title": "Track One (Remix)"},
        {"track_number": 2, "disc_number": 1, "track_id": t2.id, "take_id": take2.id, "custom_title": None},
    ]
    updated_album = custom_storage.update_album(user_id, album.id, {"tracks": track_entries})
    assert len(updated_album.tracks) == 2
    assert updated_album.tracks[0].track_number == 1
    assert updated_album.tracks[0].custom_title == "Track One (Remix)"

    # Save cover art
    dummy_cover = b"\xff\xd8\xff\xe0\x00\x10JFIF_DUMMY_JPEG"
    custom_storage.save_album_cover(user_id, album.id, dummy_cover, extension=".jpg")
    
    cover_path = custom_storage.get_album_cover_path(user_id, album.id)
    assert cover_path is not None
    assert cover_path.is_file()
    with open(cover_path, "rb") as f:
        assert f.read() == dummy_cover

    album_with_cover = custom_storage.get_album(user_id, album.id)
    assert album_with_cover.has_cover is True

    # Delete album
    assert custom_storage.delete_album(user_id, album.id) is True
    assert custom_storage.get_album(user_id, album.id) is None
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
        {"year": 2026, "genre": "Electronic"},
        change_summary="Added release year and genre",
    )
    assert updated_album.revision == 2
    assert len(updated_album.revisions) == 2
    assert updated_album.revisions[1].change_summary == "Added release year and genre"

    # Upload cover art bumps revision
    custom_storage.save_album_cover(user_id, album.id, b"FAKE_JPEG_IMAGE_BYTES", extension=".jpg")
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
    album = custom_storage.add_album_entry(user_id, album.id, empty_track.id, take_id=None)
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
    album = custom_storage.add_album_entry(user_id, album.id, recorded_track.id, take_id=t_alt.id)
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
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, take_id=t1.id)
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, take_id=t2.id)
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, take_id=t2.id, custom_title="Suite (Reprise)")

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
        {"tracks": [e3.model_dump(), e1.model_dump(), e2.model_dump()]},
    )
    assert [t.id for t in reordered.tracks] == [e3.id, e1.id, e2.id]
    assert [t.track_number for t in reordered.tracks] == [1, 2, 3]
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2

    # Clear e3 to placeholder -> same entry id preserved, owned audio removed
    cleared = custom_storage.update_album_entry(user_id, album.id, e3.id, clear_take_to_placeholder=True)
    e3_after = next(t for t in cleared.tracks if t.id == e3.id)
    assert e3_after.is_placeholder is True
    assert e3_after.has_owned_audio is False
    assert e3_after.take_id is None
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e3.id) is None

    # Remove e3 completely -> e1 and e2 unaffected
    after_remove = custom_storage.remove_album_entry(user_id, album.id, e3.id)
    assert [t.id for t in after_remove.tracks] == [e1.id, e2.id]
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, e2.id).read_bytes() == wav_2


def test_album_entry_staging_rollback_on_failure(custom_storage, monkeypatch):
    """Verify transactional rollback restores prior owned copy and removes staging/backup artifacts on failure."""
    import shutil
    import backend.project_storage as ps_mod

    user_id = "user_rollback"
    trk = custom_storage.create_track(user_id=user_id, title="Rollback Song")
    wav_1 = _make_pcm_wav_bytes(0.4)
    wav_2 = _make_pcm_wav_bytes(0.7)
    t1 = custom_storage.create_take(user_id, trk.id, wav_1, "v1.wav", ".wav", label="V1", is_master=True)
    t2 = custom_storage.create_take(user_id, trk.id, wav_2, "v2.wav", ".wav", label="V2", is_master=False)

    album = custom_storage.create_album(user_id=user_id, title="Rollback LP")
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, take_id=t1.id)
    entry_id = album.tracks[0].id
    entry_dir = custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).parent

    # 1. Fail mid-staging copy
    def broken_copy(*args, **kwargs):
        raise OSError("Simulated disk full during copy")

    monkeypatch.setattr(shutil, "copyfileobj", broken_copy)
    with pytest.raises(OSError, match="Simulated disk full"):
        custom_storage.update_album_entry(user_id, album.id, entry_id, take_id=t2.id)
    monkeypatch.undo()

    leftover_mid = [p.name for p in entry_dir.iterdir() if p.name.startswith((".staging_", ".backup_"))]
    assert leftover_mid == []
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_1

    # 2. Fail on final album.json atomic write after commit_fn() replaced audio.wav -> rollback_fn() restores wav_1
    orig_atomic_json = ps_mod.atomic_json

    def failing_manifest_write(target_path, payload):
        if target_path.name == "album.json":
            raise OSError("Simulated manifest commit failure")
        return orig_atomic_json(target_path, payload)

    monkeypatch.setattr(ps_mod, "atomic_json", failing_manifest_write)
    with pytest.raises(OSError, match="Simulated manifest commit failure"):
        custom_storage.update_album_entry(user_id, album.id, entry_id, take_id=t2.id)
    monkeypatch.undo()

    leftover_post = [p.name for p in entry_dir.iterdir() if p.name.startswith((".staging_", ".backup_"))]
    assert leftover_post == []
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_1
    assert custom_storage.get_album(user_id, album.id).tracks[0].take_id == t1.id


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
    updated = custom_storage.update_album(user_id, album.id, {"title": "Renamed Legacy"})
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
    """Verify source refresh preview diffing, stale-source conflict protection, confirmation, and deleted-source handling."""
    user_id = "user_refresh"
    trk = custom_storage.create_track(user_id=user_id, title="Refresh Song")
    wav_v1 = _make_pcm_wav_bytes(0.4)
    wav_v2 = _make_pcm_wav_bytes(0.8)
    wav_v3 = _make_pcm_wav_bytes(1.1)

    t1 = custom_storage.create_take(user_id, trk.id, wav_v1, "v1.wav", ".wav", label="Take 1", notes="Raw mix", is_master=True)
    custom_storage.save_take_lyrics(user_id, trk.id, t1.id, "Line one\nLine two", LyricsFormat.LRC)
    album = custom_storage.create_album(user_id=user_id, title="Refresh LP")
    album = custom_storage.add_album_entry(user_id, album.id, trk.id, take_id=t1.id)
    entry_id = album.tracks[0].id

    clean_preview = custom_storage.preview_album_entry_refresh(user_id, album.id, entry_id)
    assert clean_preview.source_available is True
    assert clean_preview.has_changes is False

    # Update source take audio, notes, and lyrics
    custom_storage.update_take_audio(user_id, trk.id, t1.id, wav_v2, extension=".wav")
    custom_storage.update_take(user_id, trk.id, t1.id, {"notes": "Mastered mix"})
    custom_storage.save_take_lyrics(user_id, trk.id, t1.id, "Line one\nLine two revised", LyricsFormat.LRC)

    # Owned album copy is still v1 until user explicitly refreshes
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_v1

    preview = custom_storage.preview_album_entry_refresh(user_id, album.id, entry_id)
    assert preview.source_available is True
    assert preview.has_changes is True
    assert preview.audio_changed is True
    assert preview.lyrics_changed is True
    assert "-Line two" in preview.lyrics_diff
    assert "+Line two revised" in preview.lyrics_diff
    assert any(c["field"] == "take_notes" and c["old"] == "Raw mix" and c["new"] == "Mastered mix" for c in preview.metadata_changes)

    # Modify source again after preview -> stale preview confirmation must fail with 409 conflict
    custom_storage.update_take_audio(user_id, trk.id, t1.id, wav_v3, extension=".wav")
    with pytest.raises(AlbumRefreshConflictError):
        custom_storage.confirm_album_entry_refresh(
            user_id,
            album.id,
            entry_id,
            expected_source_revision=preview.expected_source_revision,
            expected_source_audio_sha256=preview.expected_source_audio_sha256,
            expected_source_updated_at=preview.expected_source_updated_at,
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_v1

    # Fresh preview & confirm updates album copy to wav_v3 and refreshes snapshot
    fresh_preview = custom_storage.preview_album_entry_refresh(user_id, album.id, entry_id)
    refreshed = custom_storage.confirm_album_entry_refresh(
        user_id,
        album.id,
        entry_id,
        expected_source_revision=fresh_preview.expected_source_revision,
        expected_source_audio_sha256=fresh_preview.expected_source_audio_sha256,
        expected_source_updated_at=fresh_preview.expected_source_updated_at,
    )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_v3
    assert refreshed.tracks[0].snapshot.embedded_lyrics == "Line one\nLine two revised"
    assert refreshed.tracks[0].snapshot.take_notes == "Mastered mix"

    # Deleting source take -> source_available=False on preview, confirm raises conflict, album copy intact
    custom_storage.delete_take(user_id, trk.id, t1.id)
    gone_preview = custom_storage.preview_album_entry_refresh(user_id, album.id, entry_id)
    assert gone_preview.source_available is False
    assert gone_preview.album_snapshot is not None
    with pytest.raises(AlbumRefreshConflictError):
        custom_storage.confirm_album_entry_refresh(
            user_id,
            album.id,
            entry_id,
            expected_source_revision=fresh_preview.expected_source_revision,
            expected_source_audio_sha256=fresh_preview.expected_source_audio_sha256,
        )
    assert custom_storage.get_album_entry_audio_path(user_id, album.id, entry_id).read_bytes() == wav_v3


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
        custom_storage.finalize_album(user_id, album.id)
    assert len(exc_empty.value.errors) == 1

    # 2. Placeholder entry rejected
    album = custom_storage.add_album_entry(user_id, album.id, trk_empty.id, take_id=None)
    album = custom_storage.add_album_entry(user_id, album.id, trk_ok.id, take_id=t_ok.id)
    p_id = album.tracks[0].id
    r_id = album.tracks[1].id

    with pytest.raises(AlbumFinalizationError) as exc_ph:
        custom_storage.finalize_album(user_id, album.id)
    assert any("placeholder" in err["reason"].lower() for err in exc_ph.value.errors)

    # Remove placeholder so only recorded entry remains
    album = custom_storage.remove_album_entry(user_id, album.id, p_id)

    # 3. Corrupt audio fails checksum/container verification
    owned_path = custom_storage.get_album_entry_audio_path(user_id, album.id, r_id)
    owned_path.write_bytes(b"NOT_AUDIO_CORRUPT_DATA_BYTES_1234567890")
    with pytest.raises(AlbumFinalizationError) as exc_corrupt:
        custom_storage.finalize_album(user_id, album.id)
    assert any(r_id == err["entry_id"] for err in exc_corrupt.value.errors)

    # Restore valid audio, verify incomplete .staging_ file fails finalization
    owned_path.write_bytes(wav_ok)
    staging_junk = owned_path.parent / ".staging_orphan_audio.wav"
    staging_junk.write_bytes(b"partial")
    with pytest.raises(AlbumFinalizationError) as exc_staging:
        custom_storage.finalize_album(user_id, album.id)
    assert any("incomplete" in err["reason"].lower() for err in exc_staging.value.errors)
    staging_junk.unlink()

    # 4. Orphan directory under entries/ fails finalization
    orphan_dir = custom_storage._get_album_dir(user_id, album.id) / "entries" / "deadbeefdeadbeefdeadbeefdeadbeef"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "junk.bin").write_bytes(b"123")
    with pytest.raises(AlbumFinalizationError) as exc_orphan:
        custom_storage.finalize_album(user_id, album.id)
    assert any("inconsistent" in err["reason"].lower() for err in exc_orphan.value.errors)
    (orphan_dir / "junk.bin").unlink()
    orphan_dir.rmdir()

    # 5. Passing finalization sets is_finalized, completed status, and locks all mutations
    finalized = custom_storage.finalize_album(user_id, album.id)
    assert finalized.is_finalized is True
    assert finalized.status == WorkspaceStatus.COMPLETED
    assert finalized.finalized_at is not None

    with pytest.raises(AlbumFinalizedError):
        custom_storage.update_album(user_id, album.id, {"title": "Locked Edit"})
    with pytest.raises(AlbumFinalizedError):
        custom_storage.add_album_entry(user_id, album.id, trk_ok.id, take_id=t_ok.id)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.update_album_entry(user_id, album.id, r_id, clear_take_to_placeholder=True)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.remove_album_entry(user_id, album.id, r_id)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.preview_album_entry_refresh(user_id, album.id, r_id)
    with pytest.raises(AlbumFinalizedError):
        custom_storage.confirm_album_entry_refresh(user_id, album.id, r_id, 1, "sha")
    with pytest.raises(AlbumFinalizedError):
        custom_storage.save_album_cover(user_id, album.id, b"\xff\xd8\xff\xe0", extension=".jpg")
    with pytest.raises(AlbumFinalizedError):
        custom_storage.finalize_album(user_id, album.id)




