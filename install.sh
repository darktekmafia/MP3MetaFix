#!/usr/bin/env bash
# ==============================================================================
# MP3MetaFix - Universal Intelligent Linux Installer & Updater
# Supports: Fedora, Ubuntu/Debian, Arch, openSUSE (Headless LXC & Desktop Environments)
# ==============================================================================

set -e

APP_NAME="mp3metafix"
APP_DISPLAY_NAME="MP3MetaFix"
DEFAULT_PORT=8844
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="${INSTALL_DIR}/VERSION"

if [ -f "$VERSION_FILE" ]; then
    VERSION="$(cat "$VERSION_FILE" | tr -d '[:space:]')"
else
    VERSION="0.1.0"
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
    echo "  --install           Install MP3MetaFix (default action)"
    echo "  --update            Pull latest updates and rebuild dependencies"
    echo "  --uninstall         Remove MP3MetaFix service, desktop launcher, and configs"
    echo "  --status            Check installation and service status"
    echo "  --version, -v       Display application version"
    echo "  --headless          Force headless server / LXC installation mode"
    echo "  --desktop           Force desktop environment installation mode"
    echo "  --port <PORT>       Custom server port (default: 8844)"
    echo "  --user <USER>       Specify user for systemd service (default: current user)"
    echo "  --help, -h          Show this help message"
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

    USER_HOME="$HOME"
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

# Check if MP3MetaFix is already running
if curl -s -f "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "MP3MetaFix is already running. Opening browser..."
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    fi
    exit 0
fi

# Start background server
echo "Starting MP3MetaFix server on ${URL}..."
cd "$INSTALL_DIR"
"${INSTALL_DIR}/.venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port "$PORT" &
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

    # Update desktop database
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "${USER_HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true
    fi

    log_success "Installed Desktop Entry to application menu (${DESKTOP_FILE})."
}

# Headless / LXC Service Installation
install_systemd_service() {
    log_info "Configuring Headless / LXC systemd Service..."

    SERVICE_FILE="/etc/systemd/system/mp3metafix.service"
    SERVICE_TEMPLATE="${INSTALL_DIR}/deploy/mp3metafix.service"

    if [ ! -f "$SERVICE_TEMPLATE" ]; then
        log_error "Template ${SERVICE_TEMPLATE} not found."
        exit 1
    fi

    # Create populated service file in temp
    TEMP_SERVICE="/tmp/mp3metafix.service"
    sed "s|%USER%|${SERVICE_USER}|g; s|%INSTALL_DIR%|${INSTALL_DIR}|g; s|8844|${TARGET_PORT}|g" "$SERVICE_TEMPLATE" > "$TEMP_SERVICE"

    log_info "Installing systemd unit to ${SERVICE_FILE} (requires sudo)..."
    sudo cp "$TEMP_SERVICE" "$SERVICE_FILE"
    rm -f "$TEMP_SERVICE"

    sudo systemctl daemon-reload
    sudo systemctl enable --now mp3metafix.service
    log_success "MP3MetaFix systemd service enabled and started."
}

# Perform Installation
do_install() {
    print_banner
    detect_distro
    detect_environment

    log_info "Installation Target:"
    echo "  - Distro: ${DISTRO_ID} (${DISTRO_LIKE})"
    echo "  - Mode:   ${ENV_TYPE^^}"
    echo "  - Port:   ${TARGET_PORT}"
    echo "  - User:   ${SERVICE_USER}"
    echo "  - Path:   ${INSTALL_DIR}"
    echo ""

    install_system_deps
    setup_python_env

    # Create data directory
    mkdir -p "${INSTALL_DIR}/data/temp"

    if [ "$ENV_TYPE" = "desktop" ]; then
        install_desktop_integration
        echo ""
        log_success "MP3MetaFix is ready!"
        echo -e "You can launch it from your ${BOLD}Application Launcher${NC} or by running ${BOLD}mp3metafix${NC} in the terminal."
        echo -e "Access directly at: ${BOLD}http://127.0.0.1:${TARGET_PORT}${NC}"
    else
        install_systemd_service
        echo ""
        log_success "MP3MetaFix Headless Server / LXC service installed successfully!"
        echo -e "Service status: ${BOLD}sudo systemctl status mp3metafix.service${NC}"
        echo -e "Access directly at: ${BOLD}http://<YOUR-SERVER-IP>:${TARGET_PORT}${NC}"
        echo -e "Reverse proxy configs are available in ${BOLD}${INSTALL_DIR}/deploy/${NC}"
    fi
}

# Perform Update
do_update() {
    print_banner
    log_info "Updating MP3MetaFix in ${INSTALL_DIR}..."

    # If git repo, pull latest
    if [ -d "${INSTALL_DIR}/.git" ]; then
        log_info "Checking for git updates..."
        git -C "$INSTALL_DIR" fetch --tags || true
        git -C "$INSTALL_DIR" pull origin main || git -C "$INSTALL_DIR" pull || true
    fi

    # Update dependencies
    setup_python_env

    # Restart service if running
    if systemctl is-active --quiet mp3metafix.service 2>/dev/null; then
        log_info "Restarting systemd service..."
        sudo systemctl restart mp3metafix.service
        log_success "Service restarted."
    fi

    detect_environment
    if [ "$ENV_TYPE" = "desktop" ]; then
        install_desktop_integration
    fi

    log_success "MP3MetaFix updated to latest version (${VERSION})!"
}

# Check Status
do_status() {
    print_banner
    echo "MP3MetaFix Version: ${VERSION}"
    echo "Install Directory:  ${INSTALL_DIR}"

    detect_environment
    echo "Detected Mode:      ${ENV_TYPE^^}"

    if systemctl is-active --quiet mp3metafix.service 2>/dev/null; then
        echo -e "Systemd Service:    ${GREEN}ACTIVE (Running)${NC}"
    elif systemctl is-enabled --quiet mp3metafix.service 2>/dev/null; then
        echo -e "Systemd Service:    ${YELLOW}INACTIVE (Enabled)${NC}"
    else
        echo -e "Systemd Service:    ${RED}NOT INSTALLED / DISABLED${NC}"
    fi

    if curl -s -f "http://127.0.0.1:${DEFAULT_PORT}/api/health" >/dev/null 2>&1; then
        echo -e "HTTP Endpoint:      ${GREEN}RESPONDING on http://127.0.0.1:${DEFAULT_PORT}${NC}"
    else
        echo -e "HTTP Endpoint:      ${YELLOW}NOT RESPONDING on port ${DEFAULT_PORT}${NC}"
    fi
}

# Perform Uninstall
do_uninstall() {
    print_banner
    log_warn "Uninstalling MP3MetaFix..."

    # Stop and remove systemd service
    if [ -f /etc/systemd/system/mp3metafix.service ]; then
        log_info "Removing systemd service..."
        sudo systemctl stop mp3metafix.service || true
        sudo systemctl disable mp3metafix.service || true
        sudo rm -f /etc/systemd/system/mp3metafix.service
        sudo systemctl daemon-reload
        log_success "Systemd service removed."
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
TARGET_PORT="$DEFAULT_PORT"
SERVICE_USER="${SUDO_USER:-$USER}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install) ACTION="install"; shift ;;
        --update) ACTION="update"; shift ;;
        --uninstall) ACTION="uninstall"; shift ;;
        --status) ACTION="status"; shift ;;
        --version|-v)
            echo "MP3MetaFix v${VERSION}"
            exit 0
            ;;
        --headless) FORCE_HEADLESS=true; shift ;;
        --desktop) FORCE_DESKTOP=true; shift ;;
        --port) TARGET_PORT="$2"; shift 2 ;;
        --user) SERVICE_USER="$2"; shift 2 ;;
        --help|-h) show_help ;;
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
    uninstall) do_uninstall ;;
esac
