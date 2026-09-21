"""Migration tests never invoke real account/service management."""
from pathlib import Path
from types import SimpleNamespace
import os
import pytest
from scripts import migrate_local_account as migration


def test_private_copy_preserves_bytes_and_permissions(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'auth').mkdir()
    (source / 'auth/users.json').write_text('{"existing":"account"}')
    (source / '.secret_key').write_text('synthetic-test-secret')
    target = tmp_path / 'target'
    expected = migration.private_copy(source, target, os.getuid(), os.getgid())
    assert migration.file_manifest(target) == expected
    assert migration.file_manifest(source) == expected
    for path in [target, *target.rglob('*')]:
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


@pytest.mark.parametrize('kind', ['file-link', 'directory-link', 'fifo'])
def test_data_special_files_rejected_before_copy(tmp_path, kind):
    source = tmp_path / 'source'
    source.mkdir()
    suspect = source / 'unexpected'
    if kind == 'fifo':
        os.mkfifo(suspect)
    else:
        suspect.symlink_to(tmp_path if kind == 'directory-link' else __file__)
    with pytest.raises(ValueError):
        migration.private_copy(source, tmp_path / 'target', os.getuid(), os.getgid())
    assert not (tmp_path / 'target').exists()


def test_unit_exposes_only_runtime_parts():
    unit = migration.unit_text(Path('/source'), Path('/runtime'), Path('/private-data'))
    assert 'User=mp3metafix\n' in unit
    assert 'ReadWritePaths=/private-data\n' in unit
    assert 'BindReadOnlyPaths=/source/.venv:/runtime/.venv\n' in unit
    assert 'ExecStart=/usr/bin/env /runtime/.venv/bin/python -m uvicorn' in unit
    assert '.git' not in unit
    assert 'BindReadOnlyPaths=/source:' not in unit
    assert 'CapabilityBoundingSet=\n' in unit


@pytest.mark.parametrize('path', ['/source with space', '/source%h', '/source/../other'])
def test_ambiguous_systemd_paths_rejected(path):
    with pytest.raises(ValueError):
        migration.unit_text(Path(path), Path('/runtime'), Path('/data'))


def test_environment_literal_escaping_and_invalid_lines():
    result = migration.environment_lines(['TOKEN=a"b\\c$d`e'])
    assert result == 'TOKEN="a\\"b\\\\c\\$d\\`e"\n'
    with pytest.raises(ValueError):
        migration.environment_lines(['TOKEN=a\nOTHER=x'])


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    for part in migration.PARTS:
        if part == 'VERSION':
            (source / part).write_text('0.5.1')
        else:
            (source / part).mkdir()
    (source / 'data').mkdir()
    (source / 'data/record').write_text('original')
    monkeypatch.setattr(migration, 'SOURCE', source)
    monkeypatch.setattr(migration, 'RUNTIME', tmp_path / 'runtime')
    monkeypatch.setattr(migration, 'DATA', tmp_path / 'new-data')
    monkeypatch.setattr(migration, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(migration, 'UNIT', tmp_path / 'service')
    migration.UNIT.write_text('old disabled unit')
    calls = []
    def command(args, check=True):
        calls.append(args)
        return 'mp3metafix' if args[:2] == ['systemctl', 'show'] else ''
    monkeypatch.setattr(migration, 'command', command)
    monkeypatch.setattr(migration, 'userctl', lambda user, *args: calls.append(['userctl', *args]))
    monkeypatch.setattr(migration.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(migration, 'wait_health', lambda _: None)
    info = {'user':'desktop', 'uid':os.getuid(), 'gid':os.getgid(), 'port':8844,
            'user_enabled':True, 'environment':{'MP3METAFIX_PORT':'8844'}}
    return info, calls


def test_success_and_rollback_preserve_post_migration_edits(prepared):
    info, calls = prepared
    migration.apply(info)
    assert info['phase'] == 'complete'
    assert (migration.SOURCE / 'data/record').read_text() == 'original'
    assert (migration.DATA / 'record').read_text() == 'original'
    assert ['userctl', 'disable', 'mp3metafix.service'] in calls
    (migration.DATA / 'record').write_text('edited after migration')
    migration.rollback(info)
    assert (migration.SOURCE / 'data/record').read_text() == 'edited after migration'
    assert (migration.SOURCE / 'data.before-account-rollback/record').read_text() == 'original'
    assert migration.UNIT.read_text() == 'old disabled unit'
    assert info['phase'] == 'rolled-back'


def test_failed_start_restores_original_service(prepared, monkeypatch):
    info, calls = prepared
    attempts = []
    def health(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('synthetic startup failure')
    monkeypatch.setattr(migration, 'wait_health', health)
    with pytest.raises(RuntimeError, match='synthetic startup failure'):
        migration.apply(info)
    assert info['phase'] == 'failed-restored'
    assert ['userctl', 'start', 'mp3metafix.service'] in calls
    assert ['userctl', 'disable', 'mp3metafix.service'] not in calls
    assert migration.UNIT.read_text() == 'old disabled unit'
    assert (migration.SOURCE / 'data/record').read_text() == 'original'
    assert (migration.DATA / 'record').read_text() == 'original'


def test_atomic_write_retains_old_file_on_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / 'state'
    target.write_text('old')
    def fail(*args):
        raise OSError('synthetic failure')
    monkeypatch.setattr(migration.os, 'replace', fail)
    with pytest.raises(OSError):
        migration.atomic_write(target, 'new')
    assert target.read_text() == 'old'
    assert list(tmp_path.iterdir()) == [target]


def test_failed_health_retains_new_data_before_automatic_recovery(prepared, monkeypatch):
    info, calls = prepared
    checks = []
    def health(_):
        checks.append(1)
        if len(checks) == 1:
            (migration.DATA / 'record').write_text('new data before failure')
            raise RuntimeError('synthetic health failure')
    monkeypatch.setattr(migration, 'wait_health', health)
    with pytest.raises(RuntimeError, match='synthetic health failure'):
        migration.apply(info)
    assert (migration.SOURCE / 'data/record').read_text() == 'new data before failure'
    assert (migration.SOURCE / 'data.before-account-rollback/record').read_text() == 'original'
