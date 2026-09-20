# MP3MetaFix session handoff — 2026-09-20

This working document is intended for the owner and the next LLM continuing this project. It records completed work, verification, known failures, and unresolved security findings. Audit recommendations below are not implemented fixes or permission to change privileged infrastructure.

## Start here

- Project: `/run/media/psychlone/Projects/mp3metafix`.
- Branch at handoff: `main`; latest implementation commit: `da83731`; application version: **0.5.0**.
- **Local Git only. Do not push to GitHub, publish, release, or deploy remotely without explicit owner approval.** Nothing was pushed during this session.
- The owner confirmed WAV editing works perfectly. Their real M4A file failed parsing. **M4A support is not fully validated against actual Suno files.** Synthetic AAC files passed; root cause of the owner's failure remains unknown.
- The final security audit found unresolved high- and medium-severity issues. Full findings and reproduction details appear below. Passing regression tests does not mean these findings are resolved.
- Working tree was clean before creating this handoff. This document was initially an uncommitted working file. The owner subsequently requested a full documentation refresh; it is now included in that local documentation commit. No application code was changed after the audit.

## Owner's intent and preferences

The owner moved from Antigravity after an agent disconnected mid-work. They asked for codebase familiarization, including AGENTS.md and RULES.md, then completion of telemetry/UI cleanup, a console-error fix, and native M4A/WAV support.

They prefer visible progress and read the roadmap in a Markdown reader. Keep progress updates clear and maintain ROADMAP.md. All capabilities implemented in `/app` should be shared with `/manager`; this is an architectural requirement, not a statement that the manager UI is already complete.

The owner explicitly requested the backend/service restart after implementation. That restart was completed. They then requested a final security audit, explicitly leaving the M4A parsing issue to themselves. The audit did not authorize implementation of all its recommendations. Their final request was this detailed handoff file.

## Project instructions and constraints

Read `AGENTS.md` and the authoritative `docs/development_workflow.md` before making changes. No RULES.md was found. `.agents/rules/development_workflow.md` also exists as an older workflow copy; use the authoritative docs version.

Key requirements:

- Signed, timestamped HMAC file-session cookies and separate account authentication; never treat a file-session cookie as login or admin authorization.
- Hashed session storage with 0700 directories; no raw paths/session identifiers in API responses; preserve user data and secrets.
- Validate upload signatures, container structure, bounds, image dimensions, and CSRF; safe DOM construction for untrusted metadata.
- `/app` is mobile-first, including 320/375px layouts, no horizontal overflow, and suitable touch targets.
- External metadata/Suno merges must remain selectable and non-destructive. Never blindly replace existing user fields/artwork.
- Before commits, synchronize CHANGELOG.md, README.md, ROADMAP.md, docs/ARCHITECTURE.md, docs/DEPLOYMENT.md, docs/SECURITY_HARDENING.md, and required version references.
- Run the complete test suite, self-review, then use a local conventional commit. Never blindly push.
- Changes to `/api/updates/apply` have an explicit owner-approval checkpoint and must preserve admin authorization, CSRF protection, concurrency control, and interruption safety.
- Do not silently change service identity, permissions, secrets, or deployment infrastructure. Scope any such change with the owner.

## Completed work and local commits

### `a56ac85` — fix: consolidate telemetry in admin and simplify mobile hub

- Removed telemetry and the intrusive quickbar from the Gateway Hub, retaining telemetry in `/admin`.
- Removed recurring hub resource polling; retained a one-time health/version check.
- Fixed header stacking so the account dropdown remains above workspace cards, including mobile.
- Discovered that an existing updater test could invoke the real installer; changed the test to mock the subprocess with AsyncMock and verify SSE output. Future tests must not run the real installer.
- Synchronized then-current 0.4.1 version fallbacks and required documentation.

### `6ba0f43` — fix: handle absent editor update notification badge

Owner-reported error:

```text
app.js?v=0.4.1:1244 Update check failed:
TypeError: Cannot read properties of null (reading 'classList')
    at checkForUpdates (app.js?v=0.4.1:1229:21)
```

The `/app` markup lacks an optional update badge still referenced by JS. Both the update-available and up-to-date branches now guard its absence. Browser checks exercised automatic and manual checks without the badge, with no console warnings/errors. Documentation was synchronized.

### `da83731` — feat: support native M4A and WAV metadata editing

Version advanced to **0.5.0** across VERSION, backend config fallback, install.sh fallback, frontend asset query strings/version badges, and docs.

Implementation:

- New `backend/audio_formats.py`: shared format descriptors and bounded MP3/M4A/WAV signature/container checks. M4A checks ftyp/box bounds and audio-only track handlers; WAV checks RIFF length/chunks/fmt/data bounds.
- New `backend/wave_metadata.py`: reads existing RIFF INFO text as fallback and synchronizes mapped existing INFO fields on save while preserving unknown entries/chunks. Aggregate INFO input/output bounded to 1 MiB.
- `backend/metadata_engine.py`: MP3 retains ID3; WAV uses Mutagen WAVE/ID3; M4A uses native MP4 atoms and JPEG/PNG covr artwork. AAC/ALAC are intended M4A codecs. No transcoding or re-encoding.
- Shared normalized fields cover title, artist, album, album artist, genre, year, track/disc numbers and totals, composer, BPM, comment, lyrics, and artwork.
- M4A numeric atoms require whole numbers 0–65535 or blank; zero follows absent/unset semantics. Invalid values return 422 without replacing the audio file.
- Atomic sibling-copy/save/replace protects the original on write failures. WAV INFO synchronization may require another temporary copy; account for disk overhead.
- Fixed Mutagen's falsey untagged-file handling so untagged audio stream info is still extracted.
- Format-aware upload selection, storage filenames, filename sanitization, download/stream MIME types, native save-dialog filters, filename previews, restored sessions, and UI labels.
- MP3 legacy sessions with `audio.mp3` remain supported.
- Fixed remove-artwork-then-upload-replacement staging, and reset frontend removal state after successful save.
- Added synthetic AAC fixture `tests/fixtures/tone.m4a`, narrow .gitignore exception, fixture provenance README, and audio roundtrip/security tests. WAV fixtures are generated in Python. No new runtime dependencies; FFmpeg is not required to run the tests.
- Tests now isolate backend session storage and reset upload rate-limit state per test rather than touching user session storage.
- Updated the roadmap and all required documentation.

`/manager` remains a workspace shell, not a completed editor. The shared API/metadata engine supplies format support for its eventual inspector. Do not report that full manager UI integration was completed.

## Validation completed

Final full-suite command (run outside the restrictive sandbox because TestClient/ASGI operations stall inside it):

```bash
MP3METAFIX_DATA_DIR=$(mktemp -d /tmp/mp3metafix-tests-XXXXXX) ./.venv/bin/pytest -o faulthandler_timeout=30
```

Final audit rerun: **72 passed, 3 warnings in 29.69 seconds**. Warnings concern Starlette/httpx, AnyIO BlockingPortal, and the old HTTP_422 constant naming. Earlier implementation runs also passed all 72 tests.

Coverage added includes:

- Full MP3/M4A/WAV API roundtrip with Unicode tags, artwork replacement/removal, session restoration, download extensions/MIME, byte ranges, and unchanged encoded audio payloads.
- New format authentication/CSRF checks; malformed/truncated/mismatched uploads; cleanup and initial-chunk size limits.
- M4A numeric validation leaving original bytes unchanged.
- Unknown metadata/artwork preservation; WAV INFO fallback, synchronization, and unknown-chunk preservation.
- Legacy MP3 sessions, filename normalization, and failed atomic writes preserving originals.

Real headless Chrome/Playwright checks used a separate backend on port **18845**, temporary data/accounts, and synthetic two-second tones:

- MP3, AAC M4A, and WAV upload, waveform readiness, playback progress, editing, native-format download, reload/session restoration.
- Native save-dialog options and downloaded Blob MIME for each format.
- No document horizontal overflow at widths 320, 375, and 1440; mobile screenshots inspected.
- No browser page errors.
- External font requests and update checks were mocked; editing API calls used the actual isolated backend.

The temporary server was stopped afterward. The real user backend on 8844 was not used for test accounts. These synthetic tests do **not** establish compatibility with the owner's failing M4A file or all browsers/codecs.

Also completed: `git diff --check`, JS syntax validation, and implementation diff review.

## Unresolved M4A failure — important for continuation

Owner's exact report: “It works perfect on .wav files, it could not parse the m4a file but I will work on that myself.”

The owner subsequently supplied this browser error:

```text
app.js?v=0.5.0:283  POST http://localhost:8844/api/upload 422 (Unprocessable Content)
handleFileUpload @ app.js?v=0.5.0:283
await in handleFileUpload
(anonymous) @ app.js?v=0.5.0:184
```

This confirms an HTTP 422 response from the upload endpoint. For a normal file upload, the current handler returns 400 for extension/header mismatches and 422 when container/metadata extraction fails, so the evidence points toward the parsing stage after the initial checks. FastAPI request validation can also return 422; inspect the response JSON and sanitized backend exception type to confirm which path was taken. The browser stack identifies the upload call, not the underlying parser error.

No failing sample, codec details, response JSON, or backend log exception type was provided or examined. Do not claim a diagnosis or silently weaken validation.

Likely investigation points, **hypotheses only**:

1. `matches_audio_header` requires ftyp as the first box, size within the initial 8192 bytes, a multiple-of-four box size, and major brand in M4A/isom/iso2/mp41/mp42/qt. Real-world brand/box variations may be rejected.
2. `validate_audio_container` requires bounded box structure, moov + mdat, a soun handler, and no vide handler.
3. `_extract_m4a` only accepts Mutagen codec strings beginning with `mp4a` or equal to `alac`; actual files may differ.
4. Distinguish API 400 header mismatch from API 422 parse failure before changing code. Upload logs deliberately show exception type without raw metadata or filesystem paths.
5. Inspect an owner-provided copy read-only with container/codec tooling and Mutagen; preserve original bytes and sensitive tags. Add a minimized/provenance-safe regression fixture only with suitable permission.
6. Maintain actual format/signature checks and metadata preservation. Do not solve compatibility by accepting arbitrary MP4/video data or converting the audio without user choice.

## Backend/service state at end of session

The owner authorized a restart. Inspection found two separate units named `mp3metafix.service`:

- **Working user unit**: `systemctl --user ...`; runs project `.venv/bin/uvicorn backend.main:app --host $MP3METAFIX_HOST --port $MP3METAFIX_PORT --workers 2 --no-proxy-headers`, working directory the project root.
- **Failing system unit**: `/etc/systemd/system/mp3metafix.service`, enabled and repeatedly failing with status **203/EXEC**, permission denied executing the project virtualenv's uvicorn. It was not modified or disabled.

Executed `systemctl --user restart mp3metafix.service`. Verified afterward:

- User unit active/running; new main PID was 270713 (historical observation, recheck before operating on processes).
- Listener `127.0.0.1:8844`.
- `/api/health`: status ok, version 0.5.0.
- `/api/version`: version 0.5.0, commit da83731, branch main, systemd service true.

No daemon-reload or service-file changes were made. Restarting the user service restarted the actual backend and its workers. Reconcile the duplicate units deliberately rather than restarting both or broadly changing permissions.

## Security audit — full findings

**All findings below remain unresolved.** The audit was read-only with respect to application code, deployment configuration, and live user data. The prior security-hardening documentation overstates some guarantees, especially update serialization/log sanitization and live service containment. Treat the evidence below as the current assessment.

# MP3MetaFix security audit — 2026-09-20

Reviewed local commit da83731 (v0.5.0). Audit only: no application edits, installer executions, credential changes, or remote pushes. M4A compatibility debugging was excluded at the owner's request.

## Findings, in priority order

### 1. High — Embedded artwork can become same-origin active content
References: backend/main.py:620–623; backend/metadata_engine.py:158–171 and get_embedded_artwork_binary; backend/security.py:247.
An MP3/WAV ID3 APIC frame supplies both bytes and MIME type without the normalization required for separately uploaded artwork. The artwork endpoint returns that MIME verbatim. An isolated synthetic MP3 containing an APIC frame with text/html and an inert script marker was accepted by extraction; the endpoint returned the HTML unchanged. The current CSP permits inline scripts.
Impact: a victim who loads a crafted audio file and opens /api/artwork as a document can execute attacker content with the application's origin and authenticated access. This is a user-interaction-dependent stored XSS path, not automatic execution in the editor's image element. HttpOnly cookies do not prevent same-origin authenticated requests.
Recommendation: validate and re-encode embedded artwork to an allowlisted raster format before previews or serving; enforce byte/pixel bounds; reject HTML/SVG and ignore claimed MIME. Add negative MP3 and WAV tests and browser execution tests. Consider restrictive resource-specific CSP as defense in depth.

### 2. High for exposed deployments — Upload limits run after unauthenticated multipart spooling
References: backend/main.py:491–547, 625–637; installed FastAPI routing.py:430,481; Starlette formparsers.py:181–188,282–288.
The framework parses multipart bodies before authentication dependencies and endpoint-level size checks. An isolated ASGI probe confirmed that a 2 MiB unauthenticated upload was fully written through UploadFile.write before returning 401. Parser file parts do not have the ordinary text-part size limit. The upload rate limiter also runs after parsing.
Impact: a reachable client can consume temporary disk and I/O without an account; declared audio limits do not cap this preprocessing. No destructive large-body stress test was run.
Recommendation: enforce actual streamed request-byte limits before multipart parsing and early access checks where possible; configure matching proxy limits and test missing or dishonest Content-Length plus chunked bodies. Separately, artwork upload and Suno downloads currently read complete bodies before size validation. The 10 MiB quota precheck does not reserve the full audio upload or atomic-save copies across workers.

### 3. High impact, conditional — Account-store errors reopen administrator enrollment
References: backend/auth.py:205–215,257–292; backend/main.py:246–269.
Unreadable/malformed users.json is treated as an empty user database. An isolated test corrupted only a temporary test database and confirmed that setup became available and a replacement administrator could be created.
Prerequisite: account-store corruption/read failure; no independent remote way to cause that prerequisite was demonstrated. This is not a bypass of a healthy account database.
Recommendation: distinguish never-initialized installation from storage failure; fail closed on existing invalid account data; require explicit local recovery. Serialize first-run creation across workers and use unique atomic temporary writes.

### 4. Medium — Password changes and logout do not revoke issued account tokens
References: backend/auth.py:73–100,321–339; backend/main.py:172–189,306–321.
Tokens contain user ID and issuance time but are not checked against credential version or a revocation record. A pre-change token still returned 200 from authenticated /api/settings after changing the password in an isolated test. Logout deletes the browser cookie only.
Impact: a previously stolen token can remain usable until the seven-day expiration, even after password recovery.
Recommendation: validate an account/session version or revocation state on every request; invalidate old tokens on password change and provide deliberate session revocation behavior.

### 5. Medium — Update installation is not serialized across workers or disconnects
References: backend/main.py:108,467–488; backend/updater.py:190–244.
The live service runs two Uvicorn workers; asyncio.Lock is process-local. Different workers can launch concurrent installers. The pre-stream locked() check also happens before acquisition. The subprocess generator has no finally block that terminates/waits for the installer on cancellation, so client disconnect can release the lock while installation continues. Output lines and exception strings are returned without the sanitization asserted by existing documentation.
Recommendation: cross-process exclusive locking covering the full installer lifetime, defined disconnect/cancellation recovery, and log/error sanitization; tests with mocked subprocesses and multiple workers. No actual installer or update endpoint was invoked. Modifying this privileged flow requires the project's explicit owner approval checkpoint.

### 6. Medium — Suno artwork allowlist does not constrain redirects
References: backend/main.py:725–737; backend/suno_extractor.py:189–205.
Only the initial artwork hostname is checked, then urllib's default redirect handling follows redirects. Scheme and port are not constrained, and the full response is read before the byte-size check.
Impact: an allowlisted endpoint redirecting to another destination could bypass the intended outbound-host boundary. No exploitable redirect on Suno was demonstrated, so SSRF exploitability is conditional.
Recommendation: HTTPS/default-port policy; disable redirects or validate every hop and destination address; reject private/loopback destinations; stream with strict byte limits. Apply bounded responses to metadata fetches too.

### 7. Medium defense-in-depth gap — Active service lacks documented containment
Read-only systemctl inspection of the working user service showed NoNewPrivileges=no, ProtectSystem=no, PrivateTmp=no, MemoryMax=infinity, TasksMax=38134, and no CPU quota. It runs in the desktop user's context, not the documented dedicated service account. A separate system-level unit is already failing with 203/EXEC permission denied, as previously reported.
Impact: resource exhaustion or backend compromise has fewer limits and access to the desktop user's permissions. Current binding is 127.0.0.1:8844, reducing direct remote exposure; reverse-proxy reachability was not assessed.
Recommendation: reconcile the duplicate units and apply tested containment/dedicated identity with the owner's approval. No service configuration was changed during this audit.

## Verification and limits

- Full isolated regression suite: 72 passed, 3 deprecation warnings, 29.69 seconds.
- Four additional bounded probes confirmed active-content serving, pre-auth multipart spooling, token survival after password change, and enrollment reopening on corrupted account data.
- Reviewed route authorization, signed file/account tokens, upload/container checks, path handling, frontend dynamic content, Suno network access, updater lifecycle, and live service settings.
- Positive controls remain: admin dependencies on privileged routes; signed expiring cookies; hashed session directories; traversal-resistant fixed audio paths; new M4A/WAV structural bounds; atomic audio saves; normal dynamic metadata inserted with safe DOM APIs. Passing tests do not resolve the findings above.
- No dependency advisory/CVE scan, external penetration test, destructive resource exhaustion, actual updater execution, or complete browser exploit test was performed. Findings about redirects and installer concurrency are source/deployment analysis, not a live exploit claim.
- Report is a point-in-time assessment, not a security certification. Address the high-severity findings before exposing the service or trusting audio from untrusted sources.

## Recommended continuation order

1. Read this handoff and project instructions; check current Git status and ask the owner which work to undertake. Do not infer that this audit authorizes every remediation or a remote push.
2. Prioritize embedded artwork active-content protection and pre-auth request-body limits, with adversarial regression tests.
3. Implement fail-closed account-store behavior and deliberate token revocation, preserving accounts/secrets and explaining any planned session invalidation.
4. Address updater concurrency/cancellation under the project's approval checkpoint. Always mock actual installer execution during automated tests.
5. Harden Suno outbound fetches and resource budgeting; reconcile service containment separately with the owner.
6. Investigate the actual M4A sample if the owner requests it, preserving the boundaries listed above.
7. Re-run relevant tests plus the full suite, update required docs honestly, commit locally, and do not push without explicit approval.

## Temporary supporting artifacts

These are convenience artifacts in `/tmp`, not tracked or guaranteed to survive reboot. The full audit has been embedded above so continuation does not depend on them.

- `/tmp/MP3MetaFix-security-audit-2026-09-20.md`: original audit report.
- `/tmp/mp3metafix-security-probes.py`: isolated probes for password-token survival, pre-auth multipart writes, and corrupted account-store enrollment.
- `/tmp/mp3metafix-artwork-probe.py`: synthetic MP3 APIC HTML serving probe; no real user audio.
- `/tmp/mp3metafix-ui-check.cjs`: Playwright format roundtrip check; uses isolated server 18845 and fresh setup, so it is not directly reusable against the configured live server.
- `/tmp/mp3metafix-{mp3,m4a,wav}-mobile.png`: browser screenshots.
- `/tmp/format-tone.{mp3,m4a,wav}`: synthetic browser-test audio.

Do not upload real account data, session cookies, keys, or user audio into reports or commits. No secrets are needed to continue from this handoff.

## Subsequent documentation refresh

After this handoff was created, the owner asked that all project documentation be brought up to date. README, roadmap, architecture, deployment, security guide, workflow, changelog, and Suno integration notes were reviewed/corrected. The audit is now permanently available at [docs/SECURITY_AUDIT_2026-09-20.md](docs/SECURITY_AUDIT_2026-09-20.md). Unsupported security/compliance claims and claims of completed manager UI were corrected; the M4A failure and all seven audit findings remain unresolved. This was documentation-only work, committed locally with no remote push. Historical audit text above still describes the original audit scope and observations.
