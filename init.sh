#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DIR/.venv"
SYSTEM_COMMANDS=(exiftool file tesseract pdftotext antiword unrtf)
APT_PACKAGES=(libimage-exiftool-perl file tesseract-ocr poppler-utils antiword unrtf)

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(timestamp)] $*"
}

collect_missing_system_tools() {
  local missing=()
  for cmd in "${SYSTEM_COMMANDS[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if ((${#missing[@]} > 0)); then
    printf '%s\n' "${missing[@]}"
  fi
}

have_passwordless_sudo() {
  if [[ $EUID -eq 0 ]]; then
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    return 1
  fi
  if sudo -n true 2>/dev/null; then
    return 0
  fi
  return 1
}

install_system_packages() {
  if [[ $EUID -eq 0 ]]; then
    log "Running apt-get update"
    apt-get update
    log "Installing packages: ${APT_PACKAGES[*]}"
    apt-get install -y "${APT_PACKAGES[@]}"
  else
    log "Running apt-get update"
    sudo -n apt-get update
    log "Installing packages: ${APT_PACKAGES[*]}"
    sudo -n apt-get install -y "${APT_PACKAGES[@]}"
  fi
}

ensure_system_dependencies() {
  mapfile -t missing_tools < <(collect_missing_system_tools)
  if ((${#missing_tools[@]} == 0)); then
    log "All required system tools are already installed"
    return 0
  fi

  log "Missing system tools detected: ${missing_tools[*]}"

  if command -v apt-get >/dev/null 2>&1 && have_passwordless_sudo; then
    log "Attempting to install required packages: ${APT_PACKAGES[*]}"
    install_system_packages
    mapfile -t post_install_missing < <(collect_missing_system_tools)
    if ((${#post_install_missing[@]} == 0)); then
      return 0
    fi
    log "Error: system dependencies are still missing after apt-get install: ${post_install_missing[*]}"
    exit 1
  fi

  log "Error: cannot install system dependencies automatically in non-interactive mode."
  log "Install the following packages manually before rerunning: ${APT_PACKAGES[*]}"
  exit 1
}

log "===== Initializing file-sorter dependencies ====="

# Check python3
log "Checking for python3"
if ! command -v python3 >/dev/null 2>&1; then
  log "Error: python3 is not installed on this environment."
  log "Install python3 via your OS package manager, then re-run this script."
  exit 1
fi

# Check timeout command (needed for test.sh)
if ! command -v timeout >/dev/null 2>&1; then
  log "Error: timeout command not found; install coreutils/timeout before running tests." >&2
  exit 1
fi

# Check Docker (needed for run.sh)
log "Checking for Docker"
if ! command -v docker >/dev/null 2>&1; then
  log "Warning: docker is not installed. Install Docker if you plan to use run.sh" >&2
else
  if ! docker compose version >/dev/null 2>&1; then
    log "Warning: docker compose (v2) is not available. Install Docker Compose v2 if you plan to use run.sh" >&2
  else
    log "Docker and Docker Compose v2 are available"
  fi
fi

# Create venv if missing or incomplete
if [[ ! -d "$VENV_DIR" || ! -x "$VENV_DIR/bin/python" ]]; then
  log "Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  log "Virtual environment already exists at $VENV_DIR"
fi

log "Activating virtualenv"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Install system dependencies
log "Installing system dependencies"
ensure_system_dependencies

# Install Python dependencies
log "Installing Python dependencies"
pip install -r "$DIR/requirements.txt"

# Install development tools
log "Installing mypy for type checking"
pip install mypy --quiet

log "===== Initialization complete ====="
log "You can now run test.sh or run.sh"
