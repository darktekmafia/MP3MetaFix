# Suno integration scope and policy-review status

Implementation reviewed: 2026-09-21, MP3MetaFix v0.5.1.

**Policy/legal compliance has not been independently verified.** This file documents observed implementation and review requirements; it is not a compliance certification. Earlier versions asserted compliance without substantiating current Suno terms. Maintainers should review the [published Suno terms](https://suno.com/terms) before making such claims or expanding the integration. The terms were not revalidated during the documentation update.

## Current implementation

- The integration extracts a UUID-shaped song identifier and constructs `https://suno.com/song/{uuid}`. The regex shape is not a full UUID version/variant validation.
- Public song-page HTML/Next.js stream content is parsed for title, creator, style, lyrics, artwork URL, date, and attribution metadata.
- It does not implement audio generation, billing/credits, Suno login, private account access, or catalog crawling. It does not request Suno passwords or account tokens; MP3MetaFix's own login is separate.
- The editor detects Suno identifiers in loaded audio comments and provides an extraction workflow. Proposed changes appear in a per-field comparison with Apply Selected, Fill Blank Only, and Apply All choices. Attribution is proposed metadata under user control, not enforced preservation.
- Song and artwork requests use a 10-second timeout. There is no dedicated Suno request-rate limiter in the current endpoints. Blocking fetches run in worker threads. OS DNS resolver timing remains host-controlled; timeouts do not establish complete concurrency/resource safety.

## Network and image boundaries

Artwork permits `cdn1.suno.ai`, `cdn2.suno.ai`, `cdn-o.suno.com`, `images.suno.ai`, and their subdomains. The shared outbound helper requires HTTPS on port 443, rejects redirects and compressed responses, validates every resolved address as public, and connects to a validated address with TLS hostname verification. HTML reads stop at 4 MiB; artwork uses the configured image byte limit.

Downloaded and embedded artwork is normalized to safe raster output, enforcing a 10-million-pixel maximum, 4096px dimensions, and encoded-output size bounds. Invalid embedded covers are omitted from previews without silently deleting their original tags.

See [security remediation verification](SECURITY_REMEDIATION_2026-09-21.md). Tests use controlled responses; live Suno compatibility was not retested during this remediation. Pages requiring redirects or compression are intentionally rejected.

## Required review before expansion

1. Verify applicable current terms and obtain any permissions needed for the intended use; public accessibility alone does not establish permission for automated extraction.
2. Preserve per-field selection and non-destructive merging. Do not silently overwrite metadata or artwork.
3. Do not introduce Suno credential collection, generation/billing bypasses, or catalog crawling as an incidental extension of tagging.
4. Preserve bounded reads and destination confinement; assess dedicated Suno request/concurrency limits before expanding usage.
5. Treat private page structures as compatibility-sensitive; maintain parsing tests and clearly distinguish extraction failures from audio-container failures.

The reported M4A upload HTTP 422 failure was resolved by accepting Opus-in-MP4 in the local codec allowlist. It was an audio-container compatibility issue, separate from Suno page extraction and policy review.
