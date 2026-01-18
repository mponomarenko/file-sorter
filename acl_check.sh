#!/usr/bin/env bash
# acl_check.sh - Check and optionally fix directory permissions for non-root container
set -euo pipefail

usage() {
  echo "Usage: $0 <env_file> <directory>..." >&2
  echo "  Checks if directories are writable by the container UID/GID from env_file" >&2
  exit 1
}

[[ $# -lt 2 ]] && usage

ENV_FILE="$1"
shift

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: ENV_FILE not found: $ENV_FILE" >&2
  exit 1
fi

# Extract CONTAINER_UID and CONTAINER_GID from environment or .env file
# Environment variables take precedence over .env file
if [[ -z "${CONTAINER_UID:-}" ]] || [[ -z "${CONTAINER_GID:-}" ]]; then
  CONTAINER_UID=$(grep -E '^\s*CONTAINER_UID=' "$ENV_FILE" | cut -d= -f2 | tr -d ' "'"'" || echo "")
  CONTAINER_GID=$(grep -E '^\s*CONTAINER_GID=' "$ENV_FILE" | cut -d= -f2 | tr -d ' "'"'" || echo "")
fi

if [[ -z "$CONTAINER_UID" ]] || [[ -z "$CONTAINER_GID" ]]; then
  echo "Error: CONTAINER_UID and/or CONTAINER_GID not found in environment or $ENV_FILE" >&2
  echo "Please add them to your .env file, e.g.:" >&2
  echo "  CONTAINER_UID=1000" >&2
  echo "  CONTAINER_GID=1000" >&2
  exit 1
fi

echo "[acl_check] Container runs as UID:GID = $CONTAINER_UID:$CONTAINER_GID"

HAS_ISSUES=0
for DIR in "$@"; do
  DIR_OK=0
  # Create directory if it doesn't exist
  if [[ ! -d "$DIR" ]]; then
    echo "[acl_check] Creating directory: $DIR"
    if mkdir -p "$DIR"; then
      # Set ownership if we're not already the target UID
      if [[ $(id -u) -ne $CONTAINER_UID ]]; then
        if sudo chown -R "$CONTAINER_UID:$CONTAINER_GID" "$DIR" 2>/dev/null; then
          echo "[acl_check] ✓ Directory created and permissions set: $DIR"
          DIR_OK=1
        else
          echo "[acl_check] ERROR: Could not set ownership on $DIR" >&2
          HAS_ISSUES=1
        fi
      else
        echo "[acl_check] ✓ Directory created: $DIR"
        DIR_OK=1
      fi
    else
      echo "[acl_check] ERROR: Could not create $DIR" >&2
      HAS_ISSUES=1
    fi
    [[ $DIR_OK -eq 1 ]] && continue
  fi

  # Check if writable
  if [[ ! -w "$DIR" ]]; then
    echo "[acl_check] ERROR: $DIR is not writable by current user" >&2
    
    # Check if we're running as the container UID already
    if [[ $(id -u) -eq $CONTAINER_UID ]]; then
      echo "[acl_check] ERROR: Running as UID $CONTAINER_UID but $DIR is not writable" >&2
      echo "[acl_check] Check parent directory permissions" >&2
      HAS_ISSUES=1
      continue
    fi
    
    # Offer to fix
    echo "[acl_check] You may need to run:" >&2
    echo "  sudo chown -R $CONTAINER_UID:$CONTAINER_GID $DIR" >&2
    read -p "[acl_check] Attempt to fix permissions now? (requires sudo) [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
      if sudo chown -R "$CONTAINER_UID:$CONTAINER_GID" "$DIR"; then
        echo "[acl_check] ✓ Permissions updated for $DIR"
        # Check if writable now
        if [[ -w "$DIR" ]]; then
          echo "[acl_check] ✓ $DIR is now writable"
        else
          echo "[acl_check] ERROR: $DIR still not writable after permission change" >&2
          HAS_ISSUES=1
        fi
      else
        echo "[acl_check] ERROR: Failed to update permissions for $DIR" >&2
        HAS_ISSUES=1
      fi
    else
      echo "[acl_check] ERROR: Cannot proceed without fixing permissions for $DIR" >&2
      HAS_ISSUES=1
    fi
  else
    echo "[acl_check] ✓ $DIR is writable"
  fi
done

if [[ $HAS_ISSUES -ne 0 ]]; then
  echo "[acl_check] ERROR: Permission issues detected - cannot proceed" >&2
  exit 1
fi

exit 0
