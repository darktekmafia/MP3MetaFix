#!/usr/bin/env python3
"""Local developer-checkout migration. No package downloads or remote Git operations.

Run --check without sudo; --apply/--rollback require sudo from the desktop user.
Only the standard two-worker user service is supported. General installer migration
and arbitrary customized service conversion remain separate roadmap work.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import urllib.request

NAME = 'mp3metafix'
SOURCE = Path(__file__).resolve().parents[1]
RUNTIME = Path('/opt/mp3metafix')
DATA = Path('/var/lib/mp3metafix')
STATE = Path('/var/lib/mp3metafix-migration')
UNIT = Path('/etc/systemd/system/mp3metafix.service')
PARTS = ('backend', 'frontend', 'assets', '.venv', 'VERSION')


def command(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        # Unit environments, journal output and account data may contain secrets.
        raise RuntimeError('Command failed: ' + ' '.join(args[:2]) + '; inspect local service status.')
    return result.stdout.strip()


def userctl(user, *args):
    account = pwd.getpwnam(user)
    cmd = ['systemctl', '--user', *args]
    if os.geteuid() == 0:
        cmd = ['runuser', '-u', user, '--', 'env', f'XDG_RUNTIME_DIR=/run/user/{account.pw_uid}',
               f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{account.pw_uid}/bus', *cmd]
    return command(cmd)


def safe_path(path):
    # systemd specifiers, escaping and whitespace require a more general renderer.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)) or '..' in path.parts:
        raise ValueError('This migration requires ordinary absolute paths without spaces or specifiers.')
    return path


def file_manifest(root):
    """Hash regular data only; never follow links or copy device/socket files."""
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Data root must be a real directory.')
    for path in root.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError('Data contains a link or special file; migration stopped.')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        result[str(path.relative_to(root))] = digest.hexdigest()
    return result


def private_copy(source, target, uid, gid):
    expected = file_manifest(source)
    shutil.copytree(source, target)
    if file_manifest(target) != expected:
        raise RuntimeError('Data verification failed; original remains intact.')
    for path in [target, *target.rglob('*')]:
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    return expected


def atomic_write(path, text, mode=0o600):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.migration-')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def environment_lines(values):
    lines = []
    for value in values:
        if '\n' in value or '\r' in value or '\x00' in value or '=' not in value:
            raise ValueError('Unsupported environment value.')
        key, val = value.split('=', 1)
        if not re.fullmatch(r'[A-Z_][A-Z0-9_]*', key):
            raise ValueError('Unsupported environment key.')
        # Quoted EnvironmentFile values: preserve literal backslash, quote, dollar, backtick.
        val = re.sub(r'([\\"$`])', r'\\\1', val)
        lines.append(f'{key}="{val}"')
    return '\n'.join(lines) + '\n'


def unit_text(source, runtime, data):
    for path in (source, runtime, data):
        safe_path(path)
    binds = '\n'.join(f'BindReadOnlyPaths={source / part}:{runtime / part}' for part in PARTS)
    return f'''[Unit]
Description=MP3MetaFix dedicated local service
After=network.target
RequiresMountsFor={source} {data}

[Service]
Type=simple
User={NAME}
Group={NAME}
WorkingDirectory={runtime}
EnvironmentFile={STATE}/service.env
ExecStart=/usr/bin/env {runtime}/.venv/bin/python -m uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers
Restart=on-failure
RestartSec=3
ProtectSystem=strict
ProtectHome=tmpfs
{binds}
ReadWritePaths={data}
InaccessiblePaths=-/run/user -/run/dbus/system_bus_socket
PrivateTmp=true
NoNewPrivileges=true
CapabilityBoundingSet=
UMask=0077
MemoryMax=512M
TasksMax=64
CPUQuota=80%

[Install]
WantedBy=multi-user.target
'''


def wait_health(port, attempts=40):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(attempts):
        try:
            with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
                if json.load(response).get('status') == 'ok':
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError('Backend health check failed.')


def preflight(user):
    import shlex
    safe_path(SOURCE)
    account = pwd.getpwnam(user)
    if account.pw_uid == 0:
        raise ValueError('Run from the desktop account using sudo, not a root login.')
    if STATE.exists() or DATA.exists() or RUNTIME.exists():
        raise ValueError('A migration/destination already exists. Inspect it or use --rollback; nothing overwritten.')
    try:
        pwd.getpwnam(NAME)
    except KeyError:
        pass
    else:
        raise ValueError('The target account already exists; refusing to repurpose it.')
    if Path(str(UNIT) + '.d').exists():
        raise ValueError('Existing system-service drop-ins need separate review.')
    if command(['systemctl', 'is-active', NAME + '.service'], check=False) == 'active':
        raise ValueError('A system service is already active.')
    if command(['systemctl', 'is-enabled', NAME + '.service'], check=False) not in ('disabled', 'not-found'):
        raise ValueError('The old system unit must be disabled before migration.')
    if userctl(user, 'is-active', NAME + '.service') != 'active':
        raise ValueError('The source user service must be running.')
    if userctl(user, 'show', NAME + '.service', '-p', 'WorkingDirectory', '--value') != str(SOURCE):
        raise ValueError('Source service does not use this checkout.')
    if userctl(user, 'show', NAME + '.service', '-p', 'EnvironmentFiles', '--value'):
        raise ValueError('EnvironmentFile customization requires separate review.')
    launch = userctl(user, 'show', NAME + '.service', '-p', 'ExecStart', '--value')
    expected = f'{SOURCE}/.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers'
    if f'argv[]={expected} ;' not in launch:
        raise ValueError('Customized launch command requires separate review.')
    values = shlex.split(userctl(user, 'show', NAME + '.service', '-p', 'Environment', '--value'))
    env = dict(v.split('=', 1) for v in values)
    if env.get('MP3METAFIX_DATA_DIR') != str(SOURCE / 'data'):
        raise ValueError('Custom data location requires separate review.')
    if env.get('MP3METAFIX_HOST') != '127.0.0.1':
        raise ValueError('This local migration requires the existing loopback listener.')
    port = int(env['MP3METAFIX_PORT'])
    if not 1024 <= port <= 65535:
        raise ValueError('Unsupported port.')
    for part in PARTS:
        if (SOURCE / part).is_symlink() or not (SOURCE / part).exists():
            raise ValueError('Application paths must exist and not be symbolic links.')
    if not str((SOURCE / '.venv/bin/python').resolve()).startswith('/usr/'):
        raise ValueError('The virtual environment must use a system Python interpreter.')
    file_manifest(SOURCE / 'data')
    env.update(PATH=f'{RUNTIME}/.venv/bin:/usr/local/bin:/usr/bin:/bin',
               MP3METAFIX_DATA_DIR=str(DATA), MP3METAFIX_ALLOW_WEB_UPDATES='false')
    environment_lines([f'{k}={v}' for k, v in env.items()])
    return {'user': user, 'uid': account.pw_uid, 'gid': account.pw_gid, 'port': port,
            'user_enabled': userctl(user, 'is-enabled', NAME + '.service') == 'enabled',
            'environment': env}


def save_state(info):
    atomic_write(STATE / 'state.json', json.dumps(info, indent=2) + '\n')


def restore_service(info):
    if command(['systemctl', 'is-active', NAME + '.service'], check=False) == 'active':
        command(['systemctl', 'stop', NAME + '.service'])
    command(['systemctl', 'disable', NAME + '.service'], check=False)
    if (STATE / 'original-system.service').exists():
        atomic_write(UNIT, (STATE / 'original-system.service').read_text(), 0o644)
    else:
        UNIT.unlink(missing_ok=True)
    command(['systemctl', 'daemon-reload'])
    if info['user_enabled']:
        userctl(info['user'], 'enable', NAME + '.service')
    userctl(info['user'], 'start', NAME + '.service')
    wait_health(info['port'])


def rollback(info):
    staging = SOURCE / 'data.migration-restore'
    backup = SOURCE / 'data.before-account-rollback'
    if info.get('data_copied') and (staging.exists() or backup.exists()):
        raise RuntimeError('Restore staging/backup already exists; services left unchanged.')
    if command(['systemctl', 'is-active', NAME + '.service'], check=False) == 'active':
        command(['systemctl', 'stop', NAME + '.service'])
    userctl(info['user'], 'stop', NAME + '.service')
    # Preserve edits made after cutover when rolling back; retain both data copies.
    if info.get('data_copied'):
        private_copy(DATA, staging, info['uid'], info['gid'])
        (SOURCE / 'data').rename(backup)
        staging.rename(SOURCE / 'data')
    restore_service(info)
    info['phase'] = 'rolled-back'
    save_state(info)


def apply(info):
    STATE.mkdir(mode=0o700)
    env = info.pop('environment')
    info.update(source=str(SOURCE), phase='preparing', data_copied=False)
    if UNIT.exists():
        if UNIT.is_symlink() or not UNIT.is_file():
            raise ValueError('System unit must be an ordinary file.')
        atomic_write(STATE / 'original-system.service', UNIT.read_text())
    save_state(info)
    atomic_write(STATE / 'service.env', environment_lines([f'{k}={v}' for k, v in env.items()]))
    try:
        command(['useradd', '--system', '--user-group', '--home-dir', str(DATA), '--no-create-home',
                 '--shell', '/usr/sbin/nologin', NAME])
        service = pwd.getpwnam(NAME)
        RUNTIME.mkdir(mode=0o755)
        for part in PARTS:
            target = RUNTIME / part
            if (SOURCE / part).is_dir():
                target.mkdir(mode=0o755)
            else:
                target.touch(mode=0o644)
        # Stop the source before snapshotting any mutable account/session data.
        userctl(info['user'], 'stop', NAME + '.service')
        info['phase'] = 'source-stopped'
        save_state(info)
        private_copy(SOURCE / 'data', DATA, service.pw_uid, service.pw_gid)
        info['data_copied'] = True
        save_state(info)
        atomic_write(UNIT, unit_text(SOURCE, RUNTIME, DATA), 0o644)
        command(['systemd-analyze', 'verify', str(UNIT)])
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'start', NAME + '.service'])
        wait_health(info['port'])
        if command(['systemctl', 'show', NAME + '.service', '-p', 'User', '--value']) != NAME:
            raise RuntimeError('Service identity verification failed.')
        command(['systemctl', 'enable', NAME + '.service'])
        userctl(info['user'], 'disable', NAME + '.service')
        info['phase'] = 'complete'
        save_state(info)
    except BaseException:
        # Keep all new directories/account for inspection. Never delete user data.
        if info.get('data_copied'):
            rollback(info)
        else:
            restore_service(info)
        info['phase'] = 'failed-restored'
        save_state(info)
        raise
    print('Migration complete: dedicated system service healthy. Existing user unit/data retained for recovery.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--apply', action='store_true')
    action.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if not args.check and os.geteuid() != 0:
        parser.error('Apply/rollback require sudo administrator authentication.')
    if args.rollback:
        info = json.loads((STATE / 'state.json').read_text())
        if info.get('source') != str(SOURCE) or info.get('phase') not in ('complete', 'source-stopped', 'preparing'):
            raise ValueError('No supported migration state to roll back.')
        rollback(info)
        print('Original user service restored, including current migrated data.')
        return
    user = os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name
    info = preflight(user)
    if args.check:
        print(f'Preflight passed. Plan: {user} user service -> {NAME} system service, port {info["port"]}.')
        print(f'Read-only application mounts: {RUNTIME}; private data: {DATA}.')
        print('No secrets displayed; no changes made. Web installation disabled for this read-only deployment.')
    else:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        apply(info)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        # No subprocess output, environments, or account content is printed.
        print(f'Migration stopped: {exc}')
        raise SystemExit(1)
