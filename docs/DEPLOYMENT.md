# MP3MetaFix Deployment Guide

This guide covers deployment options for MP3MetaFix across **Local Fedora 44 Development**, **Proxmox LXC Containers (Ubuntu/Debian)**, and **Reverse Proxies (Nginx / Caddy)**.

---

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

### Step 3: Managing the Service
```bash
# Check service status
sudo systemctl status mp3metafix.service

# View live logs
journalctl -u mp3metafix.service -f

# Restart service
sudo systemctl restart mp3metafix.service
```

---

## 3. Reverse Proxy Configuration

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

## 4. Updates and Upgrades

To update MP3MetaFix in-place via CLI:
```bash
cd /opt/mp3metafix
sudo ./install.sh --update
```
This automatically fetches the newest release, updates dependencies, and restarts the systemd service.

> [!NOTE]
> In-app web updater execution (`POST /api/updates/apply`) is currently disabled pending administrative authorization and privilege separation review.

---

## 5. Environment Variables & Security Configuration

The server behavior and security thresholds can be customized via environment variables in systemd units or `.env` files:

| Variable | Default | Description |
|---|---|---|
| `MP3METAFIX_HOST` | `127.0.0.1` | Bind interface (use `127.0.0.1` when proxy is colocated; `0.0.0.0` with firewall when proxy is external) |
| `MP3METAFIX_PORT` | `8844` | Server listening port |
| `MP3METAFIX_SESSION_TTL_MINUTES` | `60` | Inactivity TTL for uploaded sessions |
| `MP3METAFIX_MAX_UPLOAD_SIZE_MB` | `150` | Maximum single MP3 upload size (MB) |
| `MP3METAFIX_MAX_ARTWORK_SIZE_MB` | `10` | Maximum artwork upload size (MB) |
| `MP3METAFIX_MAX_GLOBAL_STORAGE_MB` | `2048` | Disk quota cap before automatic LRU session pruning |
| `MP3METAFIX_TRUST_PROXIES` | `false` | Enables reverse proxy header processing (`X-Forwarded-For`, `X-Forwarded-Proto`) |
| `MP3METAFIX_TRUSTED_PROXIES` | `127.0.0.1,::1` | Comma-separated list of trusted proxy IPs or CIDRs |
| `MP3METAFIX_SECRET_KEY` | *(auto-generated)* | Cryptographic HMAC secret key (persisted to `data/.secret_key`) |

For comprehensive vulnerability analysis, attack surfaces, and defense mechanisms, consult [docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md).
