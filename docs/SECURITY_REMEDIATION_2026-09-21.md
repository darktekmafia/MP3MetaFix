# Security remediation verification — v0.5.1

This report follows the [2026-09-20 audit](SECURITY_AUDIT_2026-09-20.md). The original report remains historical evidence; the status below supersedes its open-finding summary.

| Audit finding | Current status | Evidence and limits |
|---|---|---|
| 1. Embedded artwork active content | Fixed at preview/serving boundary | MP3/WAV APIC and M4A covers are decoded and re-encoded as bounded raster images. Claimed MIME is ignored; unsafe covers are omitted from previews/serving without removing original tags. Artwork responses have sandboxed, deny-all CSP. Regression tests cover all three formats. |
| 2. Pre-auth multipart resource exhaustion | Early admission and byte limits implemented | Authentication/access policy and upload rate checks precede parsing. Actual bytes are counted regardless of Content-Length, including chunked input. Upload/artwork requests have a 64 KiB multipart allowance; other mutations are capped at 256 KiB. Read idle timeout is 30 seconds and overall read deadline 300 seconds. One process-shared writer lease bounds simultaneous spooling; save headroom checks reserve up to two temporary copies. Tests exercise unauthorized spooling, dishonest lengths, chunked bodies, and refused saves preserving originals. |
| 3. Account-store errors reopen setup | Fixed | Existing invalid/empty account JSON produces sanitized 503 responses. An initialization marker prevents deleted initialized stores from becoming first-run enrollment. Setup/account mutations are locked across processes and writes use private unique atomic temporary files. Concurrent setup regression proves only one administrator is created. |
| 4. Tokens survive password changes/logout | Fixed, account-wide revocation | Account token signing keys are derived from the application secret, password hash, and account revocation epoch. Password changes and logout invalidate all existing login tokens for that account. Legacy login tokens require sign-in after upgrade; passwords, file sessions, and signing secrets remain intact. |
| 5. Updater overlaps/disconnect/log disclosure | Fixed for in-app updates | A process-shared lock is acquired before accepting the request. A background job survives SSE disconnects; the installer inherits the lock descriptor, retaining exclusion even if the worker exits. Only fixed status messages are streamed; raw subprocess output and exception details are withheld. Mocked tests cover failure, disconnect, and cross-process/inherited-lock lifetime. Web updates abort on Git fetch/fast-forward failures and only install files/dependencies; an explicit local restart activates them. |
| 6. Suno redirects and unbounded reads | Hardened | HTTPS, port 443, allowed hosts, public DNS addresses, certificate validation, pinned resolved addresses, no redirects, bounded response reads, and timeouts. HTML cap 4 MiB; artwork uses configured image limit. Blocking fetches run in worker threads. Tests cover unsafe URLs, private DNS, redirects, and oversized bodies. No live Suno integration test was performed in this remediation pass; destinations requiring redirects/compression are intentionally rejected. |
| 7. Installed service containment | Hardened; account migration deferred | The existing user service was restarted with verified restrictions listed below. It still uses the desktop Unix identity. Dedicated-account migration is explicitly deferred. Disabling the duplicate failing system unit requires local administrator authentication and remains pending. |

## Live deployment verification

The active user service serves `127.0.0.1:8844`; `/api/health` returned `ok`, version `0.5.1` after restart. Installed drop-in: `~/.config/systemd/user/mp3metafix.service.d/50-security.conf`, derived from `deploy/user-hardening.conf`.

Verified properties: `PrivateUsers=yes`, `ProtectSystem=strict`, `ProtectHome=tmpfs`, `PrivateTmp=yes`, `NoNewPrivileges=yes`, `MemoryMax=536870912`, `TasksMax=64`, and `CPUQuotaPerSecUSec=800ms`. The checkout is writable for installed-code updates; home contents and service-manager sockets are inaccessible; only the checkout is explicitly bound into a hidden home when installed there. A disposable sandbox verified denied home/bus access and system-directory writes. The application ran with two workers under the same profile for browser tests.

The separate system-level unit was already failing with 203/EXEC. Its disable attempt was rejected because sudo authentication was unavailable. Complete this maintenance step locally:

```bash
sudo systemctl disable --now mp3metafix.service
```

This command targets the duplicate **system** unit. Continue managing the working unit with `systemctl --user`. Do not migrate accounts, relocate data, or rotate secrets as an incidental cleanup.

## Operational behavior

- Sign in again after deploying v0.5.1. Changing the password or logging out signs out other browsers using the same account.
- The hardened web backend cannot invoke the user/system service manager. An in-app update reports `restart_required`; run `systemctl --user restart mp3metafix.service` locally afterward. CLI maintenance still supports normal service management when run deliberately from a terminal.
- A busy storage writer produces HTTP 503; retry after the current operation. Quota refusal on atomic saves returns 507 and preserves the prior file.
- GET downloads no longer allow `cleanup_after=true` to delete a session; use CSRF-protected `DELETE /api/session`.
- Signing-key initialization is serialized across workers and atomically persisted with 0600 permissions, without rotating existing keys.
- No new runtime packages are required: locking, request limits, and network confinement use the Python standard library plus existing FastAPI/Starlette/Pillow/Mutagen dependencies.

## Verification scope

Full isolated suite: **104 passed**, four warnings (three dependency/API deprecations and one expected oversized-image warning). `pip check` found no broken requirements. Shell and changed JavaScript syntax checks passed.

Real Chromium checks against an isolated, sandboxed two-worker backend passed MP3/AAC M4A/WAV uploads, playback, native-format save/download, restoration, and 320/375/1440px viewport checks without browser page errors. A synthetic Opus-in-MP4 regression verifies its codec and encoded payload survive tag editing. No personal audio fixture was committed.

No destructive installer execution, external penetration test, dependency advisory/CVE scan, or complete hostile-network stress test was performed. Rate limits remain per-worker; one writer is an availability tradeoff, not protection against every slow-client attack. DNS resolution uses the OS resolver; its timing is governed by host resolver configuration. File-session storage is not a tenant authorization model. The retained desktop identity and writable checkout remain deployment tradeoffs. These results verify specific protections, not absolute safety.
