# MP3MetaFix Architecture & Security Specification

This document details the software architecture, metadata processing engine, security controls, and storage lifecycle of MP3MetaFix.

---

## 1. System Overview

MP3MetaFix is built as a lightweight, secure client-server application:
- **Backend**: Python 3.14 + FastAPI + Starlette + Mutagen + Pillow.
- **Frontend**: Zero-framework Vanilla JS + CSS Glassmorphism Design System.
- **IPC / Transport**: RESTful JSON APIs and HTTP 206 Partial Content audio streaming.

---

## 2. Security Perimeter & Threat Model

Because MP3MetaFix accepts arbitrary user uploads and is intended to run exposed behind reverse proxies and domain names, security is enforced across every layer:

```
[ Incoming Request ]
        │
        ▼
[ SecurityHeadersMiddleware ] (CSP, HSTS, X-Content-Type-Options: nosniff, SAMEORIGIN)
        │
        ▼
[ ProxyHeadersMiddleware ] (Handles X-Forwarded-For, X-Forwarded-Proto safely)
        │
        ▼
[ CSRFProtectionMiddleware ] (Blocks cross-site mutating requests & unauthorized origins)
        │
        ▼
[ InMemoryRateLimiter ] (Sliding-window IP rate limiting with trusted-proxy anti-spoofing)
        │
        ▼
[ Timestamped HMAC Cookie Authenticator ] (Validates HttpOnly {uuid}.{timestamp}.{signature})
        │
        ▼
[ Upload & Metadata Validator ]
  ├── 1. Chunk-level Magic Bytes Verification (ID3 / MPEG Frame Sync 0xFF 0xFB/FA/F3/F2)
  ├── 2. Upload Size Quota Enforcement (default: 150MB limit streamed)
  ├── 3. Decompression Bomb Defense (Pillow MAX_IMAGE_PIXELS = 10 MP, 4096x4096px limit)
  ├── 4. Pydantic Payload Length Constraints (Bounded title, artist, lyrics, comments)
  └── 5. Client Filename Sanitization (Replaces path traversals, control characters)
        │
        ▼
[ Decoupled Hashed Storage (POSIX 0700) ] (/data/temp/{SHA256(secret:uuid4)[:32]}/)
        │
        ▼
[ Quota Guard & Background TTL Purge ]
  ├── Global Storage Quota Guard (Auto-LRU pruning when disk storage reaches max quota)
  └── Periodic TTL Worker (Purges expired sessions > 60 minutes)
```

### Key Security Controls:
1. **Zero Raw Identifier Exposure**:
   - Internal session UUIDs and filesystem folder paths are never exposed in browser URLs, JavaScript state, or responses.
   - Authentication is maintained via an `HttpOnly`, `SameSite=Lax` cookie containing an HMAC-SHA256 signed token with embedded issuance timestamp (`session_id.timestamp.signature`).
2. **Decoupled Hashed Filesystem Storage & 0700 Permissions**:
   - Physical storage directories on the host filesystem are named using a one-way deterministic cryptographic hash: `SHA-256(server_secret:session_id)[:32]`.
   - All session directories are created with `0700` (`rwx------`) permissions, isolating temp storage across multi-tenant Linux hosts.
3. **Magic Bytes Header Verification**:
   - Files are inspected at the byte level before full persistence. Disguised executable files (e.g. `.exe`, `.elf`, `.php`, `.sh` renamed to `.mp3`) are rejected immediately with `HTTP 400 Bad Request`.
4. **Automated Lifecycle & Storage Quota LRU**:
   - Dynamic LRU session pruning triggers if total temporary storage exceeds `MAX_GLOBAL_TEMP_STORAGE_MB` (default 2GB).
   - A background asyncio task executes periodically (every 5 minutes) to prune sessions whose `last_accessed_at` timestamp exceeds the configurable TTL (`MP3METAFIX_SESSION_TTL_MINUTES`, default 60 minutes).
5. **CSRF & Origin Isolation**:
   - `CSRFProtectionMiddleware` rejects mutating state requests (`POST`, `PUT`, `DELETE`, `PATCH`) with foreign `Sec-Fetch-Site` or non-whitelisted `Origin` headers.
   - CORS is restricted to exact regex matchers (`localhost`, `127.0.0.1`, and explicit origins).
6. **Exception Masking**:
   - Internal Python exceptions and file paths are masked from HTTP responses to prevent server path leakage, while full diagnostics are recorded to internal server logs.
7. **Header Protection**:
   - `Content-Security-Policy`: Disallows untrusted script execution.
   - `X-Content-Type-Options: nosniff`: Prevents MIME confusion attacks.
   - `X-Frame-Options: SAMEORIGIN`: Protects against clickjacking.

---

## 3. Metadata Engine & Mutagen ID3 Frames

MP3MetaFix maps high-level user fields to standard ID3v2.4 and ID3v2.3 tags:

| High-Level Field | ID3 Frame ID | Encoding | Description |
|-------------------|--------------|----------|-------------|
| Title | `TIT2` | UTF-8 | Track name |
| Artist | `TPE1` | UTF-8 | Lead performer |
| Album | `TALB` | UTF-8 | Album / Collection title |
| Album Artist | `TPE2` | UTF-8 | Band / Album artist |
| Composer | `TCOM` | UTF-8 | Song composer |
| Genre | `TCON` | UTF-8 | Music category / style |
| Year / Date | `TDRC` & `TYER` | UTF-8 | Recording timestamp / year |
| Track Number | `TRCK` | UTF-8 | Formatted as `track/total` |
| Disc Number | `TPOS` | UTF-8 | Formatted as `disc/total` |
| BPM | `TBPM` | UTF-8 | Beats per minute |
| Comment | `COMM` | UTF-8 | General notes |
| Lyrics | `USLT` | UTF-8 | Unsynchronized song lyrics |
| Cover Art | `APIC` | Binary | Embedded front cover image (`type=3`) |

## 4. Audio Streaming & Range Requests

To allow instantaneous scrubbing and preview in web browsers, the `GET /api/stream/{session_id}` endpoint implements **HTTP 206 Partial Content**:
- Parses `Range: bytes=start-end` request headers.
- Streams audio chunks (64 KB) on-demand.
- Supports seeking without downloading the full audio file ahead of time.

---

## 5. Download & File System Operations

- **File System Access API**:
  - In supported Chromium-based browsers, `window.showSaveFilePicker()` is utilized to prompt the user to choose an explicit save location on their local filesystem.
- **Named Path Downloads**:
  - `GET /api/download/{session_id}/{filename}` routes provide direct file path semantics for browsers and download managers.
- **Content-Disposition Encoding**:
  - Employs RFC 5987 parameter encoding (`filename*=UTF-8''...`) for full Unicode fidelity and an ASCII-sanitized `filename` fallback.
