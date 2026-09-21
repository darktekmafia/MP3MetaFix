# MP3MetaFix security controls and open findings

Last reviewed: 2026-09-21, application v0.5.1.

The [original audit](SECURITY_AUDIT_2026-09-20.md) is historical evidence. The [remediation verification report](SECURITY_REMEDIATION_2026-09-21.md) records the six application fixes, tested user-service hardening, and remaining deployment tradeoffs. The subsequent local migration is complete: the system service runs under a dedicated account and the old user service is disabled. General installer/LXC migration remains outstanding. No claim of absolute security is made.

## Existing controls and their limits

### Identity and authorization

- Account cookies (`mp3metafix_auth`) and temporary file-session cookies (`mp3metafix_session`) serve distinct purposes. Both use timestamped HMAC signatures, HttpOnly, and SameSite=Lax; Secure is set when the application sees HTTPS.
- File cookies do not grant administrator privilege. Editing routes use access-policy dependencies; telemetry and update routes require administrators. Guest Mode allows anonymous editing, not admin operations.
- Static UI/assets, health/version, and authentication entry points are public. UI login prompts are not backend authorization. Settings reads require authentication; writes require admin privilege.
- Passwords use PBKDF2-HMAC-SHA256 with 600,000 rounds and a random salt. Account tokens expire after seven days but are invalidated on password changes and logout through credential-derived signing keys and an account epoch. Logout revokes all login tokens for that account.
- Login and upload rate limits are in-memory and per worker. Upload access/rate/actual-byte checks happen before multipart parsing. They are not a complete denial-of-service defense.

### Files and request processing

- File-session UUIDs are verified and converted to hashed storage directory names. Session directories request 0700 permissions; account JSON files request 0600 inside a 0700 directory.
- Audio lookups use fixed allowlisted basenames; filename sanitization removes traversal/control characters and preserves the actual extension.
- Endpoint audio limits default to 150 MiB; uploaded artwork defaults to 10 MiB. Early request limits include a 64 KiB multipart allowance, with actual-byte counting even without trustworthy Content-Length. Other mutation bodies have a 256 KiB cap. Artwork and Suno responses use bounded reads.
- The 2 GiB session-storage setting drives a quota precheck and LRU cleanup, a conservative full-upload headroom check under a shared writer lease. Atomic saves check for up to two extra copies before modifying files. TTL cleanup runs periodically.
- Quota/TTL values saved in the settings UI do not currently reconfigure runtime enforcement, which uses environment-derived values.
- Pydantic field bounds limit accepted metadata values, alongside an early 256 KiB total mutation-body cap before JSON parsing.
- Atomic metadata writes protect the previous file on write failure; storage writers are serialized across processes to stabilize quota/headroom decisions.

### Audio and images

- MP3/M4A/WAV extensions are allowlisted and checked against the initial 8 KiB before writing to session storage (after framework multipart spooling).
- M4A/WAV undergo bounded container traversal and Mutagen parsing. M4A handlers target AAC, ALAC, and Opus audio-only files. WAV requires valid RIFF lengths and format/data chunks; INFO input/output is capped at 1 MiB.
- Stream/download MIME is selected by audio format. Downloads use attachment filenames. Byte-range requests reject invalid/unsupported ranges.
- Separately uploaded artwork passes magic checks, Pillow verification/re-encoding, and dimension checks (4096px per side). `Image.MAX_IMAGE_PIXELS=10_000_000` configures Pillow's warning/error thresholds; a separate explicit check rejects images above ten million pixels.
- **Embedded artwork uses the same normalization and size/image checks before serving.** Untrusted MIME is ignored. Invalid covers are omitted from previews without silently deleting embedded tags; artwork responses use a deny-all sandboxed CSP.

### Browser, proxy, and outbound boundaries

- Dynamic metadata/toast text uses safe DOM construction/textContent. Remaining inspected innerHTML assignments use static markup or empty strings.
- CSRF middleware checks Fetch-Site and Origin on mutating methods. SameSite cookies provide additional protection. Localhost development exceptions remain in Origin handling.
- CSP, nosniff, and SAMEORIGIN headers exist, but CSP permits inline scripts. Embedded HTML is now rejected, and artwork has a separate sandboxed deny-all policy. The backend does not emit HSTS; configure HTTPS/HSTS at the proxy as appropriate.
- Forwarded headers are resolved only when proxy trust is enabled and the peer matches configured proxies. Default binding is loopback. Reverse-proxy reachability was not audited.
- Suno fetches start from constructed song URLs or allowlisted artwork hosts, with HTTPS/public-IP validation, pinned DNS addresses, no redirects, and bounded responses. HTML and artwork fetches run off the event loop.

### Updates and deployment

- `/api/updates/apply` is enabled and admin/CSRF protected. A shared file lock is acquired before acceptance, held by a background task, and inherited by the installer. Browser disconnect does not release it.
- SSE returns only fixed status messages; raw installer output and exception details are withheld. Web updates cannot manage services and explicitly require a local restart.
- Service templates contain restrictions; installed units may differ. The migrated system unit has a verified dedicated identity, NoNewPrivileges, private temporary storage, hidden home/bus contents, read-only application mounts, private writable data, and memory/task/CPU limits. The old user unit is inactive and disabled.
- Service migration/access scripts use scoped parsing, validated inputs, and atomic unit writes. Preserving an existing unit does not add missing containment automatically.

## Verification

The final isolated regression run passed **104 tests**, with four warnings. New regressions verify rejection of active artwork, pre-auth spooling protection, token revocation, fail-closed account storage, update lifetime locking, outbound restrictions, and Opus preservation. Tests use temporary data and mock privileged/network effects, not live accounts.

No CVE/dependency advisory scan, destructive stress test, external penetration test, live updater execution, or complete browser exploit test was performed. See [the audit](SECURITY_AUDIT_2026-09-20.md). Fixes must add regression coverage and follow [development_workflow.md](development_workflow.md), including local-only commits until explicit remote approval.

## Local migration follow-up (unreleased)

Duplicate system-unit disablement was reported completed. The dedicated-account [migration helper](ACCOUNT_MIGRATION.md) completed locally with private verified data copying, selective read-only code mounts, and rollback. The migrated service denies web updates without granting service-manager privileges. Live system-service identity, health, and restrictions were verified after successful retry. Browser login/editing confirmation remains outstanding. General installer integration and LXC account-migration testing remain outstanding.

The first Fedora system-service migration attempt failed at namespace setup while masking the system bus socket; the user backend recovered. The corrected profile hides the bus directory using a read-only temporary filesystem and probes the actual dedicated-account/system-manager boundary before any retry stops the working backend. Retry/recovery preserves previous data and stops queued system-service restarts. The corrected retry completed successfully; SELinux enforcement was not disabled.

Release governance requires remote development pushes to target `development` and an explicit maintainer request for promotion to `main` after testing and feedback. This process is not an additional runtime security control. Admin-only frontend update discovery and strict installer branch enforcement remain planned; backend authorization must be preserved.
