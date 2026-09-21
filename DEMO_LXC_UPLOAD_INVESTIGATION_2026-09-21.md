# Demo LXC upload investigation

Status: open; comparison updated 2026-09-21. No application, LXC, or proxy configuration changes were made during the investigation.

## Confirmed comparison

The fresh Ubuntu 24.04 demo and upgraded Ubuntu 24.04 installation both run v0.5.1 commit `11bbbfb`, with clean tracked application source. Inspected backend/frontend files and actual served editor JavaScript match. Python is 3.12.3 on both. Of 31 installed distributions, only watchfiles differed (1.3.0 versus 1.2.0); neither service uses reload mode. Relevant audio/parser dependencies match.

Guest authentication status, configured upload/storage limits, and proxy trust settings match. Both services run as root. The fresh service uses ProtectSystem=strict and ProtectHome=tmpfs; the upgraded service retains ProtectSystem=full and ProtectHome=no. These deployment differences do not explain the observed proxy-layer rejection by themselves.

The two NPM virtual hosts have equivalent directives apart from hostname, backend address, log paths, and access-list file references. Both use Basic Auth with satisfy any and no IP allow rules. No password files were read. Shared proxy settings and log format were inspected.

## Request evidence

- Guest `/api/updates/check` returns application HTTP 401 without a WWW-Authenticate header on both installations.
- NPM's log format records upstream status followed by response status. Demo upload entries with `- - 401` indicate NPM rejection without an upstream response; update-check entries with `- 401 401` identify an upstream application rejection.
- On September 21 at 09:10:48 (-0500), the demo update check returned application 401; at 09:10:52, POST `/api/upload` received proxy 401. The corresponding backend window contains no completed upload request.
- The working installation also receives proxy challenges: at 06:47:05, DELETE `/api/session` returned proxy 401, then 200 at 06:47:06; upload succeeded at 06:47:11.
- The demo likewise recovered: DELETE `/api/session` returned proxy 401 at 07:59:09, succeeded at 07:59:36, and upload succeeded at 07:59:44.
- Nine completed demo backend uploads in the inspected day's logs returned 200. No matching parser failures, tracebacks, OOM evidence, or service restarts were found in that inspection.

An isolated Chrome 153 test with synthetic Basic Auth reproduced an upstream application 401 without WWW-Authenticate clearing cached HTTP authentication: the next request omitted Authorization, received a proxy-style challenge, and retried successfully. This is a demonstrated mechanism, not proof of every live stall's cause. The reason recovery differs between hostnames remains uncertain.

## Excluded conclusions

The manually opened GET `/api/upload` returning 404 was not an upload failure. Uploads use POST. Artwork validation was an initial theory; no validation false positive was demonstrated. Buffering warnings also occurred during successful uploads. Browser progress percentages do not measure bytes accepted by the backend. Logs do not isolate Cloudflare as the cause; observed NPM entries use HTTPS, so an assumed HTTP-versus-HTTPS tunnel difference must not be treated as verified.

## Next validation

Move update discovery exclusively to authenticated `/admin`, retaining installed-version display in public headers and backend authorization. Then compare both hosts in clean browser contexts using the same file and public route. Correlate sanitized browser timings with NPM and backend logs for each actual POST upload, including retries and credential prompts. Verify repeated upload, edit, download, and session restoration. Do not record Authorization headers, cookies, or private file metadata. No fix is yet confirmed.
