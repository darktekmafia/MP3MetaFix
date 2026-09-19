# MP3MetaFix Deployment Guide

This guide covers deployment options for MP3MetaFix across **Local Fedora 44 Development**, **Proxmox LXC Containers (Ubuntu/Debian)**, and **Reverse Proxies (Nginx / Caddy)**.

---

## 1. Local Fedora 44 Development

### Quick Start
```bash
cd /run/media/psychlone/Projects/mp3metafix
./run.sh
```

### Desktop Application Integration
To register MP3MetaFix in your GNOME/KDE software launcher:
```bash
./install.sh --desktop
```
This will:
1. Create `~/.local/share/applications/mp3metafix.desktop`.
2. Install the application icon to `~/.local/share/icons/hicolor/scalable/apps/mp3metafix.svg`.
3. Create the launcher binary `~/.local/bin/mp3metafix` which starts the server and opens your default browser automatically.

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
