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
        return 'mp3metafix' if args[:2] == ['systemctl', 'show'] and 'User' in args else ''
    monkeypatch.setattr(migration, 'command', command)
    monkeypatch.setattr(migration, 'userctl', lambda user, *args: calls.append(['userctl', *args]))
    monkeypatch.setattr(migration.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(migration, 'wait_health', lambda _: None)
    monkeypatch.setattr(migration, 'sandbox_probe', lambda: None)
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


def test_recovery_stops_restarting_system_unit_before_replacing_file(prepared, monkeypatch):
    info, calls = prepared
    state = {'value': 'activating'}
    def command(args, check=True):
        calls.append(args)
        if args[:2] == ['systemctl', 'stop']:
            state['value'] = 'inactive'
        if 'ActiveState' in args:
            return state['value']
        return ''
    monkeypatch.setattr(migration, 'command', command)
    migration.STATE.mkdir()
    (migration.STATE / 'original-system.service').write_text('original')
    migration.restore_service(info)
    assert calls[0] == ['systemctl', 'stop', 'mp3metafix.service']
    assert migration.UNIT.read_text() == 'original'
    assert state['value'] == 'inactive'


def test_sandbox_failure_leaves_source_data_and_user_service_intact(prepared, monkeypatch):
    info, calls = prepared
    def fail():
        raise RuntimeError('probe failed')
    monkeypatch.setattr(migration, 'sandbox_probe', fail)
    with pytest.raises(RuntimeError, match='probe failed'):
        migration.apply(info)
    assert ['userctl', 'stop', 'mp3metafix.service'] not in calls
    assert (migration.SOURCE / 'data/record').read_text() == 'original'
    assert not migration.DATA.exists()
    assert info['phase'] == 'failed-restored'


def test_retry_archives_stale_data_and_uses_current_source(prepared, monkeypatch):
    info, calls = prepared
    checks = []
    def health(_):
        checks.append(1)
        if len(checks) == 1:
            raise RuntimeError('first attempt fails')
    monkeypatch.setattr(migration, 'wait_health', health)
    with pytest.raises(RuntimeError):
        migration.apply(info)
    (migration.SOURCE / 'data/record').write_text('edited after recovery')
    info['environment'] = {'MP3METAFIX_PORT': '8844'}
    migration.apply(info, retry=True)
    assert (migration.DATA / 'record').read_text() == 'edited after recovery'
    archives = list(migration.STATE.glob('previous-attempt-data-*'))
    assert len(archives) == 1
    assert (archives[0] / 'record').read_text() == 'original'
    migration.rollback(info)
    assert (migration.SOURCE / 'data/record').read_text() == 'edited after recovery'
    assert len(list(migration.SOURCE.glob('data.before-account-rollback*'))) == 2


def test_probe_command_checks_real_identity_and_readonly_mount_without_writing_source(prepared, monkeypatch):
    # Undo only the fixture's sandbox stub; build the real command without running systemd.
    import importlib.util
    spec = importlib.util.spec_from_file_location('probe_module', migration.__file__)
    probe_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe_module)
    monkeypatch.setattr(probe_module, 'STATE', migration.STATE)
    migration.STATE.mkdir()
    probe_dir = migration.SOURCE / 'probe-data'
    probe_dir.mkdir()
    monkeypatch.setattr(probe_module.tempfile, 'mkdtemp', lambda **kwargs: str(probe_dir))
    captured = []
    monkeypatch.setattr(probe_module, 'command', lambda args: captured.append(args))
    probe_module.sandbox_probe()
    args = captured[0]
    compile(args[-1], '<probe>', 'exec')
    assert 'TemporaryFileSystem=/run/dbus:ro' in args
    assert 'User=mp3metafix' in args
    assert 'os.statvfs' in args[-1]
    assert "open('VERSION'" not in args[-1]
    assert not probe_dir.exists()


def test_system_service_migration_and_rollback(prepared):
    info, calls = prepared
    info['source_type'] = 'system_service'
    info['source_enabled'] = True
    info['user_enabled'] = False
    migration.apply(info)
    assert info['phase'] == 'complete'
    assert (migration.SOURCE / 'data/record').read_text() == 'original'
    assert (migration.DATA / 'record').read_text() == 'original'
    assert ['systemctl', 'enable', 'mp3metafix.service'] in calls
    # User service disable shouldn't be called for system_service source
    assert ['userctl', 'disable', 'mp3metafix.service'] not in calls

    (migration.DATA / 'record').write_text('system service post-migration data')
    migration.rollback(info)
    assert (migration.SOURCE / 'data/record').read_text() == 'system service post-migration data'
    assert migration.UNIT.read_text() == 'old disabled unit'
    assert info['phase'] == 'rolled-back'


def test_system_service_failed_start_restores_system_service(prepared, monkeypatch):
    info, calls = prepared
    info['source_type'] = 'system_service'
    info['source_enabled'] = True
    info['user_enabled'] = False
    attempts = []
    def health(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('synthetic system service startup failure')
    monkeypatch.setattr(migration, 'wait_health', health)
    with pytest.raises(RuntimeError, match='synthetic system service startup failure'):
        migration.apply(info)
    assert info['phase'] == 'failed-restored'
    assert ['systemctl', 'start', 'mp3metafix.service'] in calls
    assert ['userctl', 'start', 'mp3metafix.service'] not in calls
    assert migration.UNIT.read_text() == 'old disabled unit'
    assert (migration.SOURCE / 'data/record').read_text() == 'original'
    assert (migration.DATA / 'record').read_text() == 'original'


def test_preflight_user_service_detection(prepared, monkeypatch):
    info, calls = prepared
    # Setup mock commands for preflight
    def command(args, check=True):
        if args[:2] == ['systemctl', 'is-active']:
            return 'inactive'
        if args[:2] == ['systemctl', 'is-enabled']:
            return 'disabled'
        return ''
    def userctl(user, *args):
        if len(args) >= 2 and args[0] == 'is-active':
            return 'active'
        if len(args) >= 2 and args[0] == 'is-enabled':
            return 'enabled'
        if len(args) >= 4 and args[0] == 'show' and args[2] == '-p':
            prop = args[3]
            if prop == 'WorkingDirectory':
                return str(migration.SOURCE)
            if prop == 'ExecStart':
                return f'{migration.SOURCE}/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers'
            if prop == 'Environment':
                return 'MP3METAFIX_PORT=8844 MP3METAFIX_HOST=127.0.0.1'
            if prop == 'EnvironmentFiles':
                return ''
        return ''

    monkeypatch.setattr(migration, 'command', command)
    monkeypatch.setattr(migration, 'userctl', userctl)
    def mock_getpwnam(name):
        if name == 'mp3metafix':
            raise KeyError('not found')
        return SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir='/home/desktop', pw_shell='/bin/bash')

    monkeypatch.setattr(migration.pwd, 'getpwnam', mock_getpwnam)
    (migration.SOURCE / '.venv/bin').mkdir(parents=True, exist_ok=True)
    python_bin = migration.SOURCE / '.venv/bin/python'
    if python_bin.exists() or python_bin.is_symlink():
        python_bin.unlink()
    python_bin.symlink_to('/usr/bin/python3')

    # Preflight should detect user_service
    res = migration.preflight('desktop')
    assert res['source_type'] == 'user_service'
    assert res['port'] == 8844
    assert res['host'] == '127.0.0.1'


def test_preflight_system_service_detection(prepared, monkeypatch):
    info, calls = prepared
    # Setup mock commands for preflight system service
    migration.UNIT.write_text('system unit running as root')
    def command(args, check=True):
        if len(args) >= 2 and args[:2] == ['systemctl', 'is-active']:
            return 'active'
        if len(args) >= 2 and args[:2] == ['systemctl', 'is-enabled']:
            return 'enabled'
        if len(args) >= 5 and args[:2] == ['systemctl', 'show'] and args[3] == '-p':
            prop = args[4]
            if prop == 'User':
                return 'root'
            if prop == 'WorkingDirectory':
                return str(migration.SOURCE)
            if prop == 'ExecStart':
                return f'{migration.SOURCE}/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers'
            if prop == 'Environment':
                return 'MP3METAFIX_PORT=8844 MP3METAFIX_HOST=0.0.0.0'
            if prop == 'EnvironmentFiles':
                return ''
        return ''

    monkeypatch.setattr(migration, 'command', command)
    def mock_getpwnam(name):
        if name == 'mp3metafix':
            raise KeyError('not found')
        return SimpleNamespace(pw_uid=0, pw_gid=0, pw_dir='/root', pw_shell='/bin/bash')

    monkeypatch.setattr(migration.pwd, 'getpwnam', mock_getpwnam)
    (migration.SOURCE / '.venv/bin').mkdir(parents=True, exist_ok=True)
    python_bin = migration.SOURCE / '.venv/bin/python'
    if python_bin.exists() or python_bin.is_symlink():
        python_bin.unlink()
    python_bin.symlink_to('/usr/bin/python3')

    # Preflight should detect system_service
    res = migration.preflight('root')
    assert res['source_type'] == 'system_service'
    assert res['port'] == 8844
    assert res['host'] == '0.0.0.0'


