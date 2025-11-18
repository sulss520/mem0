#!/usr/bin/env bash

# admin.sh - 项目管理脚本
# 功能：
#   - 支持 conda 或 venv 环境（优先检测 conda）
#   - 安装/更新依赖
#   - 启动/停止/重启 API（使用 uvicorn）
#   - 查看运行状态与日志
#   - 生成 .env（从 .env.example）
#   - 帮助信息

set -euo pipefail
IFS=$'\n\t'

# 配置：可根据需要调整
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$PROJECT_ROOT/openmemory/api"
PYTHON_MODULE="openmemory.api.main:app"
HOST="0.0.0.0"
PORT="8765"
UVICORN_WORKERS=1
PIDFILE="$PROJECT_ROOT/.admin_api.pid"
LOGFILE="$PROJECT_ROOT/.admin_api.log"
REQUIREMENTS="$PROJECT_ROOT/requirements.txt"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  help                Show this help
  env [conda|venv]    Setup and activate environment (prints activation instructions)
  install             Install Python dependencies (pip)
  gen-env             Generate .env from .env.example if missing
  start               Start the API (foreground)
  start-bg            Start the API in background (writes PID to $PIDFILE)
  stop                Stop the background API process (by PID file)
  restart             Restart the API (stop + start-bg)
  status              Show whether API is running
  logs [--tail N]     Show logs (default last 200 lines). Use --tail 0 to follow

Examples:
  $(basename "$0") env conda
  $(basename "$0") install
  $(basename "$0") start-bg
  $(basename "$0") logs --tail 100
EOF
}

# Detect conda
detect_conda() {
  if command -v conda >/dev/null 2>&1; then
    # check if activated or not
    if [[ -n "${CONDA_PREFIX-}" ]]; then
      echo "conda"
      return
    fi
  fi
  echo ""
}

# Print activation instructions
env_setup() {
  mode=${1:-}
  if [[ "$mode" == "conda" ]]; then
    if ! command -v conda >/dev/null 2>&1; then
      echo -e "${YELLOW}conda not found in PATH.${NC}"
      echo "Please install Miniconda/Anaconda or use 'env venv' to create a virtualenv."
      return 1
    fi
    env_name="mem0-dev"
    echo -e "${BLUE}Checking for conda env '$env_name'...${NC}"
    if conda env list | awk '{print $1}' | grep -qx "$env_name"; then
      echo -e "${GREEN}Conda env '$env_name' exists.${NC}"
    else
      echo -e "${YELLOW}Creating conda env '$env_name' with python=3.11...${NC}"
      conda create -y -n "$env_name" python=3.11
    fi
    echo
    echo "To activate the conda env run:"
    echo -e "  ${GREEN}conda activate $env_name${NC}"
    echo "Then you can run: ./admin.sh install && ./admin.sh start"
  else
    # venv
    echo -e "${BLUE}Setting up venv in $PROJECT_ROOT/venv ...${NC}"
    if [[ ! -d "$PROJECT_ROOT/venv" ]]; then
      python3 -m venv "$PROJECT_ROOT/venv"
      echo -e "${GREEN}venv created at $PROJECT_ROOT/venv${NC}"
    else
      echo -e "${GREEN}venv already exists.${NC}"
    fi
    echo
    echo "To activate the venv run:"
    echo -e "  ${GREEN}source $PROJECT_ROOT/venv/bin/activate${NC}"
    echo "Then you can run: ./admin.sh install && ./admin.sh start"
  fi
}

install_deps() {
  if [[ ! -f "$REQUIREMENTS" ]]; then
    echo -e "${YELLOW}Requirements file not found at $REQUIREMENTS. Skipping.${NC}"
    return 0
  fi
  echo -e "${BLUE}Installing dependencies from $REQUIREMENTS ...${NC}"
  pip install -r "$REQUIREMENTS"
  echo -e "${GREEN}Dependencies installed.${NC}"
}

gen_env() {
  if [[ -f "$ENV_FILE" ]]; then
    echo -e "${YELLOW}.env already exists at $ENV_FILE${NC}"
    return 0
  fi
  if [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo -e "${GREEN}.env created from .env.example. Please edit it: $ENV_FILE${NC}"
  else
    echo -e "${YELLOW}Neither .env nor .env.example found. Skipping.${NC}"
  fi
}

start_fg() {
  echo -e "${BLUE}Starting API in foreground... (ctrl+c to stop)${NC}"
  cd "$API_DIR"
  # Use module string if available
  uvicorn $PYTHON_MODULE --host $HOST --port $PORT --reload
}

start_bg() {
  if [[ -f "$PIDFILE" ]]; then
    pid=$(cat "$PIDFILE" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo -e "${YELLOW}API already running with PID $pid${NC}"
      return 0
    else
      rm -f "$PIDFILE"
    fi
  fi
  echo -e "${BLUE}Starting API in background...${NC}"
  cd "$API_DIR"
  # redirect stdout/stderr to logfile
  nohup uvicorn $PYTHON_MODULE --host $HOST --port $PORT --reload --workers $UVICORN_WORKERS >"$LOGFILE" 2>&1 &
  pid=$!
  echo "$pid" > "$PIDFILE"
  echo -e "${GREEN}API started (PID: $pid). Logs -> $LOGFILE${NC}"
}

stop_bg() {
  if [[ ! -f "$PIDFILE" ]]; then
    echo -e "${YELLOW}PID file not found. Is the service running?${NC}"
    return 1
  fi
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  if [[ -z "$pid" ]]; then
    echo -e "${YELLOW}PID file empty. Removing.${NC}"
    rm -f "$PIDFILE"
    return 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo -e "${BLUE}Stopping PID $pid ...${NC}"
    kill "$pid"
    # wait for process to exit
    for i in {1..10}; do
      if kill -0 "$pid" 2>/dev/null; then
        sleep 0.5
      else
        break
      fi
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo -e "${YELLOW}PID still alive, sending SIGKILL...${NC}"
      kill -9 "$pid" || true
    fi
    echo -e "${GREEN}Stopped.${NC}"
  else
    echo -e "${YELLOW}No process with PID $pid. Removing stale PID file.${NC}"
  fi
  rm -f "$PIDFILE"
}

status() {
  if [[ -f "$PIDFILE" ]]; then
    pid=$(cat "$PIDFILE" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo -e "${GREEN}API running (PID: $pid)${NC}"
      return 0
    fi
  fi
  # fallback: check if uvicorn is listening on port
  if command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"$PORT" -sTCP:LISTEN -P -n | grep -q uvicorn; then
      echo -e "${GREEN}API appears running (uvicorn listening on port $PORT)${NC}"
      return 0
    fi
  fi
  echo -e "${YELLOW}API not running${NC}"
  return 1
}

show_logs() {
  tail_arg="-n 200"
  follow=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tail)
        shift
        tail_count=${1:-200}
        if [[ "$tail_count" == "0" ]]; then
          follow="-f"
          tail_arg="-n +1"
        else
          tail_arg="-n $tail_count"
        fi
        ;;
      *)
        ;;
    esac
    shift || true
  done
  if [[ ! -f "$LOGFILE" ]]; then
    echo -e "${YELLOW}Logfile $LOGFILE not found.${NC}"
    return 1
  fi
  if [[ -n "$follow" ]]; then
    tail -f "$LOGFILE"
  else
    tail $tail_arg "$LOGFILE"
  fi
}

# main
if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

cmd="$1"
shift || true
case "$cmd" in
  help|-h|--help)
    usage
    ;;
  env)
    env_setup "$1" || true
    ;;
  install)
    install_deps
    ;;
  gen-env)
    gen_env
    ;;
  start)
    start_fg
    ;;
  start-bg)
    start_bg
    ;;
  stop)
    stop_bg
    ;;
  restart)
    stop_bg || true
    start_bg
    ;;
  status)
    status
    ;;
  logs)
    show_logs "$@"
    ;;
  *)
    echo -e "${RED}Unknown command: $cmd${NC}\n"
    usage
    exit 2
    ;;
esac

