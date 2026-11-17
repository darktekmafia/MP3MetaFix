# MP3MetaFix Engineering & Development Rules

These rules govern all agentic modifications, architecture, security, documentation, and Git workflows for the **MP3MetaFix** project.

## Current implementation assessment

These rules are engineering requirements, not proof that the application or installed service already satisfies them. Read the historical [SECURITY_AUDIT_2026-09-20.md](SECURITY_AUDIT_2026-09-20.md), current [remediation verification](SECURITY_REMEDIATION_2026-09-21.md), and [SECURITY_HARDENING.md](SECURITY_HARDENING.md) for implemented protections and remaining deployment tradeoffs. The authoritative workflow is this file. Changes must remain in local Git until explicit approval to push remotely.

---

## 🛡️ 1. Security by Design (Strict Requirement)

The application is currently a single-user, self-hosted utility deployed in multiple environments (workstation, headless server, LXC, and potentially other Linux distributions). It may evolve into a multi-tenant application. Preserve tenant-ready security boundaries now without implementing unnecessary multiuser infrastructure prematurely. Reverse-proxy deployment does not itself provide application authorization. Security must never be compromised:

1. **Zero Raw Identifier Exposure**:
   - Never expose internal session UUIDs or host directory paths in client URLs, JavaScript state, or responses.
   - Existing temporary file-editing sessions must retain cryptographically signed `HttpOnly`, `SameSite=Lax` HMAC-SHA256 cookies with embedded issuance timestamps (`{uuid}.{timestamp}.{signature}`). These cookies identify editing sessions; they must **not** be treated as user authentication. Account authentication (`mp3metafix_auth`) uses a distinct authenticated session, with `Secure` cookies on HTTPS, server-side authorization, session expiration, and credential/epoch-based revocation. Do not replace the existing file-session scheme without an approved migration.
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
5. **Account security:** Use maintained libraries and a strong password KDF (implemented using standard-library `PBKDF2-HMAC-SHA256` with 600,000 iterations and per-user cryptographic salts; `Argon2id` remains a possible future alternative, not an established migration requirement), no shared default passwords, secure cookie handling, login throttling, logout/invalidation, and a deliberate first-run administrator setup. No frontend-held privileged secrets.
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

## 🧭 6. Self-Contained Workspace Section Architecture (Zero Workflow Fragmentation)

The `/app`, `/manager`, and `/projects` sections are specifically designed for distinct use cases, screen layouts, and user workflows:

1. **Dedicated Section Workflows**: Even when multiple sections share core underlying capabilities (such as the single-track audio metadata editor, cover art manager, waveform player, Suno AI synchronization, or lyrics studio), each section must fully house the features it utilizes directly within its own interface.
2. **Zero Cross-Section Navigation for Core Tasks**: A user must never be forced or redirected to navigate away to a different section (e.g., leaving `/projects` to go to `/manager` or `/app`) to accomplish a task intended for their active workspace. Cross-section redirects break user momentum, fragment production flow, and lower productivity.
3. **Seamless In-Place Execution**: All operations initiated within a section (e.g., editing metadata, updating or saving workspace takes, reviewing waveforms, syncing lyrics) must complete seamlessly in-place, preserving workspace context and session continuity.

---

## 📚 7. Documentation Synchronization & Release Gates

Before any changes are committed or merged:

1. **Incremental Updates during Development (`development` branch)**:
   - Every feature, security patch, bug fix, or dependency update must be documented in [CHANGELOG.md](../CHANGELOG.md) under `## [Unreleased]` following [Keep a Changelog](https://keepachangelog.com/) before committing or releasing, unless the founder explicitly authorizes deferral or omission. During explicitly authorized multi-pass corrective work, intermediate changelog updates may be deferred until the commit-ready milestone only when explicitly authorized. When an unauthorized narrowly scoped task conflicts with a changelog requirement, identify the conflict and request clarification rather than silently expanding scope or omitting documentation.
   - Keep [README.md](../README.md), [ROADMAP.md](../ROADMAP.md), [docs/ARCHITECTURE.md](ARCHITECTURE.md), [docs/DEPLOYMENT.md](DEPLOYMENT.md), and [docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md) current with any newly implemented behaviors, architecture changes, and installer options. Routine documentation synchronization does not automatically authorize modifying [VISION.md](../VISION.md), [GOVERNANCE.md](../GOVERNANCE.md), `AGENTS.md`, or `docs/development_workflow.md`. Factual corrections to these documents may be performed when explicitly authorized, but such approval does not imply permission to alter project vision, governance, architectural commitments, or development policy beyond the approved scope.
2. **Public vs. Internal Documentation Boundary**:
   - Treat documentation on every remote branch as public. Document installation, use, architecture, supported behavior, product plans, and relevant security/compatibility limitations. Exclude personal learning plans, environment-specific work logs, and agent/session handoffs.
   - Keep [ROADMAP.md](../ROADMAP.md) focused on public product work.
   - Keep internal planning and personal notes strictly in the ignored `internal/` directory (`internal/README.md`). Never force-add or link untracked internal notes from public documentation.
3. **Mandatory Pre-Merge Release Gate (Before Merging `development` → `main`)**:
   - When `development` is approved for a new release, the documentation MUST be finalized on `development` before merging into `main`:
     - Convert `## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md) to the release heading: `## [X.Y.Z] - YYYY-MM-DD`.
     - Synchronize version strings across [VERSION](../VERSION), [backend/config.py](../backend/config.py), [frontend/index.html](../frontend/index.html), [install.sh](../install.sh), and all <script> and <link> asset tags (?v=X.Y.Z) across frontend/**/*.html.
     - Audit all public docs to verify they accurately reflect the release-ready product state.
     - Execute the test suite (`./.venv/bin/pytest`) with 0 failures.
     - Commit the release preparation on `development` (`chore(release): prepare vX.Y.Z`).
     - Only then execute the fast-forward merge into `main` and tag the release (`git tag vX.Y.Z`).

---

## 🔍 7A. Risk-Based Pre-Implementation & Verification Workflow

**Analyze beyond scope; implement within authorized scope.** **High-risk changes require approved plans before implementation.**

1. **Request Interpretation & Risk Classification**:
   - **Low-Risk**: Copy/documentation edits, isolated styling/layout tweaks, or pure helper fixes with no state, storage, auth, or API contract impact. Keep analysis lightweight while still checking obvious downstream dependencies.
   - **Medium-Risk**: Changes touching API contracts, shared helpers, frontend/backend workflow wiring, metadata/tag handling, or non-destructive persistent state.
   - **High-Risk**: Changes touching persistent audio/artwork/manifest storage, locking, revision/finalization boundaries, destructive/replacement operations, legacy data compatibility, auth/CSRF, installers, or privileged operations (Section 3).

2. **Pre-Implementation Impact Report (Required for Medium and High)**:
   - Before editing code, produce a concise impact report covering:
     - Relevant callers, callees, shared helpers, and sibling paths (e.g., list vs. detail vs. mutation routes, backend vs. frontend callers).
     - Data ownership (owned copies vs. referenced source artifacts) and established invariants (revisions, finalized immutability, legacy compatibility).
     - Concurrency, partial-failure, interruption, and rollback/cleanup boundaries (`NOT_PUBLISHED` vs. `UNCERTAIN` vs. `PUBLISHED`).

3. **Approval Gate (Required for High-Risk & Scope/Invariant Conflicts)**:
   - **High-risk changes require explicit founder approval of the implementation plan before implementation begins**, even when no scope conflict is discovered. The implementation plan must identify intended changes, affected files and workflows, established invariants and data-preservation requirements, failure/recovery considerations, and proposed verification. Existing mandatory checkpoints (Section 3) remain in effect.
   - For any risk level, if analysis reveals an invariant violation or shows that safe implementation requires modifying files or behaviors outside the authorized scope, **stop and request clarification or scope expansion** before editing.

4. **Scoped Implementation**:
   - Implement only within the authorized scope. Reuse shared helpers across sibling paths rather than duplicating logic that can diverge.

5. **Risk-Appropriate Verification**:
   - Risk-appropriate verification supplements, rather than replaces, the existing requirement in Stage 2 (Section 8) to run `./.venv/bin/pytest` successfully before committing.
   - **Low-Risk**: Run targeted syntax/lint checks and relevant unit tests.
   - **Medium-Risk**: Verify affected downstream/sibling workflows, negative/rejection cases, and relevant unit/API tests.
   - **High-Risk**: Verify affected workflows end-to-end, negative/rejection cases, legacy compatibility, concurrency/locking, and relevant failure-injection/cleanup paths, alongside the full `./.venv/bin/pytest` suite before commit.

6. **Developer Self-Audit (Before Declaring Ready)**:
   - Review the diff against the pre-implementation impact report: check for descriptor leaks, unverified cleanup, `GET`/read side effects, missing precondition checks, and unintended edits outside scope.

7. **Accurate Reporting**:
   - Report exact commands executed and observed results, static inspection scope, remaining assumptions, and any untested or unverified conditions.

---

## 🔄 8. Staged Git & Release Workflow (Never Blind Push)

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

### Stage 3: Local Commit on `development` (Local Commits Do Not Authorize Pushes)
- Commit locally on the `development` branch with a clear conventional commit message (`feat:`, `fix:`, `docs:`, `chore:`). Stage only task-authorized changes and preserve unrelated working-tree modifications; do not use indiscriminate staging commands (`git add .`, `git add -A`, `git commit -a`) when unrelated changes exist. Normal development work must never be committed or pushed directly to `main`.
- Local commits do not authorize remote pushes. **DO NOT** push to remote GitHub until local tests pass, verification is completely green, and the user has explicitly authorized the push.
- Before an authorized push, verify the current branch, review the commits being pushed, and confirm that no unintended changes are included. Any remaining uncommitted or untracked files must be identified and left untouched unless separately authorized.

### Stage 4: Authorized `development` Push & Separate `main` Release Gate
- **Authorized `development` Push**: `development` is the active development and pre-release testing branch, allowing interested users to install and test upcoming features (without the stability guarantee of `main`). Remote `development` (`git push origin development`) is normally pushed at the end of a development session following verification and explicit user authorization, or earlier when explicitly authorized by the user.
- **Strict `main` Release Gate**: `main` represents the latest stable release. Merging `development` into `main`, creating release tags, or deploying a stable release requires separate explicit user approval. Authorization to push `development` does not authorize merging or releasing to `main`. This can only take place after the user's own independent testing and security audits confirm release readiness.
- **Authorization Scope & Expiry**: Authorizations apply only to the explicitly approved task and permitted operations. They remain valid throughout that task, including authorized delegation and continuation, but do not extend to unrelated tasks, expanded scope, or additional Git and release operations. An authorization expires when its task is completed, canceled, or superseded.
- Apply the **Pre-Merge Release Gate** (Section 7) on `development` prior to executing any approved merge to `main`.
- Keep `origin main` as the installer default release source. The installer enforces that standard updates are run only on a `main` checkout, fast-forwards strictly from `origin main`, and halts on fetch or branch errors without falling back to generic `git pull`. Development testing checkouts use `install.sh --update --dev`.
- Validate installer and update changes in the designated disposable LXC before recommending wider deployment. Check service identity, listening ports, proxy behavior, data preservation, and recovery after failure.
- Preserve user files, configuration, authentication secrets, and existing data unless an approved migration explicitly changes them.
- Report the files changed, security risk addressed, exact tests run and results, remaining assumptions, and deployment/restart/rollback impact.

---

## 9. Agent Rules Synchronization

Treat this document as the detailed project engineering and deployment source of truth. When asked to create or refresh Antigravity conversation/workspace rules (including `AGENTS.md` if supported), summarize and reference this document without contradicting or silently dropping its specific requirements. Verify the resulting rules in conversation settings; do not assume automatic synchronization.
