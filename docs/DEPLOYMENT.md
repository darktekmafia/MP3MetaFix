# MP3MetaFix Deployment Guide

This guide covers deployment options for MP3MetaFix across **Local Fedora 44 Development**, **Proxmox LXC Containers (Ubuntu/Debian)**, and **Reverse Proxies (Nginx / Caddy)**.

---

## Current workstation status and deployment cautions

As verified after local migration on 2026-09-21, the working backend is the **system** `mp3metafix.service`, serving loopback port 8844 with two workers at v0.5.1 under the dedicated `mp3metafix` Linux account. The old user service is disabled/inactive and retained for recovery.

For this workstation:

```bash
sudo systemctl status mp3metafix.service
sudo systemctl restart mp3metafix.service
journalctl -u mp3metafix.service -f
curl --fail http://127.0.0.1:8844/api/health
```

The service has verified read-only application mounts under `/opt/mp3metafix`, private data under `/var/lib/mp3metafix` (0700), ProtectSystem=strict, ProtectHome=tmpfs, PrivateTmp, NoNewPrivileges, an inaccessible system bus, and 512 MiB/64-task/80% CPU limits. The code mounts refer to the development checkout, which must remain available. Details and rollback are in [account migration](ACCOUNT_MIGRATION.md).

Web installation is disabled on this deployment. Local edits remain visible through the mounts; restart the **system** service to load backend changes. Do not start the retained user service or use its stale checkout data copy. General installer update/access commands are not yet fully integrated with this deployment; follow the local migration maintenance instructions.

Other installations do not acquire these settings or change accounts merely by pulling updated templates. Namespace support must be checked on the target host, particularly LXC. The v0.5.1 authentication upgrade requires signing in again; the later account migration preserves passwords, signing secrets, and application data.

## 1. Local Linux / Workstation Installation (Fedora / Ubuntu / Arch)

### Systemd Background Service (Starts on Boot)
To install MP3MetaFix as a persistent background service that starts on system boot and integrates into your desktop application menu:
```bash
git clone https://github.com/darktekmafia/MP3MetaFix.git
cd MP3MetaFix
sudo ./install.sh
```
This will:
1. Setup system dependencies and Python virtual environment (`.venv`).
2. Install and enable the `mp3metafix.service` systemd unit to start on system boot.
3. Install the application icon and launcher to `~/.local/share/applications/mp3metafix.desktop`.

### Service Management
```bash
sudo systemctl status mp3metafix.service
sudo systemctl restart mp3metafix.service
sudo systemctl stop mp3metafix.service
```

### Ad-hoc Local Development
If developing or modifying code without running a background service:
```bash
./run.sh
```

---

## 2. Proxmox LXC Container Deployment (Headless Ubuntu / Debian)

### Step 1: Create or Enter LXC
Create a lightweight unprivileged Ubuntu 24.04 or Debian 12 LXC container in Proxmox (e.g. 512MB RAM, 1 vCPU, 8GB storage).

### Step 2: Install MP3MetaFix
Inside the LXC container:
```bash
# Clone the repository
git clone https://github.com/darktekmafia/MP3MetaFix.git /opt/mp3metafix
cd /opt/mp3metafix

# Run the installer in headless mode
sudo ./install.sh --headless --port 8844
```

The installer will:
- Install Python 3, pip, venv, and ffmpeg via `apt-get`.
- Prepare the Python virtual environment in `/opt/mp3metafix/.venv`.
- Install and start the systemd service `/etc/systemd/system/mp3metafix.service`.

### Step 3: Enabling LAN Access (Optional)
By default, fresh installations bind to `127.0.0.1` (localhost only) for security. To reach the MP3MetaFix web UI from other machines on your local network:
```bash
# Switch service to listen on all interfaces (0.0.0.0)
sudo ./install.sh --lan

# Verify access URLs and service status
./install.sh --access
```

### Step 4: Managing the Service
```bash
# Check service status
sudo systemctl status mp3metafix.service

# View live logs
journalctl -u mp3metafix.service -f

# Restart service
sudo systemctl restart mp3metafix.service
```

---

## 3. Network Access & Binding Management (`--access`, `--lan`, `--local`, `--bind`)

MP3MetaFix includes built-in maintenance commands to inspect and adjust network binding without manually modifying systemd unit files:

### View Current Binding, Proxy Trust & Network URLs
```bash
./install.sh --access
```
Displays:
- Current configured bind host and port (`MP3METAFIX_HOST` and `MP3METAFIX_PORT`).
- Reverse proxy trust status and configured trusted proxy subnets (`MP3METAFIX_TRUST_PROXIES`, `MP3METAFIX_TRUSTED_PROXIES`).
- Service status (active/inactive) and health check probe status on the configured interface.
- Active access endpoints (Localhost, LAN, or specific bound interface).

### Enable LAN Access (All Interfaces)
```bash
sudo ./install.sh --lan
# (or ./install.sh --bind 0.0.0.0)
```
Safely updates the systemd unit `Environment="MP3METAFIX_HOST=0.0.0.0"`, reloads the systemd daemon, restarts the service, and verifies the `/api/health` probe.

### Restrict to Localhost Only
```bash
sudo ./install.sh --local
# (or ./install.sh --bind 127.0.0.1)
```
Restricts listening to local loopback `127.0.0.1`.

### Custom Host / IP Binding
```bash
sudo ./install.sh --bind 192.168.0.190
```
Binds the service specifically to a dedicated network interface. The health probe and endpoint reporting automatically target `http://192.168.0.190:8844`.

### Reverse Proxy Trust & Domain Configuration (`--proxy`, `--domain`, `--no-proxy`, `--no-domain`)
Configure reverse proxy header processing (`X-Forwarded-For`, `X-Forwarded-Proto`) and public domain URL routing without manually editing unit files:
```bash
# Enable proxy trust with loopback defaults (127.0.0.1, ::1)
sudo ./install.sh --proxy

# Enable proxy trust for a dedicated reverse proxy IP / subnet (e.g. Nginx Proxy Manager)
sudo ./install.sh --proxy 192.168.0.55

# Set public reverse proxy domain/hostname for automated proxy health checks and access links
sudo ./install.sh --domain mp3.yourdomain.com
# (or with full scheme: sudo ./install.sh --domain https://mp3.yourdomain.com)

# Configure proxy IP and domain in a single command
sudo ./install.sh --proxy 192.168.0.55 --domain mp3.yourdomain.com

# Clear configured proxy domain
sudo ./install.sh --no-domain

# Specify multiple trusted proxy CIDR subnets
sudo ./install.sh --trusted-proxies 127.0.0.1,192.168.0.55,10.0.0.0/8

# Disable proxy trust (direct client connections only)
sudo ./install.sh --no-proxy
```

> [!NOTE]
> **Informational Reachability Probing**:
> Setting `--domain <DOMAIN>` is strictly informational. It allows `./install.sh --access` and status checks to display your public access URL and verify that the reverse proxy route to the backend is reachable (`https://${domain}/api/health`). It does **not** bind the server socket or modify network listening addresses (which remain governed independently by `--bind`, `--lan`, or `--local`).

---

## 4. Reverse Proxy Configuration

MP3MetaFix ships with proxy trust **disabled by default** (`MP3METAFIX_TRUST_PROXIES=false`) for secure standalone and local workstation operation. When placing MP3MetaFix behind a reverse proxy (e.g. Nginx, Caddy, Traefik, Apache, HAProxy, or Nginx Proxy Manager), configure proxy trust according to your topology:

---

### Topology 1: Colocated Reverse Proxy (Same Host / Loopback)

When the reverse proxy runs on the same operating system or container as MP3MetaFix:

1. **Server Bind & Trust**:
   - Keep `MP3METAFIX_HOST=127.0.0.1` so MP3MetaFix is not exposed directly to the local network.
   - Set `MP3METAFIX_TRUST_PROXIES=true` in `/etc/systemd/system/mp3metafix.service` (or environment).
   - `MP3METAFIX_TRUSTED_PROXIES` defaults to `127.0.0.1,::1`, securely trusting only loopback forwarded headers.

2. **Nginx Example (`/etc/nginx/sites-available/mp3metafix`)**:
   ```nginx
   server {
       listen 80;
       server_name mp3.yourdomain.com;
       client_max_body_size 150M;

       location / {
           proxy_pass http://127.0.0.1:8844;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

3. **Caddy Example (`/etc/caddy/Caddyfile`)**:
   ```caddy
   mp3.yourdomain.com {
       reverse_proxy 127.0.0.1:8844
       request_body {
           max_size 150MB
       }
   }
   ```

---

### Topology 2: Remote / External Reverse Proxy (Separate Host / Container / VM)

When the reverse proxy (e.g. Nginx Proxy Manager, external load balancer, or gateway) runs on a separate host or container from MP3MetaFix:

1. **Configure Service Bind & Trust**:
   In `/etc/systemd/system/mp3metafix.service` (or environment):
   ```ini
   Environment="MP3METAFIX_HOST=0.0.0.0"
   Environment="MP3METAFIX_PORT=8844"
   Environment="MP3METAFIX_TRUST_PROXIES=true"
   Environment="MP3METAFIX_TRUSTED_PROXIES=<TRUSTED_PROXY_IP>"
   ```
   Replace `<TRUSTED_PROXY_IP>` with the exact IP or CIDR of your reverse proxy (e.g. `10.0.0.5` or `192.168.1.50`).

2. **Restricting Direct Access to Port 8844**:
   > [!IMPORTANT]
   > The MP3MetaFix installer does not automatically modify or enable system firewalls. If binding to `0.0.0.0`, manually configure your host firewall (e.g. UFW or iptables) so that only your reverse proxy can communicate with port 8844:

   **UFW Example (Manual Setup):**
   ```bash
   # Allow incoming connections on port 8844 ONLY from the reverse proxy
   sudo ufw allow from <TRUSTED_PROXY_IP> to any port 8844 proto tcp comment 'Reverse Proxy Only'

   # Deny direct access to port 8844 from all other addresses
   sudo ufw deny 8844/tcp comment 'Block Direct Port 8844 Access'
   ```

   **iptables Example (Manual Setup):**
   ```bash
   sudo iptables -A INPUT -p tcp -s <TRUSTED_PROXY_IP> --dport 8844 -m state --state NEW,ESTABLISHED -j ACCEPT
   sudo iptables -A INPUT -p tcp --dport 8844 -j DROP
   ```

---

### Understanding Hairpin NAT (NAT Loopback) & Local Health Probes

When configuring a public domain or reverse proxy hostname using `./install.sh --domain <DOMAIN>` inside an LXC container, Docker container, VM, or homelab network:

1. **Local Probe vs External Reachability**:
   - The installer automatically sends an informational health check to `https://<DOMAIN>/api/health` from the **local machine** executing the installer.
   - If the local health probe reports:
     ```
     • Proxy Domain Health: UNREACHABLE from this host (https://... - check DNS / NAT)
     ```
     this indicates that the **local container/host cannot loop back to its own public domain from within the internal LAN**.

2. **Why This Happens (Hairpin NAT)**:
   - When a container or machine inside the LAN (`192.168.0.190`) requests its own public domain, DNS resolves to the external WAN IP (or Cloudflare/CDN proxy).
   - For internal packets destined for the router's WAN IP to loop back inside to the reverse proxy host (`192.168.0.55`), the local router/gateway must support and enable **Hairpin NAT** (also known as *NAT Loopback* or *NAT Reflection*).
   - Many consumer homelab routers, default container bridge interfaces, or Proxmox virtual networks do not hairpin loopback traffic from internal IPs back into the LAN.

3. **External Client Access is Unaffected**:
   - External clients (smartphones, office workstations, external browsers) route through standard internet DNS and WAN gateways to your reverse proxy without involving internal LAN loopback.
   - To verify that your reverse proxy and MP3MetaFix instance are fully functional from outside the host, run from your client workstation:
     ```bash
     curl -I https://mp3metafix.yourdomain.com/api/health
     ```
     An `HTTP 200 OK` response confirms that DNS, Cloudflare/CDN, reverse proxy routing, and the MP3MetaFix backend are all working properly.

---

## 5. Updates and Upgrades

Editor update-check badge compatibility is a frontend-only fix. Reload `/app/` to load the corrected JavaScript; no service configuration or data migration is required.

To update MP3MetaFix in-place via CLI:
```bash
cd /opt/mp3metafix
sudo ./install.sh --update
```
This automatically:
1. Pulls repository updates from the configured remote branch, seamlessly re-executes the updated installer in-place, and reloads `VERSION`.
2. Updates Python virtual environment dependencies.
3. Automatically and safely migrates existing systemd units (`/etc/systemd/system/mp3metafix.service` or `~/.config/systemd/user/mp3metafix.service`) using `scripts/migrate_service.py` to upgrade legacy launch commands to use `--no-proxy-headers` and `$MP3METAFIX_HOST` / `$MP3METAFIX_PORT` without overwriting administrator environment variables, workers, or cgroups.
4. Executes `systemctl daemon-reload` and restarts the service.

> [!IMPORTANT]
> **Upgrades Starting from Legacy Installers (`v0.3.0` & `v0.3.1`)**:
> When an upgrade is initiated under a legacy installer version (`v0.3.0` or `v0.3.1` running in memory), the initial `./install.sh --update` pulls the updated repository code to disk but completes the legacy flow (which lacked migration logic and process handoff). To apply the systemd service migration on those systems, either:
> 1. Run `./install.sh --update` a **second time** (which now executes the new installer from disk), or
> 2. Execute the standalone migration command: `sudo .venv/bin/python scripts/migrate_service.py /etc/systemd/system/mp3metafix.service && sudo systemctl daemon-reload && sudo systemctl restart mp3metafix.service`.
>
> *(Note: The standalone installer in `v0.3.2` already included `migrate_existing_services` directly. All updates initiated from `v0.3.3` onward additionally feature the automatic in-place process handoff and execute new migrations immediately during the first update invocation).*

### Manual Systemd Unit Migration (Optional)
If you wish to inspect or run the migration script directly without pulling Git updates:
```bash
# Run migration helper
sudo /opt/mp3metafix/.venv/bin/python /opt/mp3metafix/scripts/migrate_service.py /etc/systemd/system/mp3metafix.service

# Reload systemd and restart
sudo systemctl daemon-reload
sudo systemctl restart mp3metafix.service
```

> [!NOTE]
> In-app web updater execution (`POST /api/updates/apply`) is enabled by default and requires administrator authorization plus CSRF checks. It is disabled on the migrated workstation by `MP3METAFIX_ALLOW_WEB_UPDATES=false`. A process-shared lock and inherited installer descriptor prevent overlapping web updates; a background task survives stream disconnects and only fixed messages reach the browser. Web updates install files/dependencies and explicitly require a local restart (using the appropriate user/system service for that installation). They do not invoke service managers or unit migration. Modification requires the project’s maintainer-approval checkpoint.

---

### Diagnostics location

Open **Admin Dashboard** from the signed-in administrator account menu to view live diagnostics at `/admin`. The Gateway Hub (`/`) now shows workspace choices and checks basic health/version once; it no longer displays or polls CPU, memory, disk, or cache metrics. This frontend relocation needs no service configuration, permission, or data migration changes.

### Audio format support (v0.5.0)

MP3, M4A (AAC/ALAC/Opus), and standard RIFF/WAVE uploads use the existing Mutagen dependency; there is no new runtime package or transcoding service. RF64, raw AAC, video MP4, and other formats are not accepted. Reload the editor after updating to load the versioned frontend assets; restart the backend through your normal local workflow to load the new handlers. Existing MP3 sessions and secrets require no migration.

MP3, WAV, and real Suno Opus M4A editing have been verified. Synthetic AAC M4A tests also pass.

The endpoint’s existing 150 MB upload limit applies to every format; WAV can reach it sooner because it is often uncompressed. Allow temporary disk headroom for atomic tag writes (up to two additional file copies for WAV with INFO metadata). Playback and waveform decoding depend on browser codec support; metadata editing does not require browser decoding. WAV ID3/cover-art compatibility differs between players.

## 6. Environment Variables & Security Configuration

The server reads environment variables. Configure them in the service environment or export them before launch; the backend does not itself load a `.env` file. Persisted UI quota/TTL preferences currently do not reconfigure these runtime limits. ASGI admission now checks access before multipart parsing, counts actual body bytes, enforces read deadlines, and serializes storage mutations across workers. Keep proxy limits as an additional boundary.

| Variable | Default | Description |
|---|---|---|
| `MP3METAFIX_ALLOW_WEB_UPDATES` | `true` | Set false for deployments whose code is read-only to the backend; administrator update checks remain available |
| `MP3METAFIX_HOST` | `127.0.0.1` | Bind interface (use `127.0.0.1` when proxy is colocated; `0.0.0.0` with firewall when proxy is external) |
| `MP3METAFIX_PORT` | `8844` | Server listening port |
| `MP3METAFIX_SESSION_TTL_MINUTES` | `60` | Inactivity TTL for uploaded sessions |
| `MP3METAFIX_MAX_UPLOAD_SIZE_MB` | `150` | Maximum single audio upload size (MB) |
| `MP3METAFIX_MAX_ARTWORK_SIZE_MB` | `10` | Maximum artwork upload size (MB) |
| `MP3METAFIX_MAX_GLOBAL_STORAGE_MB` | `2048` | Session-storage budget with LRU pruning and serialized upload/save headroom checks; excludes unrelated host files |
| `MP3METAFIX_TRUST_PROXIES` | `false` | Enables reverse proxy header processing (`X-Forwarded-For`, `X-Forwarded-Proto`) |
| `MP3METAFIX_TRUSTED_PROXIES` | `127.0.0.1,::1` | Comma-separated list of trusted proxy IPs or CIDRs |
| `MP3METAFIX_SECRET_KEY` | *(auto-generated)* | Cryptographic HMAC secret key (persisted to `data/.secret_key`) |

For comprehensive vulnerability analysis, attack surfaces, and defense mechanisms, consult [docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md).

## Local account migration (unreleased)

The [local migration procedure](ACCOUNT_MIGRATION.md) adds explicit `--migrate-account`, `--retry-account`, and `--rollback-account` installer actions for the standard developer user service. This is not an automatic upgrade change. Root cutover requires local administrator authentication. The workstation retry completed and its dedicated identity, health, and effective restrictions were verified; retain recovery data until browser editing is confirmed. General existing-system-service migration and installer account selection/creation remain planned.

The Ubuntu 24.04 testing LXC upgrade from v0.4.0 to v0.5.1 was reported successful. No pre-upgrade snapshot was taken, so that original baseline is not available for a repeat test. Account migration in that LXC has not been tested. Duplicate workstation system-unit disablement was reported completed after the v0.5.1 audit.

The release source is strictly enforced as `origin main`; development testing uses the `development` branch separately. The installer validates that the checkout is on the `main` branch before performing updates, fetches updates explicitly from `origin main`, fast-forwards cleanly (`--ff-only`), and halts with clear errors on failure without falling back to generic `git pull` or overwriting local branches.
