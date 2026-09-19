# MP3MetaFix Deployment Guide

This guide covers deployment options for MP3MetaFix across **Local Fedora 44 Development**, **Proxmox LXC Containers (Ubuntu/Debian)**, and **Reverse Proxies (Nginx / Caddy)**.

---

## 1. Local Linux / Workstation Installation (Fedora / Ubuntu / Arch)

### Systemd Background Service (Starts on Boot)
To install MP3MetaFix as a persistent background service that starts on system boot and integrates into your desktop application menu:
```bash
cd /run/media/psychlone/Projects/mp3metafix
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
git clone https://github.com/your-username/mp3metafix.git /opt/mp3metafix
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

### Option A: Nginx Reverse Proxy (with SSL/TLS)

1. Copy the template from `deploy/nginx.conf`:
```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/mp3metafix
sudo ln -s /etc/nginx/sites-available/mp3metafix /etc/nginx/sites-enabled/
```

2. Edit `/etc/nginx/sites-available/mp3metafix` with your domain:
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

3. Obtain Let's Encrypt SSL certificate:
```bash
sudo certbot --nginx -d mp3.yourdomain.com
```

---

### Option B: Caddy (Automatic HTTPS)

Add to `/etc/caddy/Caddyfile`:
```caddy
mp3.yourdomain.com {
    reverse_proxy 127.0.0.1:8844
    request_body {
        max_size 150MB
    }
}
```
Reload Caddy:
```bash
sudo systemctl reload caddy
```

---

## 4. Updates and Upgrades

To update MP3MetaFix in-place:
```bash
cd /opt/mp3metafix
sudo ./install.sh --update
```
This automatically fetches the newest release, updates dependencies, and restarts the systemd service.

---

## 5. Environment Variables & Security Configuration

The server behavior and security thresholds can be customized via environment variables in systemd units or `.env` files:

| Variable | Default | Description |
|---|---|---|
| `MP3METAFIX_HOST` | `127.0.0.1` | Bind interface (keep `127.0.0.1` when using a reverse proxy) |
| `MP3METAFIX_PORT` | `8844` | Server listening port |
| `MP3METAFIX_SESSION_TTL_MINUTES` | `60` | Inactivity TTL for uploaded sessions |
| `MP3METAFIX_MAX_UPLOAD_SIZE_MB` | `150` | Maximum single MP3 upload size (MB) |
| `MP3METAFIX_MAX_ARTWORK_SIZE_MB` | `10` | Maximum artwork upload size (MB) |
| `MP3METAFIX_MAX_GLOBAL_STORAGE_MB` | `2048` | Disk quota cap before automatic LRU session pruning |
| `MP3METAFIX_RATE_LIMIT_UPLOADS` | `25` | Max upload bursts per client IP per minute |
| `MP3METAFIX_TRUST_PROXIES` | `true` | Enables proxy header validation from private/loopback CIDRs |
| `MP3METAFIX_SECRET_KEY` | *(auto-generated)* | Cryptographic HMAC secret key (persisted to `data/.secret_key`) |

For comprehensive vulnerability analysis, attack surfaces, and defense mechanisms, consult [docs/SECURITY_HARDENING.md](SECURITY_HARDENING.md).
