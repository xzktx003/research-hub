#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
ENV_FILE="${RESEARCH_HUB_ENV_FILE:-${PROJECT_ROOT}/config/service.env}"
ENV_FILES=("${PROJECT_ROOT}/.env" "${PROJECT_ROOT}/config/service.env")
if [[ "${RESEARCH_HUB_ENV_FILE:-}" != "" ]]; then
  ENV_FILES=("${RESEARCH_HUB_ENV_FILE}")
fi
for candidate in "${ENV_FILES[@]}"; do
  if [[ -f "${candidate}" ]]; then
    set -a
    source "${candidate}"
    set +a
  fi
done
PYTHON_BIN="${RESEARCH_HUB_PYTHON:-$(command -v python3.11 || command -v python3)}"
MINERU_ROOT="${MINERU_ROOT:-${WORKSPACE_ROOT}/mineru_service/project/MinerU}"
MINERU_MODEL_ROOT="${MINERU_MODEL_ROOT:-${WORKSPACE_ROOT}/mineru_service/model_weight}"
MINERU_PYTHON="${MINERU_PYTHON:-${MINERU_ROOT}/.venv/bin/python}"
LOG_ROOT="${RESEARCH_HUB_LOG_DIR:-${PROJECT_ROOT}/logs}"

mkdir -p "${LOG_ROOT}" "${PROJECT_ROOT}/artifacts" "${PROJECT_ROOT}/exports"

export RESEARCH_HUB_HOST="${RESEARCH_HUB_HOST:-0.0.0.0}"
export RESEARCH_HUB_PORT="${RESEARCH_HUB_PORT:-8311}"
export RESEARCH_HUB_DB="${RESEARCH_HUB_DB:-${PROJECT_ROOT}/config/research_hub.sqlite3}"
export RESEARCH_HUB_RUNTIME_CONFIG="${RESEARCH_HUB_RUNTIME_CONFIG:-${PROJECT_ROOT}/config/runtime_config.json}"
export RESEARCH_HUB_ARTIFACT_ROOT="${RESEARCH_HUB_ARTIFACT_ROOT:-${PROJECT_ROOT}/artifacts}"
export RESEARCH_HUB_EXPORT_DIR="${RESEARCH_HUB_EXPORT_DIR:-${PROJECT_ROOT}/exports}"
export PATENT_DISCLOSURE_ROOT="${PATENT_DISCLOSURE_ROOT:-${WORKSPACE_ROOT}/patent-disclosure-skill}"
export MINERU_BASE_URL="${MINERU_BASE_URL:-http://127.0.0.1:8000}"

children=()

stop_children() {
  local child
  for child in "${children[@]:-}"; do
    kill "${child}" 2>/dev/null || true
  done
  wait "${children[@]:-}" 2>/dev/null || true
}
trap stop_children EXIT INT TERM

if [[ "${MINERU_ENABLED:-1}" == "1" ]]; then
  if [[ ! -x "${MINERU_PYTHON}" ]]; then
    echo "MinerU Python is unavailable: ${MINERU_PYTHON}" >&2
    exit 1
  fi
  export MINERU_TOOLS_CONFIG_JSON="${MINERU_TOOLS_CONFIG_JSON:-${MINERU_MODEL_ROOT}/mineru.json}"
  export MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-modelscope}"
  export HF_HOME="${HF_HOME:-${MINERU_MODEL_ROOT}/huggingface}"
  export HF_HUB_CACHE="${HF_HUB_CACHE:-${HF_HOME}/hub}"
  export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-${MINERU_MODEL_ROOT}/modelscope}"
  export TORCH_HOME="${TORCH_HOME:-${MINERU_MODEL_ROOT}/torch}"
  export UV_CACHE_DIR="${UV_CACHE_DIR:-${MINERU_MODEL_ROOT}/uv-cache}"
  (
    unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
    export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}"
    export no_proxy="${NO_PROXY}"
    cd "${MINERU_ROOT}"
    exec "${MINERU_PYTHON}" -m mineru.cli.router \
      --host 127.0.0.1 \
      --port 8000 \
      --local-gpus "${MINERU_LOCAL_GPUS:-auto}"
  ) >>"${LOG_ROOT}/mineru.log" 2>&1 &
  children+=("$!")
fi

if [[ "${RESEARCH_HUB_API_ENABLED:-1}" == "1" ]]; then
  (
    cd "${PROJECT_ROOT}"
    exec "${PYTHON_BIN}" -m uvicorn research_hub.app:app \
      --host "${RESEARCH_HUB_HOST}" \
      --port "${RESEARCH_HUB_PORT}"
  ) >>"${LOG_ROOT}/api.log" 2>&1 &
  children+=("$!")
fi

(
  cd "${PROJECT_ROOT}"
  exec "${PYTHON_BIN}" scripts/scheduler.py worker \
    --interval "${RESEARCH_HUB_WORKER_INTERVAL:-30}"
) >>"${LOG_ROOT}/worker.log" 2>&1 &
children+=("$!")

echo "Research Hub stack started: API=${RESEARCH_HUB_PORT}, MinerU=${MINERU_BASE_URL}, children=${children[*]}"
wait -n "${children[@]}"
echo "A local stack process exited; stopping remaining services." >&2
exit 1