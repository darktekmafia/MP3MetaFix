# Administrator Control Center (`/admin`) — System & Security Guide

**The Administrator Control Center (`/admin`)** provides full administrative management, security policies, guest access controls, telemetry diagnostics, and authorized software updates for MP3MetaFix.

---

## 🔐 Access & Authentication

- **Admin Only**: Accessing `/admin` requires authenticated administrator session credentials (`mp3metafix_auth` cookie).
- **First-Run Setup Wizard**: On initial deployment, if no administrator account exists, the application presents an automated first-run setup wizard to establish the primary admin account with secure PBKDF2-HMAC-SHA256 password hashing (600,000 rounds).
- **Navigation**: Access the Admin Dashboard at any time via the user account dropdown in the top header.

---

## ⚙️ Core Administration Features

### 1. Workspace Access Controls & Guest Mode
- **Guest Mode Toggle**: Enable or disable guest access to the Gateway Hub (`/`) and Single-Track Editor (`/app`).
- **Granular Workspace Gating** *(Planned)*: Individually enable or disable guest and user access for `/app`, `/manager`, and `/projects`.
- **Maintenance Notices & Hub Card Visibility** *(Planned)*: Set custom maintenance/reason messages and configure whether disabled services render on the Hub or remain hidden.

### 2. Dynamic Quick Settings Pinning
- Administrators can designate specific settings to appear in the header **Quick Settings** modal across all app pages.
- Toggleable pins include:
  - `Guest Access Mode`
  - `Suno AI Integration & Auto-Detection`
  - `Maximum Active Sessions`
  - `Global Storage Quotas (MB)`
  - `Session TTL Expiration (Minutes)`
  - `Maximum Upload File Size (MB)`

### 3. AI Services & Metadata Extractors
- **Suno.com Integration Policy**: Enable or disable Suno AI URL parsing, automatic tag detection, and artwork extraction globally. When disabled, backend endpoints return `403 Forbidden` and UI detection badges are hidden.

### 4. System Telemetry & Resource Diagnostics
- **Live Host Diagnostics**: Real-time monitoring for:
  - CPU Utilization & Load Averages
  - Host RAM Allocation & Consumption
  - Disk Storage Availability
  - Temporary Hashed Storage Cache Size & Quotas
  - Process Uptime & System Host Details
  - Active Network Bindings & Reverse Proxy Status

### 5. Authorized In-App Software Updates
- **Isolated Update Discovery**: Check for newer releases securely directly against official release channels.
- **Preflight Guards & Mutexing**: Update processes run serialized with concurrency locks, preventing concurrent execution and ensuring filesystem safety.
- **Clear Status Reporting**: Updaters emit sanitized status output without exposing sensitive system logs to the browser.
- *Note: In read-only or containerized production environments, in-app updates can be disabled via the `MP3METAFIX_ALLOW_WEB_UPDATES=false` environment policy.*

---

*For deployment setup and system service configuration, see the [Production Deployment Guide](../DEPLOYMENT.md).*
