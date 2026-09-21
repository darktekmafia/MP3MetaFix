# MP3MetaFix security audit — 2026-09-20

Reviewed local commit da83731 (v0.5.0). Audit only: no application edits, installer executions, credential changes, or remote pushes. Audio-format compatibility debugging was outside the audit scope.

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
Recommendation: cross-process exclusive locking covering the full installer lifetime, defined disconnect/cancellation recovery, and log/error sanitization; tests with mocked subprocesses and multiple workers. No actual installer or update endpoint was invoked. Modifying this privileged flow requires the project's explicit maintainer approval checkpoint.

### 6. Medium — Suno artwork allowlist does not constrain redirects
References: backend/main.py:725–737; backend/suno_extractor.py:189–205.
Only the initial artwork hostname is checked, then urllib's default redirect handling follows redirects. Scheme and port are not constrained, and the full response is read before the byte-size check.
Impact: an allowlisted endpoint redirecting to another destination could bypass the intended outbound-host boundary. No exploitable redirect on Suno was demonstrated, so SSRF exploitability is conditional.
Recommendation: HTTPS/default-port policy; disable redirects or validate every hop and destination address; reject private/loopback destinations; stream with strict byte limits. Apply bounded responses to metadata fetches too.

### 7. Medium defense-in-depth gap — Active service lacks documented containment
Read-only systemctl inspection of the working user service showed NoNewPrivileges=no, ProtectSystem=no, PrivateTmp=no, MemoryMax=infinity, TasksMax=38134, and no CPU quota. It runs in the desktop user's context, not the documented dedicated service account. A separate system-level unit is already failing with 203/EXEC permission denied, as previously reported.
Impact: resource exhaustion or backend compromise has fewer limits and access to the desktop user's permissions. Current binding is 127.0.0.1:8844, reducing direct remote exposure; reverse-proxy reachability was not assessed.
Recommendation: reconcile the duplicate units and apply tested containment/dedicated identity with explicit maintainer approval. No service configuration was changed during this audit.

## Verification and limits

- Full isolated regression suite: 72 passed, 3 deprecation warnings, 29.69 seconds.
- Four additional bounded probes confirmed active-content serving, pre-auth multipart spooling, token survival after password change, and enrollment reopening on corrupted account data.
- Reviewed route authorization, signed file/account tokens, upload/container checks, path handling, frontend dynamic content, Suno network access, updater lifecycle, and live service settings.
- Positive controls remain: admin dependencies on privileged routes; signed expiring cookies; hashed session directories; traversal-resistant fixed audio paths; new M4A/WAV structural bounds; atomic audio saves; normal dynamic metadata inserted with safe DOM APIs. Passing tests do not resolve the findings above.
- No dependency advisory/CVE scan, external penetration test, destructive resource exhaustion, actual updater execution, or complete browser exploit test was performed. Findings about redirects and installer concurrency are source/deployment analysis, not a live exploit claim.
- Report is a point-in-time assessment, not a security certification. Address the high-severity findings before exposing the service or trusting audio from untrusted sources.
