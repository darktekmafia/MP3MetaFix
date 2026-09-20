#!/usr/bin/env bash
# ==============================================================================
# MP3MetaFix - Universal Intelligent Linux Installer & Updater
# Supports: Fedora, Ubuntu/Debian, Arch, openSUSE (Headless LXC & Desktop Environments)
# ==============================================================================

set -e

ORIG_ARGS=("$@")
APP_NAME="mp3metafix"
APP_DISPLAY_NAME="MP3MetaFix"
DEFAULT_PORT=8844
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="${INSTALL_DIR}/VERSION"

if [ -f "$VERSION_FILE" ]; then
    VERSION="$(cat "$VERSION_FILE" | tr -d '[:space:]')"
else
    VERSION="0.5.0"
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

print_banner() {
    echo -e "${PURPLE}${BOLD}"
    echo "  __  __ _____ ____  __  __      _        _____ _       "
    echo " |  \/  |  __ \___ \|  \/  |    | |      |  ___(_)      "
    echo " | \  / | |__) |__) | \  / | ___| |_ __ _| |_   ___  __ "
    echo " | |\/| |  ___/|__ <| |\/| |/ _ \ __/ _\` |  _| | \ \/ / "
    echo " | |  | | |    ___) | |  | |  __/ || (_| | |   | |>  <  "
    echo " |_|  |_|_|   |____/|_|  |_|\___|\__\__,_|_|   |_/_/\_\ "
    echo -e "                   v${VERSION} - Linux Installer${NC}\n"
}

log_info() { echo -e "${CYAN}[*]${NC} $1"; }
log_success() { echo -e "${GREEN}[+]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[-]${NC} $1"; }

show_help() {
    print_banner
    echo -e "Usage: ${BOLD}./install.sh [OPTIONS]${NC}\n"
    echo "Options:"
    echo "  --install               Install MP3MetaFix & systemd service (default action)"
    echo "  --update                Pull latest updates and rebuild dependencies"
    echo "  --uninstall             Remove MP3MetaFix service, desktop launcher, and configs"
    echo "  --status                Check installation and service status"
    echo "  --access                Show network bind address, proxy trust status, and LAN URLs"
    echo "  --lan                   Switch service to listen on all network interfaces (0.0.0.0)"
    echo "  --local                 Switch service to listen on localhost only (127.0.0.1)"
    echo "  --bind <HOST>           Set custom bind host (e.g. 0.0.0.0, 127.0.0.1, or IP)"
    echo "  --proxy [IPS]           Enable reverse proxy trust (optional comma-separated IPs)"
    echo "  --no-proxy              Disable reverse proxy trust (ignore forwarded headers)"
    echo "  --trusted-proxies <IPS> Set explicit trusted reverse proxy IPs/subnets"
    echo "  --domain <DOMAIN>       Set reverse proxy domain/hostname or public URL"
    echo "  --no-domain             Clear configured reverse proxy domain/hostname"
    echo "  --version, -v           Display application version"
    echo "  --no-service            Skip installing systemd service"
    echo "  --headless              Force headless server / LXC installation mode"
    echo "  --desktop               Force desktop environment installation mode"
    echo "  --port <PORT>           Custom server port (default: 8844)"
    echo "  --user <USER>           Specify user for systemd service (default: current user)"
    echo "  --help, -h              Show this help message"
    echo ""
    exit 0
}

# Distro Detection
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO_ID="$ID"
        DISTRO_LIKE="${ID_LIKE:-$ID}"
    else
        DISTRO_ID="unknown"
        DISTRO_LIKE="unknown"
    fi
}

# Environment Detection (Desktop vs Headless)
detect_environment() {
    if [ "$FORCE_HEADLESS" = true ]; then
        ENV_TYPE="headless"
        return
    fi
    if [ "$FORCE_DESKTOP" = true ]; then
        ENV_TYPE="desktop"
        return
    fi

    if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ] || [ -n "$XDG_CURRENT_DESKTOP" ]; then
        ENV_TYPE="desktop"
    else
        ENV_TYPE="headless"
    fi
}

# Install System Dependencies
install_system_deps() {
    if command -v python3 >/dev/null 2>&1 && command -v ffmpeg >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
        log_success "System dependencies already satisfied (python3, ffmpeg, curl detected)."
        return 0
    fi

    log_info "Detecting system package manager (Distro: ${DISTRO_ID})..."

    if command -v dnf >/dev/null 2>&1; then
        log_info "Installing dependencies via DNF (Fedora/RHEL)..."
        sudo dnf install -y python3 python3-pip python3-virtualenv ffmpeg curl
    elif command -v apt-get >/dev/null 2>&1; then
        log_info "Installing dependencies via APT (Ubuntu/Debian)..."
        sudo apt-get update -y
        sudo apt-get install -y python3 python3-pip python3-venv ffmpeg curl
    elif command -v pacman >/dev/null 2>&1; then
        log_info "Installing dependencies via Pacman (Arch Linux)..."
        sudo pacman -Sy --noconfirm python python-pip python-virtualenv ffmpeg curl
    elif command -v zypper >/dev/null 2>&1; then
        log_info "Installing dependencies via Zypper (openSUSE)..."
        sudo zypper install -y python3 python3-pip python3-virtualenv ffmpeg curl
    else
        log_warn "No recognized package manager found. Please ensure Python 3.10+, pip, venv, and ffmpeg are installed."
    fi
}

# Setup Python Virtual Environment
setup_python_env() {
    log_info "Setting up Python virtual environment in ${INSTALL_DIR}/.venv..."
    if [ ! -d "${INSTALL_DIR}/.venv" ]; then
        python3 -m venv "${INSTALL_DIR}/.venv"
    fi

    "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
    log_info "Installing Python dependencies from backend/requirements.txt..."
    "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/backend/requirements.txt"
    log_success "Python environment prepared."
}

# Desktop Environment Installation
install_desktop_integration() {
    log_info "Configuring Desktop Environment Integration..."

    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    else
        USER_HOME="$HOME"
    fi

    ICON_DIR="${USER_HOME}/.local/share/icons/hicolor/scalable/apps"
    APPS_DIR="${USER_HOME}/.local/share/applications"
    BIN_DIR="${USER_HOME}/.local/bin"

    mkdir -p "$ICON_DIR" "$APPS_DIR" "$BIN_DIR"

    # 1. Install Icon
    if [ -f "${INSTALL_DIR}/assets/mp3metafix.svg" ]; then
        cp "${INSTALL_DIR}/assets/mp3metafix.svg" "${ICON_DIR}/mp3metafix.svg"
        log_success "Installed application icon to ${ICON_DIR}/mp3metafix.svg"
    fi

    # 2. Create Launcher Wrapper Script
    WRAPPER_SCRIPT="${BIN_DIR}/mp3metafix"
    cat > "$WRAPPER_SCRIPT" << 'EOF'
#!/usr/bin/env bash
INSTALL_DIR="__INSTALL_DIR__"
PORT="__PORT__"
URL="http://127.0.0.1:${PORT}"

# Check if MP3MetaFix service is active or endpoint is responding
if curl -s -f "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    fi
    exit 0
fi

# Try starting systemd service if available
if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled --quiet mp3metafix.service 2>/dev/null; then
    systemctl start mp3metafix.service 2>/dev/null || sudo systemctl start mp3metafix.service 2>/dev/null || true
    sleep 0.5
    if curl -s -f "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$URL" >/dev/null 2>&1 &
        fi
        exit 0
    fi
fi

# Fallback: Start background server process directly
echo "Starting MP3MetaFix on ${URL}..."
cd "$INSTALL_DIR"
"${INSTALL_DIR}/.venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port "$PORT" --no-proxy-headers &
SERVER_PID=$!

# Wait for server ready
for i in {1..30}; do
    if curl -s -f "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Open Default Browser
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
fi

wait $SERVER_PID
EOF

    # Replace placeholders
    sed -i "s|__INSTALL_DIR__|${INSTALL_DIR}|g" "$WRAPPER_SCRIPT"
    sed -i "s|__PORT__|${TARGET_PORT}|g" "$WRAPPER_SCRIPT"
    chmod +x "$WRAPPER_SCRIPT"

    # Ensure correct ownership if run via sudo
    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        chown -R "${SUDO_USER}:" "$ICON_DIR/mp3metafix.svg" "$WRAPPER_SCRIPT" 2>/dev/null || true
    fi

    log_success "Installed launcher command: ${WRAPPER_SCRIPT}"

    # 3. Create .desktop Entry
    DESKTOP_FILE="${APPS_DIR}/mp3metafix.desktop"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=MP3MetaFix
GenericName=Audio Metadata Editor
Comment=Server-side and local MP3 ID3 tag and cover art editor
Exec=${WRAPPER_SCRIPT}
Icon=mp3metafix
Terminal=false
Categories=AudioVideo;Audio;AudioVideoEditing;
Keywords=mp3;id3;tag;metadata;audio;music;editor;
StartupNotify=true
EOF
    chmod +x "$DESKTOP_FILE"

    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        chown "${SUDO_USER}:" "$DESKTOP_FILE" 2>/dev/null || true
    fi

    # Update desktop database
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "${USER_HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true
    fi

    log_success "Installed Desktop Entry to application menu (${DESKTOP_FILE})."
}

# Systemd Service Installation
install_systemd_service() {
    SERVICE_TEMPLATE="${INSTALL_DIR}/deploy/mp3metafix.service"
    if [ ! -f "$SERVICE_TEMPLATE" ]; then
        log_error "Template ${SERVICE_TEMPLATE} not found."
        exit 1
    fi

    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        REAL_USER="$SUDO_USER"
        REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    else
        REAL_USER="$USER"
        REAL_HOME="$HOME"
    fi

    # Determine whether to use systemd user service or system-wide service
    # User service is preferred for desktop sessions and paths under /home/ or /run/media/
    if [ "$ENV_TYPE" = "desktop" ] || [[ "$INSTALL_DIR" == /home/* ]] || [[ "$INSTALL_DIR" == /run/media/* ]]; then
        log_info "Configuring systemd User Service (systemctl --user)..."
        USER_SERVICE_DIR="${REAL_HOME}/.config/systemd/user"
        mkdir -p "$USER_SERVICE_DIR"
        USER_SERVICE_FILE="${USER_SERVICE_DIR}/mp3metafix.service"

        cat > "$USER_SERVICE_FILE" << EOF
[Unit]
Description=MP3MetaFix Web Server & Audio Metadata Editor
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
Environment="PATH=${INSTALL_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="MP3METAFIX_HOST=127.0.0.1"
Environment="MP3METAFIX_PORT=${TARGET_PORT}"
Environment="MP3METAFIX_DATA_DIR=${INSTALL_DIR}/data"
Environment="MP3METAFIX_TRUST_PROXIES=false"
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn backend.main:app --host \$MP3METAFIX_HOST --port \$MP3METAFIX_PORT --workers 2 --no-proxy-headers
Restart=always
RestartSec=3
MemoryMax=512M
TasksMax=64
CPUQuota=80%

[Install]
WantedBy=default.target
EOF

        if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
            chown -R "${REAL_USER}:" "$USER_SERVICE_DIR"
            sudo -u "$REAL_USER" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$REAL_USER")/bus" systemctl --user daemon-reload 2>/dev/null || true
            sudo -u "$REAL_USER" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$REAL_USER")/bus" systemctl --user enable --now mp3metafix.service 2>/dev/null || true
        else
            systemctl --user daemon-reload 2>/dev/null || true
            systemctl --user enable --now mp3metafix.service 2>/dev/null || true
        fi
        loginctl enable-linger "$REAL_USER" 2>/dev/null || true
        log_success "MP3MetaFix systemd user service enabled and started."
    else
        log_info "Configuring systemd System Service (/etc/systemd/system/mp3metafix.service)..."
        SERVICE_FILE="/etc/systemd/system/mp3metafix.service"
        TEMP_SERVICE="/tmp/mp3metafix.service"
        sed "s|%USER%|${SERVICE_USER}|g; s|%INSTALL_DIR%|${INSTALL_DIR}|g; s|8844|${TARGET_PORT}|g" "$SERVICE_TEMPLATE" > "$TEMP_SERVICE"

        if [ "$EUID" -eq 0 ]; then
            cp "$TEMP_SERVICE" "$SERVICE_FILE"
            rm -f "$TEMP_SERVICE"
            systemctl daemon-reload
            systemctl enable --now mp3metafix.service
        else
            sudo cp "$TEMP_SERVICE" "$SERVICE_FILE"
            rm -f "$TEMP_SERVICE"
            sudo systemctl daemon-reload
            sudo systemctl enable --now mp3metafix.service
        fi
        log_success "MP3MetaFix systemd system service enabled and started."
    fi
}

# Perform Installation
do_install() {
    print_banner
    detect_distro
    detect_environment

    log_info "Installation Target:"
    echo "  - Distro:  ${DISTRO_ID} (${DISTRO_LIKE})"
    echo "  - Mode:    ${ENV_TYPE^^}"
    echo "  - Service: systemd (enabled on boot)"
    echo "  - Port:    ${TARGET_PORT}"
    echo "  - User:    ${SERVICE_USER}"
    echo "  - Path:    ${INSTALL_DIR}"
    echo ""

    install_system_deps
    setup_python_env

    # Create data directory
    mkdir -p "${INSTALL_DIR}/data/temp"
    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        chown -R "${SERVICE_USER}:" "${INSTALL_DIR}/data" "${INSTALL_DIR}/.venv" 2>/dev/null || true
    fi

    # Always install and enable systemd service unless explicit skip
    if [ "$SKIP_SERVICE" != true ]; then
        install_systemd_service
    fi

    # If desktop environment detected, also install desktop launcher and icon
    if [ "$ENV_TYPE" = "desktop" ] || [ "$FORCE_DESKTOP" = true ]; then
        install_desktop_integration
    fi

    echo ""
    log_success "MP3MetaFix installation complete!"
    echo -e "  • ${BOLD}Web Access:${NC}      http://127.0.0.1:${TARGET_PORT} (Localhost only)"
    echo -e "  • ${BOLD}LAN Access:${NC}      Run '${CYAN}./install.sh --lan${NC}' to allow access from other machines"
    echo -e "  • ${BOLD}Service Status:${NC}  systemctl --user status mp3metafix.service (or sudo systemctl status mp3metafix.service)"
    if [ "$ENV_TYPE" = "desktop" ]; then
        echo -e "  • ${BOLD}App Launcher:${NC}    Available in your system Application Menu"
    fi
    echo -e "  • ${BOLD}Auto-Start:${NC}      Active & enabled to automatically start on boot"
}

# Migrate Existing Systemd Units
migrate_existing_services() {
    MIGRATE_SCRIPT="${INSTALL_DIR}/scripts/migrate_service.py"
    if [ ! -f "$MIGRATE_SCRIPT" ] || [ ! -f "${INSTALL_DIR}/.venv/bin/python" ]; then
        return 0
    fi

    local migration_error=false

    # 1. System-wide service
    SYS_SVC="/etc/systemd/system/mp3metafix.service"
    if [ -f "$SYS_SVC" ]; then
        log_info "Checking system service (${SYS_SVC}) for launch settings migration..."
        set +e
        if [ "$EUID" -eq 0 ]; then
            "${INSTALL_DIR}/.venv/bin/python" "$MIGRATE_SCRIPT" "$SYS_SVC"
            SVC_RES=$?
        else
            sudo "${INSTALL_DIR}/.venv/bin/python" "$MIGRATE_SCRIPT" "$SYS_SVC"
            SVC_RES=$?
        fi
        set -e

        if [ "$SVC_RES" -eq 0 ]; then
            log_info "Reloading systemd daemon for migrated system service..."
            if [ "$EUID" -eq 0 ]; then
                if ! systemctl daemon-reload; then
                    log_error "Failed to reload systemd daemon."
                    migration_error=true
                fi
            else
                if ! sudo systemctl daemon-reload; then
                    log_error "Failed to reload systemd daemon."
                    migration_error=true
                fi
            fi
        elif [ "$SVC_RES" -eq 2 ]; then
            log_info "System service (${SYS_SVC}) is already up to date."
        else
            log_error "Migration failed for ${SYS_SVC}. Unit was left unchanged."
            migration_error=true
        fi
    fi

    # 2. User service
    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        TARGET_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    else
        TARGET_HOME="$HOME"
    fi
    USER_SVC="${TARGET_HOME}/.config/systemd/user/mp3metafix.service"
    if [ -f "$USER_SVC" ]; then
        log_info "Checking user service (${USER_SVC}) for launch settings migration..."
        set +e
        "${INSTALL_DIR}/.venv/bin/python" "$MIGRATE_SCRIPT" "$USER_SVC"
        USER_SVC_RES=$?
        set -e

        if [ "$USER_SVC_RES" -eq 0 ]; then
            log_info "Reloading systemd user daemon for migrated user service..."
            if ! systemctl --user daemon-reload; then
                log_error "Failed to reload systemd user daemon."
                migration_error=true
            fi
        elif [ "$USER_SVC_RES" -eq 2 ]; then
            log_info "User service (${USER_SVC}) is already up to date."
        else
            log_error "Migration failed for ${USER_SVC}. Unit was left unchanged."
            migration_error=true
        fi
    fi

    if [ "$migration_error" = true ]; then
        return 1
    fi
    return 0
}

# Perform Update
do_update() {
    print_banner
    log_info "Updating MP3MetaFix in ${INSTALL_DIR}..."

    # If git repo, pull latest
    if [ -d "${INSTALL_DIR}/.git" ]; then
        if [ "$_MP3METAFIX_REEXEC" != "1" ]; then
            log_info "Checking for git updates..."
            PREV_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)
            git -C "$INSTALL_DIR" fetch --tags || true
            git -C "$INSTALL_DIR" pull origin main || git -C "$INSTALL_DIR" pull || true
            NEW_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)

            if [ -n "$PREV_COMMIT" ] && [ -n "$NEW_COMMIT" ] && [ "$PREV_COMMIT" != "$NEW_COMMIT" ]; then
                log_info "Fetched updates (${PREV_COMMIT:0:7} -> ${NEW_COMMIT:0:7}). Re-executing updated installer..."
                export _MP3METAFIX_REEXEC=1
                exec bash "${INSTALL_DIR}/install.sh" "${ORIG_ARGS[@]}"
            fi
        fi
    fi

    # Reload version from disk after git pull to prevent stale version reporting
    if [ -f "$VERSION_FILE" ]; then
        VERSION="$(cat "$VERSION_FILE" | tr -d '[:space:]')"
    fi

    # Update dependencies
    setup_python_env

    local has_errors=false

    # Safely migrate existing service units if needed
    if ! migrate_existing_services; then
        log_warn "One or more systemd service units could not be migrated or reloaded automatically."
        has_errors=true
    fi

    detect_environment
    if [ "$ENV_TYPE" = "desktop" ]; then
        install_desktop_integration
    fi

    # Restart service if running
    if systemctl --user is-active --quiet mp3metafix.service 2>/dev/null; then
        log_info "Restarting systemd user service..."
        if systemctl --user daemon-reload && systemctl --user restart mp3metafix.service; then
            log_success "Systemd user service restarted successfully."
        else
            log_error "Failed to restart systemd user service. Check 'systemctl --user status mp3metafix.service'."
            has_errors=true
        fi
    elif systemctl is-active --quiet mp3metafix.service 2>/dev/null; then
        log_info "Restarting systemd system service..."
        if [ "$EUID" -eq 0 ]; then
            if systemctl daemon-reload && systemctl restart mp3metafix.service; then
                log_success "Systemd system service restarted successfully."
            else
                log_error "Failed to restart systemd system service. Check 'sudo systemctl status mp3metafix.service'."
                has_errors=true
            fi
        else
            if sudo systemctl daemon-reload && sudo systemctl restart mp3metafix.service; then
                log_success "Systemd system service restarted successfully."
            else
                log_error "Failed to restart systemd system service. Check 'sudo systemctl status mp3metafix.service'."
                has_errors=true
            fi
        fi
    fi

    if [ "$has_errors" = true ]; then
        log_warn "MP3MetaFix updated to ${VERSION}, but one or more service migration/restart operations failed."
        return 1
    else
        log_success "MP3MetaFix updated to latest version (${VERSION})!"
    fi
}

# Locate active MP3MetaFix service file
get_active_service_file() {
    local user_home
    if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
        user_home=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    else
        user_home="$HOME"
    fi
    local user_svc="${user_home}/.config/systemd/user/mp3metafix.service"
    local sys_svc="/etc/systemd/system/mp3metafix.service"

    if [ -f "$user_svc" ]; then
        echo "${user_svc}:user"
    elif [ -f "$sys_svc" ]; then
        echo "${sys_svc}:system"
    else
        echo ""
    fi
}

# Network Access Configuration & Inspection Workflow
do_access_config() {
    print_banner
    local target_host="$1"
    local target_port="$2"
    local target_trust="$3"
    local target_trusted="$4"
    local target_domain="$5"
    local cfg_script="${INSTALL_DIR}/scripts/configure_access.py"

    if [ ! -f "$cfg_script" ] || [ ! -f "${INSTALL_DIR}/.venv/bin/python" ]; then
        log_error "Configuration helper or virtual environment not found in ${INSTALL_DIR}."
        exit 1
    fi

    local svc_info
    svc_info="$(get_active_service_file)"
    if [ -z "$svc_info" ]; then
        log_warn "No installed MP3MetaFix systemd service unit found."
        log_info "To install MP3MetaFix and create the service, run: ./install.sh"
        exit 1
    fi

    local svc_file="${svc_info%%:*}"
    local svc_type="${svc_info##*:}"

    # If no modification is requested, perform inspection
    if [ -z "$target_host" ] && [ -z "$target_port" ] && [ -z "$target_trust" ] && [ -z "$target_trusted" ] && [ -z "$target_domain" ]; then
        log_info "Inspecting network access configuration (${svc_type} service: ${svc_file})..."
        local json_info
        json_info="$("${INSTALL_DIR}/.venv/bin/python" "$cfg_script" get "$svc_file")"
        local cur_host cur_port cur_trust cur_trusted cur_domain
        cur_host=$(echo "$json_info" | grep -oP '(?<="host": ")[^"]+' || echo "127.0.0.1")
        cur_port=$(echo "$json_info" | grep -oP '(?<="port": ")[^"]+' || echo "8844")
        cur_trust=$(echo "$json_info" | grep -oP '(?<="trust_proxies": ")[^"]+' || echo "false")
        cur_trusted=$(echo "$json_info" | grep -oP '(?<="trusted_proxies": ")[^"]+' || echo "127.0.0.1,::1")
        cur_domain=$(echo "$json_info" | grep -oP '(?<="proxy_host": ")[^"]+' || true)

        echo ""
        echo -e "${BOLD}Current Service Network Configuration:${NC}"
        echo -e "  • ${BOLD}Configured Bind Host:${NC} ${cur_host}"
        echo -e "  • ${BOLD}Configured Port:${NC}      ${cur_port}"
        if [ "$cur_trust" = "true" ]; then
            echo -e "  • ${BOLD}Reverse Proxy Trust:${NC}  ${GREEN}ENABLED${NC} (Trusted: ${cur_trusted})"
        else
            echo -e "  • ${BOLD}Reverse Proxy Trust:${NC}  ${YELLOW}DISABLED${NC} (Forwarded headers ignored)"
        fi
        if [ -n "$cur_domain" ]; then
            echo -e "  • ${BOLD}Reverse Proxy Domain:${NC} ${CYAN}${cur_domain}${NC}"
        fi
        echo -e "  • ${BOLD}Service File:${NC}         ${svc_file} (${svc_type})"

        # Check service status
        if [ "$svc_type" = "user" ]; then
            if systemctl --user is-active --quiet mp3metafix.service 2>/dev/null; then
                echo -e "  • ${BOLD}Service Status:${NC}       ${GREEN}ACTIVE (User Service Running)${NC}"
            else
                echo -e "  • ${BOLD}Service Status:${NC}       ${YELLOW}INACTIVE${NC}"
            fi
        else
            if systemctl is-active --quiet mp3metafix.service 2>/dev/null; then
                echo -e "  • ${BOLD}Service Status:${NC}       ${GREEN}ACTIVE (System Service Running)${NC}"
            else
                echo -e "  • ${BOLD}Service Status:${NC}       ${YELLOW}INACTIVE${NC}"
            fi
        fi

        # Determine probe host
        local probe_host="127.0.0.1"
        if [ "$cur_host" != "0.0.0.0" ] && [ "$cur_host" != "::" ] && [ "$cur_host" != "localhost" ] && [ "$cur_host" != "127.0.0.1" ]; then
            probe_host="$cur_host"
        fi

        # Check HTTP health
        if curl -s -f "http://${probe_host}:${cur_port}/api/health" >/dev/null 2>&1; then
            echo -e "  • ${BOLD}Service Health:${NC}       ${GREEN}OK (Responding on http://${probe_host}:${cur_port})${NC}"
        else
            echo -e "  • ${BOLD}Service Health:${NC}       ${YELLOW}NOT RESPONDING on port ${cur_port}${NC}"
        fi

        # Check Proxy Domain Health if configured
        local formatted_domain_url=""
        if [ -n "$cur_domain" ]; then
            formatted_domain_url="$cur_domain"
            if [[ ! "$formatted_domain_url" =~ ^https?:// ]]; then
                formatted_domain_url="https://${formatted_domain_url}"
            fi
            formatted_domain_url="${formatted_domain_url%/}"
            if curl -s -f -L -k -m 4 --connect-timeout 3 "${formatted_domain_url}/api/health" >/dev/null 2>&1; then
                echo -e "  • ${BOLD}Proxy Domain Health:${NC}  ${GREEN}OK (Responding on ${formatted_domain_url})${NC}"
            else
                if [[ ! "$cur_domain" =~ ^https?:// ]] && curl -s -f -L -k -m 4 --connect-timeout 3 "http://${cur_domain%/}/api/health" >/dev/null 2>&1; then
                    formatted_domain_url="http://${cur_domain%/}"
                    echo -e "  • ${BOLD}Proxy Domain Health:${NC}  ${GREEN}OK (Responding on ${formatted_domain_url})${NC}"
                else
                    echo -e "  • ${BOLD}Proxy Domain Health:${NC}  ${YELLOW}UNREACHABLE from this host (${formatted_domain_url}/api/health)${NC}"
                    echo -e "    ${YELLOW}↳ Note:${NC} Homelab routers without Hairpin NAT (NAT loopback) cannot query their own public domain from within the LAN. External reachability is unaffected (see docs/DEPLOYMENT.md)."
                fi
            fi
        fi

        echo ""
        echo -e "${BOLD}Access URLs:${NC}"

        if [ -n "$formatted_domain_url" ]; then
            echo -e "  • ${BOLD}Reverse Proxy / Domain:${NC} ${GREEN}${formatted_domain_url}${NC}"
        fi

        if [ "$cur_host" = "127.0.0.1" ] || [ "$cur_host" = "localhost" ]; then
            echo -e "  • ${BOLD}Localhost:${NC}            http://127.0.0.1:${cur_port}"
            echo -e "  • ${BOLD}LAN Access:${NC}            ${YELLOW}DISABLED${NC} (Bound to ${cur_host} only)"
            local lan_ips
            lan_ips=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | grep -v '^$' || true)
            if [ -n "$lan_ips" ]; then
                echo -e "    Detected network IPs on this machine:"
                while IFS= read -r ip_addr; do
                    [ -n "$ip_addr" ] && echo -e "      ➜ http://${ip_addr}:${cur_port} (Unreachable until LAN access enabled)"
                done <<< "$lan_ips"
            fi
        elif [ "$cur_host" = "0.0.0.0" ] || [ "$cur_host" = "::" ]; then
            echo -e "  • ${BOLD}Localhost:${NC}            http://127.0.0.1:${cur_port}"
            local lan_ips
            lan_ips=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | grep -v '^$' || true)
            if [ -n "$lan_ips" ]; then
                echo -e "  • ${BOLD}LAN Access:${NC}            ${GREEN}ENABLED${NC} on all network interfaces:"
                while IFS= read -r ip_addr; do
                    [ -n "$ip_addr" ] && echo -e "      ➜ http://${ip_addr}:${cur_port}"
                done <<< "$lan_ips"
            else
                echo -e "  • ${BOLD}LAN Access:${NC}            ${GREEN}ENABLED${NC} (Listening on 0.0.0.0)"
            fi
        else
            echo -e "  • ${BOLD}Bound Interface:${NC}      http://${cur_host}:${cur_port}"
            echo -e "  • ${BOLD}LAN Access:${NC}            ${GREEN}ENABLED${NC} (Listening specifically on ${cur_host})"
            echo -e "  • ${BOLD}Localhost (127.0.0.1):${NC} ${YELLOW}NOT BOUND${NC} (Service is bound only to ${cur_host})"
        fi

        echo ""
        echo -e "${BOLD}Quick Maintenance Commands:${NC}"
        echo -e "  • Enable LAN access:      ${CYAN}./install.sh --lan${NC} (or ./install.sh --bind 0.0.0.0)"
        echo -e "  • Restrict to localhost:  ${CYAN}./install.sh --local${NC} (or ./install.sh --bind 127.0.0.1)"
        echo -e "  • Custom interface bind:  ${CYAN}./install.sh --bind <IP>${NC}"
        echo -e "  • Enable proxy trust:     ${CYAN}./install.sh --proxy [TRUSTED_IPS]${NC}"
        echo -e "  • Set proxy domain:       ${CYAN}./install.sh --domain <DOMAIN_OR_URL>${NC}"
        echo -e "  • Clear proxy domain:     ${CYAN}./install.sh --no-domain${NC}"
        echo -e "  • Disable proxy trust:    ${CYAN}./install.sh --no-proxy${NC}"
        return 0
    fi

    # Update host/port/proxy/domain configuration
    log_info "Updating MP3MetaFix network configuration in ${svc_file}..."
    local py_args=("set" "$svc_file")
    if [ -n "$target_host" ]; then
        py_args+=("--host" "$target_host")
    fi
    if [ -n "$target_port" ]; then
        py_args+=("--port" "$target_port")
    fi
    if [ -n "$target_trust" ]; then
        py_args+=("--trust-proxies" "$target_trust")
    fi
    if [ -n "$target_trusted" ]; then
        py_args+=("--trusted-proxies" "$target_trusted")
    fi
    if [ -n "$target_domain" ]; then
        if [ "$target_domain" = "__CLEAR__" ]; then
            py_args+=("--no-domain")
        else
            py_args+=("--domain" "$target_domain")
        fi
    fi

    set +e
    if [ "$svc_type" = "system" ] && [ "$EUID" -ne 0 ]; then
        sudo "${INSTALL_DIR}/.venv/bin/python" "$cfg_script" "${py_args[@]}"
        local update_res=$?
    else
        "${INSTALL_DIR}/.venv/bin/python" "$cfg_script" "${py_args[@]}"
        local update_res=$?
    fi
    set -e

    if [ "$update_res" -ne 0 ] && [ "$update_res" -ne 2 ]; then
        log_error "Failed to update network configuration in ${svc_file}."
        exit 1
    fi

    log_info "Reloading systemd daemon and restarting service..."
    if [ "$svc_type" = "user" ]; then
        systemctl --user daemon-reload
        systemctl --user restart mp3metafix.service
    else
        if [ "$EUID" -eq 0 ]; then
            systemctl daemon-reload
            systemctl restart mp3metafix.service
        else
            sudo systemctl daemon-reload
            sudo systemctl restart mp3metafix.service
        fi
    fi

    # Read effective host, port, proxy, and domain configuration
    local json_info
    json_info="$("${INSTALL_DIR}/.venv/bin/python" "$cfg_script" get "$svc_file")"
    local eff_host eff_port eff_trust eff_trusted eff_domain
    eff_host=$(echo "$json_info" | grep -oP '(?<="host": ")[^"]+' || echo "127.0.0.1")
    eff_port=$(echo "$json_info" | grep -oP '(?<="port": ")[^"]+' || echo "8844")
    eff_trust=$(echo "$json_info" | grep -oP '(?<="trust_proxies": ")[^"]+' || echo "false")
    eff_trusted=$(echo "$json_info" | grep -oP '(?<="trusted_proxies": ")[^"]+' || echo "127.0.0.1,::1")
    eff_domain=$(echo "$json_info" | grep -oP '(?<="proxy_host": ")[^"]+' || true)

    # Determine probe host
    local probe_host="127.0.0.1"
    if [ "$eff_host" != "0.0.0.0" ] && [ "$eff_host" != "::" ] && [ "$eff_host" != "localhost" ] && [ "$eff_host" != "127.0.0.1" ]; then
        probe_host="$eff_host"
    fi

    # Verify health probe
    log_info "Verifying service health on http://${probe_host}:${eff_port}/api/health..."
    local health_ok=false
    for i in {1..20}; do
        if curl -s -f "http://${probe_host}:${eff_port}/api/health" >/dev/null 2>&1; then
            health_ok=true
            break
        fi
        sleep 0.2
    done

    # Check Proxy Domain Health if configured
    local formatted_domain_url=""
    local domain_probe_ok=false
    if [ -n "$eff_domain" ]; then
        formatted_domain_url="$eff_domain"
        if [[ ! "$formatted_domain_url" =~ ^https?:// ]]; then
            formatted_domain_url="https://${formatted_domain_url}"
        fi
        formatted_domain_url="${formatted_domain_url%/}"
        if curl -s -f -L -k -m 4 --connect-timeout 3 "${formatted_domain_url}/api/health" >/dev/null 2>&1; then
            domain_probe_ok=true
        elif [[ ! "$eff_domain" =~ ^https?:// ]] && curl -s -f -L -k -m 4 --connect-timeout 3 "http://${eff_domain%/}/api/health" >/dev/null 2>&1; then
            formatted_domain_url="http://${eff_domain%/}"
            domain_probe_ok=true
        fi
    fi

    echo ""
    if [ "$health_ok" = true ]; then
        log_success "Service successfully updated and verified healthy!"
    else
        log_warn "Service restarted, but health check probe on http://${probe_host}:${eff_port} has not responded yet."
    fi

    echo -e "${BOLD}Active MP3MetaFix Configuration:${NC}"
    echo -e "  • ${BOLD}Configured Bind:${NC}      ${eff_host}:${eff_port}"
    if [ "$eff_trust" = "true" ]; then
        echo -e "  • ${BOLD}Reverse Proxy Trust:${NC}  ${GREEN}ENABLED${NC} (Trusted: ${eff_trusted})"
    else
        echo -e "  • ${BOLD}Reverse Proxy Trust:${NC}  ${YELLOW}DISABLED${NC} (Forwarded headers ignored)"
    fi
    if [ -n "$eff_domain" ]; then
        echo -e "  • ${BOLD}Reverse Proxy Domain:${NC} ${CYAN}${eff_domain}${NC}"
        if [ "$domain_probe_ok" = true ]; then
            echo -e "  • ${BOLD}Proxy Domain Health:${NC}  ${GREEN}OK (Responding on ${formatted_domain_url})${NC}"
        else
            echo -e "  • ${BOLD}Proxy Domain Health:${NC}  ${YELLOW}UNREACHABLE from this host (${formatted_domain_url}/api/health)${NC}"
            echo -e "    ${YELLOW}↳ Note:${NC} Homelab routers without Hairpin NAT (NAT loopback) cannot query their own public domain from within the LAN. External reachability is unaffected (see docs/DEPLOYMENT.md)."
        fi
    fi

    echo -e "${BOLD}Active Access URLs:${NC}"
    if [ -n "$formatted_domain_url" ]; then
        echo -e "  • ${BOLD}Reverse Proxy / Domain:${NC} ${GREEN}${formatted_domain_url}${NC}"
    fi

    if [ "$eff_host" = "127.0.0.1" ] || [ "$eff_host" = "localhost" ]; then
        echo -e "  • ${BOLD}Localhost:${NC}            http://127.0.0.1:${eff_port}"
        echo -e "  • ${BOLD}LAN Access:${NC}            ${YELLOW}RESTRICTED${NC} (Bound to ${eff_host} only)"
        echo -e "    Run '${CYAN}./install.sh --lan${NC}' anytime to enable access from other machines on your LAN."
    elif [ "$eff_host" = "0.0.0.0" ] || [ "$eff_host" = "::" ]; then
        echo -e "  • ${BOLD}Localhost:${NC}            http://127.0.0.1:${eff_port}"
        local lan_ips
        lan_ips=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | grep -v '^$' || true)
        if [ -n "$lan_ips" ]; then
            echo -e "  • ${BOLD}LAN Access:${NC}"
            while IFS= read -r ip_addr; do
                [ -n "$ip_addr" ] && echo -e "      ➜ http://${ip_addr}:${eff_port}"
            done <<< "$lan_ips"
        fi
    else
        echo -e "  • ${BOLD}Bound Interface:${NC}      http://${eff_host}:${eff_port}"
        echo -e "  • ${BOLD}LAN Access:${NC}            ${GREEN}ENABLED${NC} (Listening specifically on ${eff_host})"
        echo -e "  • ${BOLD}Localhost (127.0.0.1):${NC} ${YELLOW}NOT BOUND${NC} (Service is bound only to ${eff_host})"
    fi
}

# Check Status
do_status() {
    print_banner
    echo "MP3MetaFix Version: ${VERSION}"
    echo "Install Directory:  ${INSTALL_DIR}"

    detect_environment
    echo "Detected Mode:      ${ENV_TYPE^^}"

    local svc_info
    svc_info="$(get_active_service_file)"
    local cur_host="127.0.0.1"
    local cur_port="${DEFAULT_PORT}"
    local cur_trust="false"
    local cur_trusted="127.0.0.1,::1"
    local cur_domain=""

    if [ -n "$svc_info" ] && [ -f "${INSTALL_DIR}/scripts/configure_access.py" ] && [ -f "${INSTALL_DIR}/.venv/bin/python" ]; then
        local svc_file="${svc_info%%:*}"
        local json_info
        json_info="$("${INSTALL_DIR}/.venv/bin/python" "${INSTALL_DIR}/scripts/configure_access.py" get "$svc_file" 2>/dev/null || true)"
        if [ -n "$json_info" ]; then
            cur_host=$(echo "$json_info" | grep -oP '(?<="host": ")[^"]+' || echo "127.0.0.1")
            cur_port=$(echo "$json_info" | grep -oP '(?<="port": ")[^"]+' || echo "8844")
            cur_trust=$(echo "$json_info" | grep -oP '(?<="trust_proxies": ")[^"]+' || echo "false")
            cur_trusted=$(echo "$json_info" | grep -oP '(?<="trusted_proxies": ")[^"]+' || echo "127.0.0.1,::1")
            cur_domain=$(echo "$json_info" | grep -oP '(?<="proxy_host": ")[^"]+' || true)
        fi
    fi

    if systemctl --user is-active --quiet mp3metafix.service 2>/dev/null; then
        echo -e "Systemd Service:    ${GREEN}ACTIVE (User Service Running)${NC}"
    elif systemctl is-active --quiet mp3metafix.service 2>/dev/null; then
        echo -e "Systemd Service:    ${GREEN}ACTIVE (System Service Running)${NC}"
    elif systemctl --user is-enabled --quiet mp3metafix.service 2>/dev/null || systemctl is-enabled --quiet mp3metafix.service 2>/dev/null; then
        echo -e "Systemd Service:    ${YELLOW}INACTIVE (Enabled)${NC}"
    else
        echo -e "Systemd Service:    ${RED}NOT INSTALLED / DISABLED${NC}"
    fi

    echo -e "Configured Bind:    ${cur_host}:${cur_port}"
    if [ "$cur_trust" = "true" ]; then
        echo -e "Proxy Trust:        ${GREEN}ENABLED${NC} (Trusted: ${cur_trusted})"
    else
        echo -e "Proxy Trust:        ${YELLOW}DISABLED${NC}"
    fi
    if [ -n "$cur_domain" ]; then
        echo -e "Proxy Domain:       ${CYAN}${cur_domain}${NC}"
    fi

    local probe_host="127.0.0.1"
    if [ "$cur_host" != "0.0.0.0" ] && [ "$cur_host" != "::" ] && [ "$cur_host" != "localhost" ] && [ "$cur_host" != "127.0.0.1" ]; then
        probe_host="$cur_host"
    fi

    if curl -s -f "http://${probe_host}:${cur_port}/api/health" >/dev/null 2>&1; then
        echo -e "HTTP Endpoint:      ${GREEN}RESPONDING on http://${probe_host}:${cur_port}${NC}"
    else
        echo -e "HTTP Endpoint:      ${YELLOW}NOT RESPONDING on port ${cur_port}${NC}"
    fi

    if [ -n "$cur_domain" ]; then
        local formatted_domain_url="$cur_domain"
        if [[ ! "$formatted_domain_url" =~ ^https?:// ]]; then
            formatted_domain_url="https://${formatted_domain_url}"
        fi
        formatted_domain_url="${formatted_domain_url%/}"
        echo -e "Proxy Domain URL:   ${GREEN}${formatted_domain_url}${NC}"
    fi

    if [ "$cur_host" = "0.0.0.0" ] || [ "$cur_host" = "::" ]; then
        local lan_ips
        lan_ips=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | grep -v '^$' || true)
        if [ -n "$lan_ips" ]; then
            echo -e "LAN Endpoints:"
            while IFS= read -r ip_addr; do
                [ -n "$ip_addr" ] && echo -e "  ➜ http://${ip_addr}:${cur_port}"
            done <<< "$lan_ips"
        fi
    elif [ "$cur_host" != "127.0.0.1" ] && [ "$cur_host" != "localhost" ]; then
        echo -e "Bound Interface:    ➜ http://${cur_host}:${cur_port}"
    else
        echo -e "LAN Access:         ${YELLOW}RESTRICTED (Localhost only)${NC} — Run './install.sh --lan' to allow LAN access"
    fi
}

# Perform Uninstall
do_uninstall() {
    print_banner
    log_warn "Uninstalling MP3MetaFix..."

    # Stop and remove user systemd service
    if [ -f "${HOME}/.config/systemd/user/mp3metafix.service" ]; then
        log_info "Removing user systemd service..."
        systemctl --user stop mp3metafix.service || true
        systemctl --user disable mp3metafix.service || true
        rm -f "${HOME}/.config/systemd/user/mp3metafix.service"
        systemctl --user daemon-reload || true
        log_success "User systemd service removed."
    fi

    # Stop and remove system systemd service
    if [ -f /etc/systemd/system/mp3metafix.service ]; then
        log_info "Removing system-wide systemd service..."
        sudo systemctl stop mp3metafix.service || true
        sudo systemctl disable mp3metafix.service || true
        sudo rm -f /etc/systemd/system/mp3metafix.service
        sudo systemctl daemon-reload
        log_success "System-wide systemd service removed."
    fi

    # Remove Desktop Integration
    rm -f "${HOME}/.local/share/applications/mp3metafix.desktop"
    rm -f "${HOME}/.local/share/icons/hicolor/scalable/apps/mp3metafix.svg"
    rm -f "${HOME}/.local/bin/mp3metafix"
    log_success "Desktop integration and launchers removed."

    log_success "Uninstall completed. (Application files in ${INSTALL_DIR} remain untouched)."
}

# --- CLI Arguments Parsing ---
ACTION="install"
FORCE_HEADLESS=false
FORCE_DESKTOP=false
SKIP_SERVICE=false
TARGET_PORT="$DEFAULT_PORT"
TARGET_BIND_HOST=""
TARGET_PORT_ARG=""
TARGET_TRUST_PROXIES=""
TARGET_TRUSTED_PROXIES=""
TARGET_PROXY_HOST=""
SERVICE_USER="${SUDO_USER:-$USER}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install) ACTION="install"; shift ;;
        --update) ACTION="update"; shift ;;
        --uninstall) ACTION="uninstall"; shift ;;
        --status) ACTION="status"; shift ;;
        --access) ACTION="access"; shift ;;
        --lan)
            ACTION="access"
            TARGET_BIND_HOST="0.0.0.0"
            shift
            ;;
        --local)
            ACTION="access"
            TARGET_BIND_HOST="127.0.0.1"
            shift
            ;;
        --bind)
            ACTION="access"
            if [[ $# -ge 2 && ! "$2" =~ ^-- ]]; then
                TARGET_BIND_HOST="$2"
                shift 2
            else
                shift 1
            fi
            ;;
        --proxy|--trust-proxy|--trust-proxies)
            ACTION="access"
            TARGET_TRUST_PROXIES="true"
            if [[ $# -ge 2 && ! "$2" =~ ^-- ]]; then
                TARGET_TRUSTED_PROXIES="$2"
                shift 2
            else
                shift 1
            fi
            ;;
        --no-proxy|--no-trust-proxy|--disable-proxy)
            ACTION="access"
            TARGET_TRUST_PROXIES="false"
            shift 1
            ;;
        --trusted-proxies|--trusted-proxy)
            ACTION="access"
            TARGET_TRUST_PROXIES="true"
            if [[ $# -ge 2 && ! "$2" =~ ^-- ]]; then
                TARGET_TRUSTED_PROXIES="$2"
                shift 2
            else
                shift 1
            fi
            ;;
        --domain|--proxy-host|--url|--public-url)
            ACTION="access"
            if [[ $# -ge 2 && ! "$2" =~ ^-- ]]; then
                TARGET_PROXY_HOST="$2"
                shift 2
            else
                shift 1
            fi
            ;;
        --no-domain|--clear-domain|--no-proxy-host)
            ACTION="access"
            TARGET_PROXY_HOST="__CLEAR__"
            shift 1
            ;;
        --version|-v)
            echo "MP3MetaFix v${VERSION}"
            exit 0
            ;;
        --no-service) SKIP_SERVICE=true; shift ;;
        --headless) FORCE_HEADLESS=true; shift ;;
        --desktop) FORCE_DESKTOP=true; shift ;;
        --port)
            TARGET_PORT="$2"
            TARGET_PORT_ARG="$2"
            shift 2
            ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        --help|-h|--h|-help) show_help ;;
        *)
            log_error "Unknown option: $1"
            show_help
            ;;
    esac
done

case "$ACTION" in
    install) do_install ;;
    update) do_update ;;
    status) do_status ;;
    access) do_access_config "$TARGET_BIND_HOST" "$TARGET_PORT_ARG" "$TARGET_TRUST_PROXIES" "$TARGET_TRUSTED_PROXIES" "$TARGET_PROXY_HOST" ;;
    uninstall) do_uninstall ;;
esac
