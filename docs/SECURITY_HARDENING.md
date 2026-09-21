# MP3MetaFix security controls and open findings

Last reviewed: 2026-09-20, application v0.5.0 / implementation commit `da83731`.

The [security audit](SECURITY_AUDIT_2026-09-20.md) is the current assessment. **Its seven findings remain unresolved.** Earlier versions of this guide marked broad defenses complete; that overstated both implementation and deployed protection. This document distinguishes existing controls from missing guarantees.

## Open findings

| Priority | Finding | Required follow-up |
|---|---|---|
| High | MP3/WAV embedded APIC MIME and bytes can be served as same-origin HTML | Validate/re-encode allowlisted raster artwork before preview/serving; reject active content and enforce bounds |
| High for exposed deployments | Multipart files spool before authentication and endpoint size/rate checks | Bound actual request bytes before parsing; test chunked bodies and dishonest/missing Content-Length |
| High impact, conditional | Unreadable/malformed account store reopens administrator enrollment | Fail closed; distinguish first installation from corruption; serialize setup |
| Medium | Password changes/logout do not revoke issued tokens | Add account/session revocation state and explicit recovery behavior |
| Medium | Updater mutex is process-local; cancellation/log sanitization incomplete | Cross-process lifetime locking, interruption recovery, sanitized output; owner approval before modification |
| Medium | Suno initial-host check does not constrain redirects; reads unbounded | Validate each destination/scheme/port and stream with byte limits |
| Medium | Active workstation user service lacks documented containment | Reconcile duplicate units and test dedicated identity/resource restrictions with owner approval |

See the audit for precise prerequisites, code references, bounded probes, and limits. No public exploit against Suno or remote corruption of the account database was demonstrated.

## Existing controls and their limits

### Identity and authorization

- Account cookies (`mp3metafix_auth`) and temporary file-session cookies (`mp3metafix_session`) serve distinct purposes. Both use timestamped HMAC signatures, HttpOnly, and SameSite=Lax; Secure is set when the application sees HTTPS.
- File cookies do not grant administrator privilege. Editing routes use access-policy dependencies; telemetry and update routes require administrators. Guest Mode allows anonymous editing, not admin operations.
- Static UI/assets, health/version, and authentication entry points are public. UI login prompts are not backend authorization. Settings reads require authentication; writes require admin privilege.
- Passwords use PBKDF2-HMAC-SHA256 with 600,000 rounds and a random salt. Account tokens expire after seven days but currently survive password changes. Logout clears the cookie without server-side revocation.
- Login and upload rate limits are in-memory and per worker. Upload checks happen after multipart parsing. They are not a complete denial-of-service defense.

### Files and request processing

- File-session UUIDs are verified and converted to hashed storage directory names. Session directories request 0700 permissions; account JSON files request 0600 inside a 0700 directory.
- Audio lookups use fixed allowlisted basenames; filename sanitization removes traversal/control characters and preserves the actual extension.
- Endpoint audio limits default to 150 MiB; uploaded artwork defaults to 10 MiB. These do not cap pre-endpoint multipart spooling. Artwork and Suno response reads also need earlier bounds.
- The 2 GiB session-storage setting drives a quota precheck and LRU cleanup, not a cross-worker disk reservation. The current upload check reserves only 10 MiB. Atomic saves need extra disk headroom. TTL cleanup runs periodically.
- Quota/TTL values saved in the settings UI do not currently reconfigure runtime enforcement, which uses environment-derived values.
- Pydantic field bounds limit accepted metadata values, not the total HTTP body before JSON parsing.
- Atomic metadata writes protect the previous file on write failure; concurrent operations still need deliberate resource budgeting.

### Audio and images

- MP3/M4A/WAV extensions are allowlisted and checked against the initial 8 KiB before writing to session storage (after framework multipart spooling).
- M4A/WAV undergo bounded container traversal and Mutagen parsing. M4A handlers target AAC, ALAC, and Opus audio-only files. WAV requires valid RIFF lengths and format/data chunks; INFO input/output is capped at 1 MiB.
- Stream/download MIME is selected by audio format. Downloads use attachment filenames. Byte-range requests reject invalid/unsupported ranges.
- Separately uploaded artwork passes magic checks, Pillow verification/re-encoding, and dimension checks (4096px per side). `Image.MAX_IMAGE_PIXELS=10_000_000` configures Pillow's warning/error thresholds; it is not an explicit hard rejection at exactly ten million pixels.
- **Embedded artwork does not pass that normalization pipeline.** Untrusted APIC MIME and bytes are returned directly; this is the high-severity active-content finding. M4A cover extraction also needs consistent size/image validation.

### Browser, proxy, and outbound boundaries

- Dynamic metadata/toast text uses safe DOM construction/textContent. Remaining inspected innerHTML assignments use static markup or empty strings.
- CSRF middleware checks Fetch-Site and Origin on mutating methods. SameSite cookies provide additional protection. Localhost development exceptions remain in Origin handling.
- CSP, nosniff, and SAMEORIGIN headers exist, but CSP permits inline scripts. These headers do not neutralize explicitly served HTML artwork. The backend does not emit HSTS; configure HTTPS/HSTS at the proxy as appropriate.
- Forwarded headers are resolved only when proxy trust is enabled and the peer matches configured proxies. Default binding is loopback. Reverse-proxy reachability was not audited.
- Suno fetches start from constructed song URLs or allowlisted artwork hosts, but default redirects and whole-response reads weaken confinement and bounds.

### Updates and deployment

- `/api/updates/apply` is enabled and admin/CSRF protected. Its asyncio lock is worker-local; the observed service runs two workers. Disconnect handling does not reliably retain ownership of the installer lifetime.
- SSE output and exception strings are not fully sanitized. Normal editing/image error paths mask details, but that is not a universal endpoint guarantee.
- Service templates contain restrictions; installed units may differ. The observed working user unit had NoNewPrivileges=no, ProtectSystem=no, PrivateTmp=no, and no memory/CPU quota. The duplicate system unit was failing 203/EXEC.
- Service migration/access scripts use scoped parsing, validated inputs, and atomic unit writes. Preserving an existing unit does not add missing containment automatically.

## Verification

The final isolated regression run passed **72 tests**, with three deprecation warnings. Four additional bounded probes confirmed HTML artwork serving, pre-auth multipart spooling, token survival after password change, and setup reopening on corrupted temporary account data. These probes used temporary data, not live accounts.

No CVE/dependency advisory scan, destructive stress test, external penetration test, live updater execution, or complete browser exploit test was performed. See [the audit](SECURITY_AUDIT_2026-09-20.md) and [handoff](../SESSION_HANDOFF_2026-09-20.md). Fixes must add regression coverage and follow [development_workflow.md](development_workflow.md), including local-only commits until explicit remote approval.
