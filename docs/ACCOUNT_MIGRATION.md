# Local service-account migration

This is a narrowly scoped migration for the standard, two-worker, loopback **user service** in a developer checkout. It is not yet the general installation/account-selection workflow or an LXC system-service migration. The Ubuntu 24.04 LXC upgrade from v0.4.0 to v0.5.1 was reported successful; that does not verify account migration there.

## What changes

- Create the non-login system account `mp3metafix`, without reusing an existing account.
- Run a hardened **system** service as that account, preserving the current port and environment/proxy settings.
- Present only `backend`, `frontend`, `assets`, `.venv`, and `VERSION` as read-only mounts under `/opt/mp3metafix` inside the service namespace. These are mappings of the development checkout, not a second code copy. Local edits still take effect after restarting the system service. The checkout must remain mounted and available at boot; it is still trusted application code.
- Copy data to `/var/lib/mp3metafix` after stopping the old service; verify every regular file by SHA-256, then assign private account ownership and directory/file modes 0700/0600. Preserve passwords, signing secrets, settings, and sessions without rotating credentials.
- Preserve the old user unit and original data. Disable its autostart only after the new service passes its health and identity checks. Back up any disabled system unit and keep private migration state/environment under `/var/lib/mp3metafix-migration`.
- Disable web installation through `MP3METAFIX_ALLOW_WEB_UPDATES=false`; update checks remain available. The admin button explains that updates are managed locally. No sudo permission is granted to the web process.

## Run locally

Read-only preflight, as the desktop user:

```bash
python3 scripts/migrate_local_account.py --check
```

From the checkout, perform the migration with one command. Sudo requests the desktop user's administrator password in the terminal:

```bash
./install.sh --migrate-account
```

The helper refuses existing destinations/accounts, active or enabled system services, unsupported customized launch commands, external environment files, custom data locations, ambiguous paths, and symlink/special-file data. It downloads no packages and makes no remote Git changes. The existing virtual environment must use a system Python interpreter. Account selection and general deployment conversion remain planned.

Once migration succeeds, manage the **system** service:

```bash
sudo systemctl status mp3metafix.service
sudo systemctl restart mp3metafix.service
```

Do not start the old user service alongside it. The old checkout `data/` is a recovery copy, no longer the active data location. Do not launch `run.sh` against that stale copy. The normal update/access installer paths still favor a retained user unit in some cases; their general integration with this migration is not complete. For this local development deployment, update files/dependencies deliberately from the checkout and restart the system service explicitly. Do not use the generic installer to reinstall over the migrated unit.

## Recovery

On a handled migration failure the helper attempts to restore and start the original service. It retains new files/account for inspection rather than deleting them. Automatic recovery cannot run during a power loss or SIGKILL. An interrupted run has private state for explicit recovery.

To roll back a completed migration, or a recorded interrupted preparation/cutover:

```bash
./install.sh --rollback-account
```

Rollback stops the system service, copies its current data back (including edits after migration), verifies the copy, preserves the earlier checkout data as `data.before-account-rollback`, restores the prior system-unit file, and starts the original user service. New data and migration state remain available. Rollback refuses to overwrite existing recovery directories. Do not invoke rollback after an automatically recovered failure: the original service may have newer data already.

Do not delete migration backups until the running service, login, audio editing, and download have been checked. Repeated apply requests stop without changing an existing migration; this first local utility does not automatically clean up or retry a failed migration.

## Verification status

Read-only preflight passed on the workstation. A disposable user namespace verified the same selective mounts, relocated Python imports, isolated data, and read-only application files. Automated tests cover private byte-preserving copies, special-file rejection, environment escaping, unit scope, failed startup recovery, and rollback preserving post-migration edits. The full isolated regression suite passed **118 tests** (four existing/expected warnings); shell/JavaScript syntax and whitespace checks passed. Root account creation and final system-service cutover still require local administrator authentication and must be verified after execution. These checks do not establish Ubuntu LXC compatibility for account migration.
