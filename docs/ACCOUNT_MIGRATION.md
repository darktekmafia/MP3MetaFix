# Service-Account Migration & Hardening

This guide covers migrating existing MP3MetaFix services (both desktop **user services** `systemctl --user` and existing **system services** `/etc/systemd/system/mp3metafix.service`) to a dedicated, unprivileged `mp3metafix` Linux system account with strict POSIX permissions and systemd sandbox containment.

## What changes

- Create the non-login system account `mp3metafix`, without reusing an existing personal or root account.
- Run a hardened **system** service as that account, preserving current port, host, environment, and proxy settings.
- Present only `backend`, `frontend`, `assets`, `.venv`, and `VERSION` as read-only mounts under `/opt/mp3metafix` inside the service namespace. These are mappings of the application checkout, not an untracked second code copy. Local edits take effect after restarting the system service.
- Copy data to `/var/lib/mp3metafix` after stopping the source service; verify every regular file by SHA-256 digest, then assign private account ownership and directory/file modes 0700/0600. Preserve passwords, signing secrets, settings, and sessions without rotating credentials.
- Preserve the prior unit configuration and original data. Disable autostart on the prior service only after the new service passes its health and identity checks. Back up original units and keep private migration state/environment under `/var/lib/mp3metafix-migration`.
- Disable web installation through `MP3METAFIX_ALLOW_WEB_UPDATES=false`; update checks remain available. The admin button explains that updates are managed locally. No sudo permission is granted to the web process.

## Preflight and Execution

Read-only preflight check:

```bash
# Via install.sh:
./install.sh --check-account

# Or directly:
python3 scripts/migrate_local_account.py --check
```

Perform the migration with one command (requires sudo or root):

```bash
sudo ./install.sh --migrate-account
```

The preflight check verifies existing destinations/accounts, detects whether the source is a user or system service, verifies working directories, and runs sandbox probes before stopping the working service. The existing virtual environment must use a system Python interpreter.

Once migration succeeds, manage the **system** service:

```bash
sudo systemctl status mp3metafix.service
sudo systemctl restart mp3metafix.service
```

Do not start the old user service alongside it. The old checkout `data/` is a recovery copy, no longer the active data location. Do not launch `run.sh` against that stale copy. The normal update/access installer paths still favor a retained user unit in some cases; their general integration with this migration is not complete. For this local development deployment, update files/dependencies deliberately from the checkout and restart the system service explicitly. Do not use the generic installer to reinstall over the migrated unit.

## Recovery

### Retrying the recovered Fedora attempt

The first live attempt failed before Python started: systemd reported 226/NAMESPACE while masking `/run/dbus/system_bus_socket`. The original user backend recovered and served a healthy v0.5.1 response. A recovery bug missed the system unit's `activating` restart state, leaving the restored duplicate unit restarting; the corrected helper unconditionally stops its job before restoring configuration.

The updated profile hides the entire `/run/dbus` directory with a read-only temporary filesystem instead of mounting an inaccessible socket over the bus socket. Before stopping the working user backend, it now runs a disposable **system-manager** probe under the actual dedicated account. That probe checks imports, private data writes, read-only application mounts, and hidden bus access. The earlier user-manager smoke test did not cover this system-manager boundary.

Retry the recorded, recovered failure with:

```bash
./install.sh --retry-account
```

Retry first stops the duplicate restart loop, validates the recorded checkout/account, then runs the new sandbox probe. If the probe fails, the working user service remains available. If it passes, retry copies the latest source data; earlier destination data is archived privately rather than reused or deleted. The corrected retry subsequently completed on this Fedora workstation; system-service identity and health were independently verified. It does not disable SELinux or remove bus isolation.

On a handled migration failure the helper attempts to restore and start the original service. It retains new files/account for inspection rather than deleting them. Automatic recovery cannot run during a power loss or SIGKILL. An interrupted run has private state for explicit recovery.

To roll back a completed migration, or a recorded interrupted preparation/cutover:

```bash
./install.sh --rollback-account
```

Rollback stops the system service, copies its current data back (including edits after migration), verifies the copy, preserves the earlier checkout data as `data.before-account-rollback`, restores the prior system-unit file, and starts the original user service. New data and migration state remain available. Rollback refuses existing restore staging and chooses a new timestamped backup name when an earlier completed rollback backup exists. Do not invoke rollback after an automatically recovered failure: the original service may have newer data already.

Do not delete migration backups until the running service, login, audio editing, and download have been checked. Repeated apply requests stop without changing an existing migration; the explicit retry action only accepts a recorded recovered failure and does not delete prior data.

## Verification status

Read-only preflight passed on the workstation. A disposable user namespace verified the same selective mounts, relocated Python imports, isolated data, and read-only application files. Automated tests cover private byte-preserving copies, special-file rejection, environment escaping, unit scope, failed startup recovery, and rollback preserving post-migration edits. The corrected helper and full isolated regression suite passed **122 tests** with four existing/expected warnings. Shell/JavaScript syntax and whitespace checks passed. The live retry completed on 2026-09-21. Verification confirmed the main process and both workers run as `mp3metafix`, the system service is enabled/running, and the old user service is disabled/inactive. Health returned `ok`, v0.5.1; the data directory is owned by `mp3metafix` with mode 0700. Effective restrictions include read-only application mounts, writable access to the data directory, hidden system bus, ProtectSystem=strict, ProtectHome=tmpfs, NoNewPrivileges, PrivateTmp, 512 MiB memory, 64 tasks, and 80% CPU. Browser login and a real editing roundtrip after cutover have not yet been confirmed. These checks do not establish Ubuntu LXC compatibility for account migration.
