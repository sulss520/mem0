#!/bin/bash

# ============================================================================
# OpenMemory 服务管理脚本
# ============================================================================
# 主要功能：本地服务的启动、停止、重启、状态查看和日志管理
# Docker 部署：使用 ./admin.sh docker-deploy 命令（需要特殊指定）
# ============================================================================

set -e

# ============================================================================
# 配置变量
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

API_DIR="$SCRIPT_DIR/api"
UI_DIR="$SCRIPT_DIR/ui"
PID_DIR="$SCRIPT_DIR/.local_pids"
LOG_DIR="$SCRIPT_DIR/logs"
API_PID_FILE="$PID_DIR/api.pid"
UI_PID_FILE="$PID_DIR/ui.pid"
API_LOG_FILE="$LOG_DIR/api.log"
UI_LOG_FILE="$LOG_DIR/ui.log"

# 默认端口
DEFAULT_API_PORT=8765
DEFAULT_UI_PORT=3000

# ============================================================================
# 工具函数
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_debug() { echo -e "${BLUE}[DEBUG]${NC} $1"; }

# ============================================================================
# 环境检查
# ============================================================================
check_python() {
    if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
        log_error "未找到 Python，请先安装 Python 3"
        exit 1
    fi
    PYTHON_CMD=$(command -v python3 2>/dev/null || command -v python)
    log_info "使用 Python: $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"
}

check_node() {
    if ! command -v pnpm &> /dev/null && ! command -v npm &> /dev/null; then
        log_error "未找到 pnpm 或 npm，请先安装 Node.js"
        exit 1
    fi
    NODE_CMD=$(command -v pnpm 2>/dev/null || command -v npm)
    log_info "使用: $NODE_CMD"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "未找到 Docker，请先安装 Docker"
        exit 1
    fi
    if ! docker info > /dev/null 2>&1; then
        log_error "Docker 未运行，请先启动 Docker"
        exit 1
    fi
}

load_config() {
    local env_file="$API_DIR/.env"
    if [ -f "$env_file" ]; then
        API_PORT=$(grep -E "^API_PORT=" "$env_file" 2>/dev/null | head -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "$DEFAULT_API_PORT")
        UI_PORT=$(grep -E "^UI_PORT=" "$env_file" 2>/dev/null | head -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "$DEFAULT_UI_PORT")
        USER_ID=$(grep -E "^USER=" "$env_file" 2>/dev/null | head -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "$(whoami)")
    else
        API_PORT="$DEFAULT_API_PORT"
        UI_PORT="$DEFAULT_UI_PORT"
        USER_ID=$(whoami)
    fi
    
    export API_PORT="${API_PORT:-$DEFAULT_API_PORT}"
    export UI_PORT="${UI_PORT:-$DEFAULT_UI_PORT}"
    export USER="${USER_ID:-$(whoami)}"
    export NEXT_PUBLIC_API_URL="http://localhost:${API_PORT}"
    export NEXT_PUBLIC_USER_ID="$USER"
}

check_port() {
    local port=$1
    local service=$2
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        local pid=$(lsof -Pi :$port -sTCP:LISTEN -t | head -1)
        log_warn "端口 $port ($service) 被进程 $pid 占用"
        return 1
    fi
    return 0
}

is_running() {
    local pid_file=$1
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# ============================================================================
# 服务管理（本地）
# ============================================================================
start_service() {
    log_info "========================================="
    log_info "  启动 OpenMemory 服务（本地）"
    log_info "========================================="
    echo ""
    
    # 创建 PID 和日志目录
    mkdir -p "$PID_DIR"
    mkdir -p "$LOG_DIR"
    
    # 检查是否已运行
    if is_running "$API_PID_FILE" || is_running "$UI_PID_FILE"; then
        log_warn "服务已在运行中"
        log_info "使用 './admin.sh restart' 重启服务"
        return 1
    fi
    
    # 检查环境
    check_python
    check_node
    check_docker
    load_config
    
    # 检查端口
    check_port "$API_PORT" "API" || log_warn "API 端口被占用，可能无法启动"
    check_port "$UI_PORT" "UI" || log_warn "UI 端口被占用，可能无法启动"
    
    # 检查环境文件
    if [ ! -f "$API_DIR/.env" ]; then
        log_warn "api/.env 文件不存在"
        if [ -f "$API_DIR/.env.example" ]; then
            log_info "从示例文件创建 api/.env..."
            cp "$API_DIR/.env.example" "$API_DIR/.env"
            log_warn "请编辑 api/.env 文件，设置必要的配置（特别是 OPENAI_API_KEY）"
            return 1
        else
            log_error "api/.env.example 文件也不存在"
            return 1
        fi
    fi
    
    # 检查 API 虚拟环境
    local venv_dir="$API_DIR/venv"
    if [ ! -d "$venv_dir" ]; then
        log_warn "虚拟环境不存在: $venv_dir"
        log_info "正在创建虚拟环境..."
        python3 -m venv "$venv_dir"
        log_info "请先安装依赖: cd api && source venv/bin/activate && pip install -r requirements.txt"
        return 1
    fi
    
    # 检查 API 依赖
    if ! "$venv_dir/bin/python" -c "import fastapi" 2>/dev/null; then
        log_warn "API 依赖未安装"
        log_info "请先安装依赖: cd api && source venv/bin/activate && pip install -r requirements.txt"
        return 1
    fi
    
    # 检查 UI 依赖
    if [ ! -d "$UI_DIR/node_modules" ]; then
        log_warn "UI 依赖未安装"
        log_info "正在安装 UI 依赖..."
        cd "$UI_DIR"
        if command -v pnpm &> /dev/null; then
            pnpm install
        else
            npm install
        fi
        cd "$SCRIPT_DIR"
    fi
    
    # MySQL 是外部配置，不需要在这里启动
    log_info "注意：MySQL 需要外部配置，请确保 MySQL 服务已运行"
    
    # 启动 API 服务
    log_info "启动 API 服务..."
    cd "$API_DIR"
    export PYTHONPATH="$API_DIR${PYTHONPATH:+:$PYTHONPATH}"
    
    # 数据库 URL 配置（MySQL 是外部服务，需要在 .env 中配置正确的连接地址）
    if [ -f "$API_DIR/.env" ]; then
        local db_url=$(grep "^DATABASE_URL=" "$API_DIR/.env" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
        if [ -n "$db_url" ]; then
            export DATABASE_URL="$db_url"
            log_info "使用配置的数据库 URL"
        fi
    fi
    
    # 日志配置（服务级别，通过环境变量传递给 Python 服务）
    export LOG_LEVEL="${LOG_LEVEL:-INFO}"
    export LOG_OUTPUT="${LOG_OUTPUT:-file}"  # file, console, both
    export LOG_FILE="${LOG_FILE:-api.log}"
    # 日志目录使用绝对路径，相对于 API 目录
    export LOG_DIR="${LOG_DIR:-$API_DIR/logs}"
    export LOG_MAX_BYTES="${LOG_MAX_BYTES:-10485760}"  # 10MB
    export LOG_BACKUP_COUNT="${LOG_BACKUP_COUNT:-5}"
    
    log_info "日志配置:"
    log_info "  级别: $LOG_LEVEL"
    log_info "  输出: $LOG_OUTPUT"
    if [ "$LOG_OUTPUT" = "file" ] || [ "$LOG_OUTPUT" = "both" ]; then
        log_info "  文件: $LOG_DIR/$LOG_FILE"
    fi
    
    # 服务自己处理日志输出，不需要重定向
    "$venv_dir/bin/python" "$API_DIR/main.py" &
    local api_pid=$!
    echo "$api_pid" > "$API_PID_FILE"
    log_info "API 服务已启动 (PID: $api_pid)"
    
    # 等待 API 启动
    log_info "等待 API 服务启动..."
    sleep 3
    
    # 启动 UI 服务
    log_info "启动 UI 服务..."
    cd "$UI_DIR"
    export NEXT_PUBLIC_API_URL="http://localhost:${API_PORT}"
    export NEXT_PUBLIC_USER_ID="$USER"
    
    # UI 日志配置（Node.js 使用简单的重定向，因为 Next.js 的日志管理不同）
    if command -v pnpm &> /dev/null; then
        pnpm dev > "$UI_LOG_FILE" 2>&1 &
    else
        npm run dev > "$UI_LOG_FILE" 2>&1 &
    fi
    local ui_pid=$!
    echo "$ui_pid" > "$UI_PID_FILE"
    log_info "UI 服务已启动 (PID: $ui_pid)"
    
    cd "$SCRIPT_DIR"
    
    # 等待服务启动
    log_info "等待服务完全启动..."
    sleep 5
    
    log_info ""
    log_info "✅ 服务启动成功！"
    log_info "  API: http://localhost:${API_PORT}"
    log_info "  UI:  http://localhost:${UI_PORT}"
    log_info ""
    log_info "日志配置:"
    if [ "$LOG_OUTPUT" = "file" ] || [ "$LOG_OUTPUT" = "both" ]; then
        log_info "  API: $LOG_DIR/$LOG_FILE (日志轮转，最大 ${LOG_MAX_BYTES} 字节，保留 ${LOG_BACKUP_COUNT} 个备份)"
    else
        log_info "  API: 控制台输出"
    fi
    log_info "  UI:  $UI_LOG_FILE"
    log_info ""
    log_info "查看日志: ./admin.sh logs"
    log_info "停止服务: ./admin.sh stop"
}

stop_service() {
    log_info "========================================="
    log_info "  停止 OpenMemory 服务"
    log_info "========================================="
    echo ""
    
    local stopped=0
    
    # 停止 API
    if is_running "$API_PID_FILE"; then
        local pid=$(cat "$API_PID_FILE" 2>/dev/null)
        log_info "停止 API 服务 (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        stopped=$((stopped + 1))
        rm -f "$API_PID_FILE"
    fi
    
    # 停止 UI
    if is_running "$UI_PID_FILE"; then
        local pid=$(cat "$UI_PID_FILE" 2>/dev/null)
        log_info "停止 UI 服务 (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
        stopped=$((stopped + 1))
        rm -f "$UI_PID_FILE"
    fi
    
    # 通过进程名查找并停止
    local api_processes=$(ps aux | grep -E "[p]ython.*main\.py|[u]vicorn.*main" | awk '{print $2}')
    if [ -n "$api_processes" ]; then
        for pid in $api_processes; do
            [ -n "$pid" ] && kill "$pid" 2>/dev/null && stopped=$((stopped + 1)) || true
        done
    fi
    
    local ui_processes=$(ps aux | grep -E "[n]ext.*dev" | awk '{print $2}')
    if [ -n "$ui_processes" ]; then
        for pid in $ui_processes; do
            [ -n "$pid" ] && kill "$pid" 2>/dev/null && stopped=$((stopped + 1)) || true
        done
    fi
    
    sleep 2
    
    if [ $stopped -eq 0 ]; then
        log_info "没有运行中的服务"
    else
        log_info "✅ 服务已停止"
    fi
}

restart_service() {
    log_info "========================================="
    log_info "  重启 OpenMemory 服务"
    log_info "========================================="
    echo ""
    
    if is_running "$API_PID_FILE" || is_running "$UI_PID_FILE"; then
        stop_service
        echo ""
        sleep 2
    fi
    
    start_service
}

status_service() {
    log_info "========================================="
    log_info "  OpenMemory 服务状态"
    log_info "========================================="
    echo ""
    
    load_config
    
    # API 状态
    if is_running "$API_PID_FILE"; then
        local pid=$(cat "$API_PID_FILE" 2>/dev/null)
        log_info "API 服务: ${GREEN}运行中${NC} (PID: $pid)"
        log_info "  地址: http://localhost:${API_PORT}"
    else
        log_info "API 服务: ${RED}未运行${NC}"
        rm -f "$API_PID_FILE"
    fi
    
    # UI 状态
    if is_running "$UI_PID_FILE"; then
        local pid=$(cat "$UI_PID_FILE" 2>/dev/null)
        log_info "UI 服务:  ${GREEN}运行中${NC} (PID: $pid)"
        log_info "  地址: http://localhost:${UI_PORT}"
    else
        log_info "UI 服务:  ${RED}未运行${NC}"
        rm -f "$UI_PID_FILE"
    fi
    
    # MySQL 状态（外部配置）
    echo ""
    log_info "MySQL 服务: ${YELLOW}外部配置${NC}"
    log_info "  请确保 MySQL 服务已运行并配置正确"
    
    # 日志文件
    echo ""
    log_info "日志文件:"
    local api_log_dir="${LOG_DIR:-$API_DIR/logs}"
    local api_log_file="${LOG_FILE:-api.log}"
    local api_log_path="$api_log_dir/$api_log_file"
    
    if [ -f "$api_log_path" ]; then
        log_info "  API: $api_log_path ($(du -h "$api_log_path" 2>/dev/null | cut -f1))"
    elif [ -f "$API_LOG_FILE" ]; then
        log_info "  API: $API_LOG_FILE ($(du -h "$API_LOG_FILE" 2>/dev/null | cut -f1))"
    else
        log_info "  API: $api_log_path (不存在)"
    fi
    
    [ -f "$UI_LOG_FILE" ] && log_info "  UI:  $UI_LOG_FILE ($(du -h "$UI_LOG_FILE" 2>/dev/null | cut -f1))" || log_info "  UI:  $UI_LOG_FILE (不存在)"
}

# ============================================================================
# 日志管理
# ============================================================================
view_logs() {
    local service="${1:-all}"
    local lines="${2:-50}"
    
    log_info "========================================="
    log_info "  查看服务日志 (最后 $lines 行)"
    log_info "========================================="
    echo ""
    
    # 加载日志配置（使用环境变量或默认值）
    local api_log_dir="${LOG_DIR:-$API_DIR/logs}"
    local api_log_file="${LOG_FILE:-api.log}"
    local api_log_path="$api_log_dir/$api_log_file"
    
    # 确保日志目录存在
    mkdir -p "$api_log_dir"
    
    case "$service" in
        api|API)
            if [ -f "$api_log_path" ]; then
                tail -n "$lines" "$api_log_path"
            elif [ -f "$API_LOG_FILE" ]; then
                # 兼容旧路径
                tail -n "$lines" "$API_LOG_FILE"
            else
                log_warn "API 日志文件不存在: $api_log_path"
            fi
            ;;
        ui|UI)
            [ -f "$UI_LOG_FILE" ] && tail -n "$lines" "$UI_LOG_FILE" || log_warn "UI 日志文件不存在: $UI_LOG_FILE"
            ;;
        all|*)
            if [ -f "$api_log_path" ] || [ -f "$API_LOG_FILE" ]; then
                log_info "=== API 日志 ==="
                if [ -f "$api_log_path" ]; then
                    tail -n "$lines" "$api_log_path"
                else
                    tail -n "$lines" "$API_LOG_FILE"
                fi
                echo ""
            fi
            if [ -f "$UI_LOG_FILE" ]; then
                log_info "=== UI 日志 ==="
                tail -n "$lines" "$UI_LOG_FILE"
            fi
            if [ ! -f "$api_log_path" ] && [ ! -f "$API_LOG_FILE" ] && [ ! -f "$UI_LOG_FILE" ]; then
                log_warn "没有找到日志文件"
                log_info "  API 日志目录: $api_log_dir"
                log_info "  UI 日志目录: $LOG_DIR"
            fi
            ;;
    esac
    
    echo ""
    log_info "实时查看: ./admin.sh follow [api|ui]"
}

follow_logs() {
    local service="${1:-all}"
    
    log_info "========================================="
    log_info "  实时查看服务日志 (Ctrl+C 退出)"
    log_info "========================================="
    echo ""
    
    # 加载日志配置（使用环境变量或默认值）
    local api_log_dir="${LOG_DIR:-$API_DIR/logs}"
    local api_log_file="${LOG_FILE:-api.log}"
    local api_log_path="$api_log_dir/$api_log_file"
    
    # 确保日志目录存在
    mkdir -p "$api_log_dir"
    
    case "$service" in
        api|API)
            if [ -f "$api_log_path" ]; then
                tail -f "$api_log_path"
            elif [ -f "$API_LOG_FILE" ]; then
                # 兼容旧路径
                tail -f "$API_LOG_FILE"
            else
                log_warn "API 日志文件不存在: $api_log_path"
            fi
            ;;
        ui|UI)
            [ -f "$UI_LOG_FILE" ] && tail -f "$UI_LOG_FILE" || log_warn "UI 日志文件不存在: $UI_LOG_FILE"
            ;;
        all|*)
            local api_log=""
            if [ -f "$api_log_path" ]; then
                api_log="$api_log_path"
            elif [ -f "$API_LOG_FILE" ]; then
                api_log="$API_LOG_FILE"
            fi
            
            if [ -n "$api_log" ] && [ -f "$UI_LOG_FILE" ]; then
                tail -f "$api_log" "$UI_LOG_FILE" 2>/dev/null || {
                    log_info "=== API 日志 ==="
                    tail -f "$api_log" &
                    log_info "=== UI 日志 ==="
                    tail -f "$UI_LOG_FILE"
                }
            elif [ -n "$api_log" ]; then
                tail -f "$api_log"
            elif [ -f "$UI_LOG_FILE" ]; then
                tail -f "$UI_LOG_FILE"
            else
                log_warn "没有找到日志文件"
                log_info "  API 日志目录: $api_log_dir"
                log_info "  UI 日志目录: $LOG_DIR"
            fi
            ;;
    esac
}

# ============================================================================
# Docker 部署（需要特殊指定）
# ============================================================================
docker_deploy() {
    log_info "========================================="
    log_info "  Docker 部署 OpenMemory"
    log_info "========================================="
    log_warn "注意：这是 Docker 部署模式，与本地运行模式不同"
    echo ""
    
    check_docker
    
    # 检查 docker-compose.yml
    if [ ! -f "$SCRIPT_DIR/docker-compose.yml" ]; then
        log_error "docker-compose.yml 文件不存在"
        exit 1
    fi
    
    # 检查环境文件
    if [ ! -f "$API_DIR/.env" ]; then
        log_warn "api/.env 文件不存在"
        if [ -f "$API_DIR/.env.example" ]; then
            log_info "从示例文件创建 api/.env..."
            cp "$API_DIR/.env.example" "$API_DIR/.env"
            log_warn "请编辑 api/.env 文件设置必要的配置"
            read -p "按 Enter 继续..."
        else
            log_error "api/.env.example 文件也不存在"
            exit 1
        fi
    fi
    
    # 构建和启动
    log_info "构建 Docker 镜像..."
    docker-compose build || docker compose build
    
    log_info ""
    log_info "启动服务..."
    docker-compose up -d || docker compose up -d
    
    log_info ""
    log_info "等待服务启动..."
    sleep 5
    
    log_info ""
    log_info "服务状态:"
    docker-compose ps || docker compose ps
    
    load_config
    log_info ""
    log_info "✅ Docker 部署完成！"
    log_info "  API: http://localhost:${API_PORT}"
    log_info "  UI:  http://localhost:${UI_PORT}"
    log_info "查看日志: docker-compose logs -f"
}

# ============================================================================
# 帮助信息
# ============================================================================
show_help() {
    echo "用法: $0 {命令} [参数]"
    echo ""
    echo "本地服务管理（主要功能）:"
    echo "  start         启动服务（后台运行，API + UI 本地，MySQL 需外部配置）"
    echo "  stop          停止服务"
    echo "  restart       重启服务"
    echo "  status        查看服务状态"
    echo "  logs [服务]   查看日志（api/ui/all，默认 all）"
    echo "  follow [服务] 实时查看日志（api/ui/all，默认 all）"
    echo ""
    echo "Docker 部署（需要特殊指定）:"
    echo "  docker-deploy Docker 部署服务（API + UI）"
    echo ""
    echo "示例:"
    echo "  ./admin.sh start              # 启动服务（本地模式）"
    echo "  ./admin.sh stop                # 停止服务"
    echo "  ./admin.sh logs api            # 查看 API 日志"
    echo "  ./admin.sh docker-deploy      # Docker 部署"
}

# ============================================================================
# 主函数
# ============================================================================
main() {
    case "${1:-help}" in
        start)
            start_service
            ;;
        stop)
            stop_service
            ;;
        restart)
            restart_service
            ;;
        status)
            status_service
            ;;
        logs)
            view_logs "${2:-all}" "${3:-50}"
            ;;
        follow)
            follow_logs "${2:-all}"
            ;;
        docker-deploy)
            docker_deploy
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知命令: $1"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

main "$@"
