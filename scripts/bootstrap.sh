#!/bin/sh
# Entrypoint wrapper for the render-tools image.
#
# Runs as root (PID-1 child of tini). On every boot it:
#   1. Ensures /opt/data exists and is owned by hermes:hermes.
#   2. Synchronizes OPENAI_API_KEY from Render Environment into /opt/data/.env.
#      This removes stale keys previously saved by the Hermes dashboard.
#   3. Runs the config patcher as the hermes user. The patcher is
#      idempotent: it inserts Render MCP/skill entries and repairs the
#      Alumínio JR OpenAI-compatible model block.
#   4. Exec's the upstream entrypoint chain with the original args
#      (default CMD is `gateway run`).
#
# The upstream entrypoint also chowns /opt/data and drops to the hermes
# user via gosu for the gateway process. Our chown here is redundant in
# the happy path but harmless, and it lets the patcher run on a fresh
# disk that hasn't been chowned yet.

set -eu

DATA_DIR="${HERMES_HOME:-/opt/data}"
PATCHER="/opt/render-tools/patch-config.py"

truthy() {
  case "$(printf '%s' "${1:-1}" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off|"") return 1 ;;
    *) return 0 ;;
  esac
}

sync_openai_key_to_disk_env() {
  # Hermes' dashboard also writes provider keys to ${DATA_DIR}/.env.
  # If an old OPENAI_API_KEY is saved there, some Hermes/dotenv paths can
  # keep using it even after the Render Environment variable is changed.
  #
  # For this Alumínio JR template, Render Environment is the source of truth:
  # when OPENAI_API_KEY exists in the process environment, rewrite the disk
  # .env entry to the same value on every boot. We never print the key.
  if ! truthy "${ALUMINIO_JR_SYNC_OPENAI_ENV_TO_DISK:-1}"; then
    return 0
  fi

  if [ -z "${OPENAI_API_KEY:-}" ]; then
    if [ -f "${DATA_DIR}/.env" ] && grep -q '^OPENAI_API_KEY=' "${DATA_DIR}/.env" 2>/dev/null; then
      echo "[render-tools] warning: OPENAI_API_KEY exists in ${DATA_DIR}/.env but not in Render Environment" >&2
    else
      echo "[render-tools] warning: OPENAI_API_KEY is not set in Render Environment" >&2
    fi
    return 0
  fi

  ENV_FILE="${DATA_DIR}/.env"
  TMP_FILE="${DATA_DIR}/.env.render-tools.tmp"

  touch "${ENV_FILE}"

  # Remove any old OPENAI_API_KEY lines, then append the current Render env value.
  # Use awk instead of sed -i for portability and to avoid leaving partial files.
  awk 'BEGIN{removed=0} !/^OPENAI_API_KEY=/{print} /^OPENAI_API_KEY=/{removed=1} END{}' "${ENV_FILE}" > "${TMP_FILE}"
  printf 'OPENAI_API_KEY=%s\n' "${OPENAI_API_KEY}" >> "${TMP_FILE}"
  mv "${TMP_FILE}" "${ENV_FILE}"

  if ! chown hermes:hermes "${ENV_FILE}" 2>/dev/null; then
    true
  fi

  echo "[render-tools] synced OPENAI_API_KEY from Render Environment into ${ENV_FILE}"
}

# Make sure the data dir exists and the hermes user can write to it
# before we run the patcher. Idempotent — if /opt/data is already a
# mounted, chowned disk this is a no-op.
mkdir -p "${DATA_DIR}"
if ! chown -R hermes:hermes "${DATA_DIR}" 2>/dev/null; then
  echo "[render-tools] warning: could not chown ${DATA_DIR}; continuing" >&2
fi

sync_openai_key_to_disk_env

# Patch config.yaml. We never fail the boot on a patch error — the agent
# can still run without the Render MCP server registered, and the user
# can always add it manually from the dashboard.
if [ -x "${PATCHER}" ]; then
  if ! gosu hermes "${PATCHER}" "${DATA_DIR}/config.yaml"; then
    echo "[render-tools] warning: config patch failed; continuing with unmodified config" >&2
  fi
else
  echo "[render-tools] warning: ${PATCHER} not found or not executable; skipping" >&2
fi

# Optional Alumínio JR HTTP bridge mode. This is intentionally separate from
# the upstream OpenAI-compatible API server because some hosted deployments
# expose the dashboard/gateway but not /v1/chat/completions. In bridge mode,
# the public Render URL serves a tiny authenticated HTTP API that calls the
# Hermes CLI one request at a time.
if truthy "${HERMES_HTTP_BRIDGE_ENABLED:-0}"; then
  BRIDGE="/opt/render-tools/hermes_http_bridge.py"
  if [ ! -x "${BRIDGE}" ]; then
    echo "[render-tools] error: ${BRIDGE} not found or not executable" >&2
    exit 1
  fi
  echo "[render-tools] starting Alumínio JR Hermes HTTP bridge"
  exec gosu hermes /opt/hermes/.venv/bin/python "${BRIDGE}"
fi

# Hand off to the upstream entrypoint. The upstream script handles
# privilege drop, dashboard backgrounding, and the actual gateway exec.
exec /opt/hermes/docker/entrypoint.sh "$@"
