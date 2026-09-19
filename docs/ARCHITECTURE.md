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
[ Upload Validator ]
  ├── 1. Chunk-level Magic Bytes Verification (ID3 / MPEG Frame Sync 0xFF 0xFB/FA/F3/F2)
  ├── 2. Upload Size Quota Enforcement (default: 150MB limit streamed)
  ├── 3. Strict Pillow image verification for artwork (JPEG / PNG / WebP)
  └── 4. Client Filename Sanitization (Replaces path traversals, control characters)
        │
        ▼
[ UUID Isolated Storage ] (/data/temp/{uuid4}/)
        │
        ▼
[ Background TTL Purge ] (Periodic async worker purges sessions > 60 minutes)
```

### Key Security Controls:
1. **Magic Bytes Header Verification**:
   - Files are inspected at the byte level before full persistence. Disguised executable files (e.g. `.exe`, `.elf`, `.php`, `.sh` renamed to `.mp3`) are rejected immediately with `HTTP 400 Bad Request`.
2. **UUID Storage Isolation**:
   - Files are never saved using client-supplied names on the server filesystem. Each upload receives a cryptographically secure `UUID4` storage container (`data/temp/<uuid>/audio.mp3`).
3. **Automated Lifecycle / Temp Pruning**:
   - A background asyncio task executes periodically (every 5 minutes) to prune sessions whose `last_accessed_at` timestamp exceeds the configurable TTL (`MP3METAFIX_SESSION_TTL_MINUTES`, default 60 minutes).
4. **Header Protection**:
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

---

## 4. Audio Streaming & Range Requests

To allow instantaneous scrubbing and preview in web browsers, the `GET /api/stream/{session_id}` endpoint implements **HTTP 206 Partial Content**:
- Parses `Range: bytes=start-end` request headers.
- Streams audio chunks (64 KB) on-demand.
- Supports seeking without downloading the full audio file ahead of time.
