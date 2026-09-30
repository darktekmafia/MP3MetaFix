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
    is_valid_project_id,
    get_tenant_storage_dir_name,
    get_track_dir_name,
    get_take_dir_name,
    get_album_dir_name,
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
