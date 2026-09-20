# Suno integration scope and policy-review status

Implementation reviewed: 2026-09-20, MP3MetaFix v0.5.0.

**Policy/legal compliance has not been verified in this session.** This file documents observed implementation and review requirements; it is not a compliance certification. Earlier versions asserted compliance without substantiating current Suno terms. Maintainers should review the [published Suno terms](https://suno.com/terms) before making such claims or expanding the integration. The terms were not revalidated during the documentation update.

## Current implementation

- The integration extracts a UUID-shaped song identifier and constructs `https://suno.com/song/{uuid}`. The regex shape is not a full UUID version/variant validation.
- Public song-page HTML/Next.js stream content is parsed for title, creator, style, lyrics, artwork URL, date, and attribution metadata.
- It does not implement audio generation, billing/credits, Suno login, private account access, or catalog crawling. It does not request Suno passwords or account tokens; MP3MetaFix's own login is separate.
- The editor detects Suno identifiers in loaded audio comments and provides an extraction workflow. Proposed changes appear in a per-field comparison with Apply Selected, Fill Blank Only, and Apply All choices. Attribution is proposed metadata under user control, not enforced preservation.
- Song and artwork requests use a 10-second timeout. There is no dedicated Suno request-rate limiter in the current endpoints. Blocking fetches run within async handlers, so a timeout does not establish concurrency/resource safety.

## Network and image boundaries

Artwork initially permits `cdn1.suno.ai`, `cdn2.suno.ai`, `cdn-o.suno.com`, `images.suno.ai`, and their subdomains. The initial-host check is not complete SSRF confinement: urllib follows redirects without validating every new destination, scheme/port are not constrained, and responses are read in full before artwork-size validation. Song HTML is also read in full.

Downloaded artwork is subsequently normalized with Pillow, configured with a 10-million-pixel warning/error threshold and 4096px dimension checks. These checks do not limit bytes already downloaded. Embedded audio artwork follows a different, currently unsafe path.

See [security audit findings 1, 2, and 6](SECURITY_AUDIT_2026-09-20.md). No exploitable Suno redirect was demonstrated; the report distinguishes the code gap from a verified external exploit.

## Required review before expansion

1. Verify applicable current terms and obtain any permissions needed for the intended use; public accessibility alone does not establish permission for automated extraction.
2. Preserve per-field selection and non-destructive merging. Do not silently overwrite metadata or artwork.
3. Do not introduce Suno credential collection, generation/billing bypasses, or catalog crawling as an incidental extension of tagging.
4. Add bounded response reads, redirect/destination validation, and appropriate request/concurrency limits.
5. Treat private page structures as compatibility-sensitive; maintain parsing tests and clearly distinguish extraction failures from audio-container failures.

The owner's unresolved M4A parse failure concerns the local audio upload/metadata engine. No evidence currently connects it to Suno page extraction or policy restrictions.
