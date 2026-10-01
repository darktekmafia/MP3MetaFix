# MP3MetaFix Project Vision & Long-Term Direction

This document establishes the permanent foundational commitment, core engineering principles, current purpose, long-term direction, and vision evolution process for **MP3MetaFix**.

---

## 1. Permanent Foundational Commitment

The following principle is the non-negotiable foundational governance commitment established at project inception. Future governance processes, priorities, and community decisions operate within this boundary:

- **Permanent Open-Source Commitment**:
  - MP3MetaFix is and will permanently remain an open-source project.
  - The software is developed for the benefit of its community with a community-oriented, non-commercial intent. *(Note: This describes the project's purpose and ethos; it is not a formal legal claim of incorporated nonprofit entity status.)*
  - Portions of the codebase may be used by or incorporated into commercial or for-profit projects where permitted by the project's open-source license.
  - Commercial organizations may participate in or financially support development, but commercial use, sponsorship, or corporate affiliation will never confer governance authority or ownership over MP3MetaFix.

---

## 2. Core Product & Engineering Principles

The following core principles guide product design and technical execution:

1. **Non-Destructive Audio Integrity**:
   - Tagging, cover art injection, and metadata modifications must operate on audio containers without causing unintended audio degradation or accidental loss of original track data.
   - User metadata and tags must never be silently or destructively overwritten without explicit user choice and interactive confirmation.
   - This principle protects against unintended corruption and data loss; it does not prohibit intentional, user-requested transformations such as optional format transcoding or export utilities when explicitly chosen.

2. **Security by Design & Safe Deployment**:
   - Robust, platform-appropriate security boundaries, request limits, rate limiting, and safe DOM construction remain standard engineering requirements across all server and client interfaces.

---

## 3. Current Project Purpose

Today, MP3MetaFix exists to provide a high-performance, self-hosted, touch-friendly, and security-focused metadata editor and container tagger for **MP3, M4A (AAC/ALAC/Opus), and WAV** audio formats on Linux workstations, servers, and Proxmox LXC containers.

Its current capabilities include:
- **Mobile-First Track Tagging (`/app`)**: Rapid single-track metadata editing with live retina waveforms, APIC album art studio, and comment presets.
- **Selective AI Metadata Ingestion**: Non-destructive, field-by-field extraction and staging from Suno.com URLs and embedded creation tags with explicit merge previews.
- **Power-User Desktop Shell (`/manager`)**: In-progress multi-panel workspace for batch operations, directory tree ingestion, and structured tag management.
- **Administrative Control Center (`/admin`)**: Diagnostics, resource telemetry, security policy toggles, and authorized update management.
- **In-App Documentation Portal (`/docs`)**: Synchronized, searchable architecture guides, workspace manuals, and API references.

---

## 4. Initial Formal Vision (Founder Stewardship Phase)

During the current founder-stewarded phase of development, the founder will establish and publish the initial formal vision for MP3MetaFix.

The purpose of establishing this initial formal vision is to:
- Define a clear, coherent scope and identity while the software is actively being built.
- Establish architectural boundaries to prevent uncontrolled feature creep and maintain focus on MP3MetaFix's adopted project scope.
- Ensure that core stability, usability, and bug remediation remain prioritized alongside new capabilities.
- Provide a stable foundation that the community can later review, refine, and evolve under formal governance.

---

## 5. Long-Term Architectural Direction (API-Centered Foundation)

As MP3MetaFix matures, its architecture is evolving toward an **API-centered foundation**:

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                      MP3MetaFix Core Engine & API                          │
 │       (Metadata Parsing, APIC Studio, Storage Manager, Quota Enforcement)   │
 └──────┬───────────────────┬───────────────────┬───────────────────┬──────────┘
        │                   │                   │                   │
 ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────────┐
 │ Mobile Web  │     │ Batch Power │     │  Projects   │     │  External / DAW │
 │  (/app)     │     │  (/manager) │     │ (/projects) │     │ Clients/Plugins │
 └─────────────┘     └─────────────┘     └─────────────┘     └─────────────────┘
```

1. **Decoupled Backend Capabilities**:
   - The backend server and documented REST/WebSocket APIs house the core capabilities: container parsing, ID3/MP4/RIFF frame manipulation, image normalization, audio waveform extraction, and session storage.
2. **Interfaces as Clients**:
   - Official web interfaces (`/app`, `/manager`, `/projects`, `/admin`) are clients built on top of the shared engine.
   - Long-term, the documented API is intended to stand independently so external desktop utilities, mobile apps, Digital Audio Workstation (DAW) integrations, command-line tools, and community plugins can interact with MP3MetaFix capabilities directly.
3. **Evolutionary Approach**:
   - This architectural direction guides new development and refactoring without requiring disruptive, premature rewrites of the current application.

---

## 6. Stability & Workflow Preservation

Future vision and development must balance the introduction of new features against the preservation of existing functionality:
- **Zero Casual Churn**: Established workflows and features that community members rely upon must not be casually removed or changed without deliberate consideration of real-world use.
- **Bug & Debt Remediation**: Resolving existing bugs, performance bottlenecks, and security hardening takes priority over unconstrained feature expansion.

---

## 7. Vision Evolution & Community Governance

While the initial vision is defined during founder stewardship to guide project establishment, the long-term governance of MP3MetaFix belongs to its community:

- Once formal community governance is established (as outlined in [GOVERNANCE.md](GOVERNANCE.md)), the community will possess defined authority to review, amend, and evolve the project's vision.
- Vision amendments will occur through a deliberate, structured governance process rather than allowing project identity to drift ad-hoc through individual pull requests or feature debates.
- The precise governance mechanics for vision review, amendment procedures, voting thresholds, and review cadences remain **To Be Determined (TBD)** as part of future community governance specification.
