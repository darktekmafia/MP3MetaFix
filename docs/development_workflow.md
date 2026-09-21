# MP3MetaFix Engineering & Development Rules

These rules govern all agentic modifications, architecture, security, documentation, and Git workflows for the **MP3MetaFix** project.

## Current implementation assessment

These rules are engineering requirements, not proof that the application or installed service already satisfies them. Read [SECURITY_AUDIT_2026-09-20.md](SECURITY_AUDIT_2026-09-20.md) and [SECURITY_HARDENING.md](SECURITY_HARDENING.md) for unresolved findings. The authoritative workflow is this file; `.agents/rules/development_workflow.md` is a legacy copy and must not override it. Changes must remain in local Git until explicit approval to push remotely.

---

## 🛡️ 1. Security by Design (Strict Requirement)

The application is currently a single-user, self-hosted utility deployed in multiple environments (workstation, headless server, LXC, and potentially other Linux distributions). It may evolve into a multi-tenant application. Preserve tenant-ready security boundaries now without implementing unnecessary multiuser infrastructure prematurely. Reverse-proxy deployment does not itself provide application authorization. Security must never be compromised:

1. **Zero Raw Identifier Exposure**:
   - Never expose internal session UUIDs or host directory paths in client URLs, JavaScript state, or responses.
   - Existing temporary file-editing sessions must retain cryptographically signed `HttpOnly`, `SameSite=Lax` HMAC-SHA256 cookies with embedded issuance timestamps (`{uuid}.{timestamp}.{signature}`). These cookies identify editing sessions; they must **not** be treated as user authentication. When account authentication is implemented, use a distinct authenticated session, with Secure cookies on HTTPS, server-side authorization, session expiration, and appropriate rotation/revocation. Do not replace the existing file-session scheme without an approved migration.
2. **Filesystem Isolation & POSIX 0700 Permissions**:
   - Existing file-session storage directories on disk must retain one-way deterministic SHA-256 hashes (`SHA-256(secret:uuid)[:32]`). For future multi-tenancy, enforce tenant/user ownership separately; a hashed path alone is not an authorization boundary.
   - Temporary directories and session folders must strictly maintain `0700` (`rwx------`) POSIX permissions.
3. **MIME & Magic Bytes Header Verification**:
   - Inspect files at byte level before persistence. Reject masquerading binaries or malformed headers immediately.
4. **Input Length & Decompression Bounds**:
   - All Pydantic input models must enforce strict field length constraints (`Field(max_length=...)`).
   - Pillow image processing must cap `Image.MAX_IMAGE_PIXELS` (max 10 MP) and enforce dimension bounds (max 4096×4096px).
5. **CSRF, Rate Limiting & Exception Masking**:
   - Enforce `Sec-Fetch-Site` and `Origin` validation on mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`).
   - Maintain in-memory sliding-window upload rate limiting with verified proxy subnet validation. If deployed with multiple workers/instances or multi-tenant traffic, review whether shared, per-user/tenant rate limits and quotas are required; do not assume in-memory limits are globally enforced.
   - Never expose raw Python tracebacks or host filesystem paths in client HTTP responses; log diagnostic details internally without logging secrets, authentication cookies, private metadata, or unnecessary user content.
6. **XSS Prevention in Frontend**:
   - Never use `innerHTML` with untrusted dynamic content (filenames, user tags, lyrics, release notes). Always use safe DOM construction and `textContent`.

---

## 🔐 2. Identity, Tenant Readiness & API Authorization

1. **Present scope versus future scope:** Do not add public signup, teams, role hierarchies, or a tenant database merely to anticipate future features. Preserve a path to multi-tenancy in new data models, storage access, and API design.
2. **Distinct trust boundaries:** A signed file-session cookie, a logged-in account session, an administrator privilege, and a tenant membership are different concepts. Do not let possession of one imply the others. Nginx Proxy Manager authentication is defense in depth, not a substitute for backend checks.
3. **Route-by-route enforcement:** Every endpoint that reads or changes private data must authenticate the caller when application login is enabled and authorize access to the particular file/session/resource. Explicitly document narrowly public endpoints such as minimal health checks. Check authorization on the backend, including requests that bypass the UI or proxy.
4. **Future tenant ownership:** When adding persistent libraries, accounts, or shared resources, define ownership in the data model and enforce it on every query, object lookup, download, and background task. Deny cross-tenant access by default; test with two isolated tenants. Never trust a client-supplied tenant ID without verifying membership and resource ownership.
5. **Account security when implemented:** Use maintained libraries and a modern password hash such as Argon2id, no shared default passwords, secure cookie handling, login throttling, logout/invalidation, and a deliberate first-run administrator setup. No frontend-held privileged secrets.
6. **Browser/API protections:** CORS, Origin, `Sec-Fetch-Site`, and hidden controls do not authenticate callers. Preserve CSRF validation for cookie-authenticated state changes, use restrictive CORS, and validate all input server-side. Preserve safe DOM construction and security headers.

---

## ⚠️ 3. Privileged Operations & Update Installation (Mandatory Checkpoint)

Before implementing or modifying command execution, installers, service management, authentication, network bindings, filesystem access outside approved storage, or secret handling: identify the trust boundary, privileges, affected files, abuse cases, and smallest safe design. Obtain maintainer approval before consequential changes.

- **Do not enable or extend the update-install endpoint until its authentication, authorization, and privilege design are reviewed and tested.** Read-only version checks are separate.
- Update installation requires authenticated administrator authorization and CSRF protection; a file-editing session or proxy login alone is insufficient.
- Do not run a root installer directly from a general-purpose web request handler. Prefer a narrowly scoped, auditable privileged helper/service with only necessary capabilities.
- Do not accept arbitrary commands, shell fragments, script paths, repositories, branches, or update URLs from clients. Restrict update targets to approved sources and verify integrity/authenticity appropriate to the release process.
- Prevent concurrent updates, sanitize streamed/SSE logs, and plan for interrupted installs, recovery, and service restart. Test unauthorized direct API requests and failed-update behavior.
- Never silently add a security bypass or grant broader privileges to make an update work.

---

## 🌐 4. Network, Filesystem & Deployment Boundaries

- Treat uploaded audio, artwork, tags, filenames, and metadata as untrusted. Preserve MIME/magic-byte checks, file-size limits, Pillow bounds, path traversal protections, quotas, and cleanup. Reject symlink escapes and arbitrary host paths; use atomic replacement where practical.
- Run the web application as a dedicated unprivileged service user with only necessary filesystem access. Preserve existing `0700` session-directory requirements. Review systemd hardening and required write paths before changing them.
- Prefer a localhost listener when the reverse proxy shares a network namespace. If Nginx Proxy Manager is in another LXC, restrict the backend listener with deliberate network/firewall rules. Verify actual listening sockets and direct reachability; the reverse proxy does not automatically block backend access.
- Trust forwarded headers only from explicitly configured proxies, which must overwrite client-supplied forwarding headers. Do not use unrestricted production proxy trust such as `trusted_hosts=["*"]`.
- Use HTTPS for exposed access; do not disable TLS verification as a workaround. An unprivileged Proxmox LXC is not a guarantee of network isolation.
- Protect secrets outside Git, frontend assets, and logs; preserve existing secrets and user data during normal updates. Explain and obtain approval before rotating credentials, invalidating sessions, or changing permissions.
- Prefer maintained, necessary dependencies with reproducible constraints. Review major upgrades and never execute unverified remote code or automatically install untrusted plugins.

---

## 🧪 5. Security Verification & Change Control

- Make focused changes; do not combine a security fix with unrelated refactoring or prematurely add Docker, plugin infrastructure, or multiuser features.
- Add relevant positive and negative tests: unauthenticated/unauthorized routes, session tampering and expiration, CSRF, cross-session and (when implemented) cross-tenant access, malformed/oversized uploads, traversal/symlink escapes, resource limits, proxy spoofing, and update authorization/failure/concurrency.
- Do not weaken or delete security tests to obtain a green run. Report exact commands, results, and untested conditions. Passing tests are not proof that the application is vulnerability-free.
- Do not silently change service users, network bindings, permissions, secrets, or deployment infrastructure. Explain impact and obtain approval for disruptive changes.

---

## 📚 6. Documentation Synchronization (Mandatory Before Commit)

Before any changes are committed to local Git:

1. **CHANGELOG.md**:
   - Every feature, security patch, bug fix, or dependency update must be documented in [CHANGELOG.md](../CHANGELOG.md) under the appropriate version section following [Keep a Changelog](https://keepachangelog.com/).
2. **README.md & Docs**:
   - Ensure [README.md](../README.md), [docs/ARCHITECTURE.md](ARCHITECTURE.md), [docs/DEPLOYMENT.md](DEPLOYMENT.md), and [docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md) reflect current behavior, architecture diagrams, and configuration variables.
3. **ROADMAP.md**:
   - Keep [ROADMAP.md](../ROADMAP.md) updated when items are started, completed, or newly proposed.
4. **Version Consistency**:
   - Keep version strings synchronized across [VERSION](../VERSION), [backend/config.py](../backend/config.py), [frontend/index.html](../frontend/index.html), and [install.sh](../install.sh).

---

## 🔄 7. Staged Git & Release Workflow (Never Blind Push)

Follow a strict 4-stage validation pipeline:

### Stage 1: Local Implementation & Self-Review
- Implement changes following architecture and clean code principles.
- Maintain documentation integrity and preserve unrelated existing code/comments.

### Stage 2: Local Test & Verification (Before Any Push)
- Run the full automated test suite:
  ```bash
  ./.venv/bin/pytest
  ```
- All tests must pass with 0 failures before staging Git commits.
- Manually verify UI interactions, error handling, and performance.
- Inspect `git diff` to verify only intended changes are staged.

### Stage 3: Local Commit First, Push Only When Verified
- Commit to local Git with a clear conventional commit message (`feat:`, `fix:`, `docs:`, `chore:`).
- **DO NOT** push to remote GitHub until local tests pass and verification is completely green.
- Verify `git status` is clean before pushing upstream.

### Stage 4: Explicitly Approved Deployment & Update Validation
- Do not automatically push, tag, publish a release, or deploy to any environment without maintainer approval.
- Validate installer and update changes in the designated disposable LXC before recommending wider deployment. Check service identity, listening ports, proxy behavior, data preservation, and recovery after failure.
- Preserve user files, configuration, authentication secrets, and existing data unless an approved migration explicitly changes them.
- Report the files changed, security risk addressed, exact tests run and results, remaining assumptions, and deployment/restart/rollback impact.

---

## 8. Agent Rules Synchronization

Treat this document as the detailed project engineering and deployment source of truth. When asked to create or refresh Antigravity conversation/workspace rules (including `AGENTS.md` if supported), summarize and reference this document without contradicting or silently dropping its specific requirements. Verify the resulting rules in conversation settings; do not assume automatic synchronization.
