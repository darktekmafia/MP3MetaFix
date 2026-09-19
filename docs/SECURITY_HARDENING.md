# MP3MetaFix Security Hardening & Threat Defense Guide

This document logs the threat model, attack surface analysis, vulnerability vectors, and defense implementations for MP3MetaFix.

---

## 🛡️ Threat Model & Defense Checklist

- [x] **1. DOM XSS Sanitization in Frontend**: Prevent HTML/script injection via untrusted metadata/tags/filenames in UI notifications. *(Implemented in `frontend/js/app.js`)*
- [x] **2. Image Decompression Bomb Defense**: Protect Python Pillow image processing against memory exhaustion and pixel flood attacks. *(Implemented in `backend/security.py`)*
- [x] **3. Global Storage Quota & Temp Protection**: Prevent disk exhaustion DoS attacks by capping maximum temporary storage size and auto-pruning. *(Implemented in `backend/storage.py` and `backend/config.py`)*
- [x] **4. Origin & Sec-Fetch-Site CSRF Defense**: Block cross-site request forgery and unauthorized external state-changing requests. *(Implemented in `backend/security.py` & `backend/main.py`)*
- [x] **5. In-Memory Upload Rate Limiting**: Limit rapid-fire upload bursts per client IP. *(Implemented in `backend/security.py` & `backend/main.py`)*
- [x] **6. Systemd Process Resource Sandboxing**: Bound memory, CPU, and process execution in system service units. *(Implemented in `deploy/mp3metafix.service` & `install.sh`)*

---

## 🔍 Detailed Vector Analysis & Implementations

### Vector 1: DOM XSS via Metadata / Toasts [COMPLETED]
- **Threat**: Untrusted metadata strings containing script or image error payloads (`<img src=x onerror=...>`) rendered via `innerHTML`.
- **Defense**: Replaced `innerHTML` with safe DOM node construction and `textContent` for dynamic text nodes in `frontend/js/app.js`.

### Vector 2: Image Decompression Bomb & Memory Exhaustion [COMPLETED]
- **Threat**: Highly compressed images (PNG/WebP) expanding to multi-gigabyte uncompressed bitmaps upon decompression, triggering OOM kernel kills (`SIGKILL`).
- **Defense**: Configured `Image.MAX_IMAGE_PIXELS = 10_000_000` (max 10 MP) and enforced dimension boundary checks (max 4096x4096px) in `backend/security.py`.

### Vector 3: Storage & Inode Exhaustion (Disk DoS) [COMPLETED]
- **Threat**: Rapid-fire uploads of 150MB audio files filling the entire host filesystem partition before TTL cleanup triggers.
- **Defense**: Enforced `MAX_GLOBAL_TEMP_STORAGE_MB = 2048` (2GB default) with automatic LRU session pruning when storage approaches capacity limits in `backend/storage.py`.

### Vector 4: Cross-Site Request Forgery (CSRF) [COMPLETED]
- **Threat**: Malicious third-party web pages issuing forged state-changing API requests (`POST /api/save`, `DELETE /api/session`) using browser session cookies.
- **Defense**: Implemented `CSRFProtectionMiddleware` blocking `Sec-Fetch-Site: cross-site` and untrusted `Origin` headers on mutating HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`).

### Vector 5: Upload Flood Rate Limiting [COMPLETED]
- **Threat**: Automated bots exhausting network bandwidth and worker processing queues.
- **Defense**: Implemented `InMemoryRateLimiter` sliding-window rate limiter (25 uploads / 60 seconds per client IP) on `POST /api/upload`.

### Vector 6: Systemd Process Sandboxing [COMPLETED]
- **Threat**: Memory leaks or CPU exhaustion affecting host system stability.
- **Defense**: Added systemd cgroup limits: `MemoryMax=512M`, `TasksMax=64`, `CPUQuota=80%` in `deploy/mp3metafix.service` and `install.sh`.

