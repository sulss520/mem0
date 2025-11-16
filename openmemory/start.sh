#!/bin/bash

# OpenMemory 启动脚本
# 用于启动 mem0-other 版本的 OpenMemory 服务

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  OpenMemory 启动脚本${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    echo "请先安装 Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

# 检查 Docker Compose
if ! docker compose version &> /dev/null; then
    echo -e "${RED}错误: Docker Compose 未安装${NC}"
    echo "请先安装 Docker Compose V2"
    exit 1
fi

# 检查 .env 文件
if [ ! -f "api/.env" ]; then
    echo -e "${YELLOW}警告: api/.env 文件不存在${NC}"
    echo "正在从示例文件创建..."
    if [ -f "api/.env.example" ]; then
        cp api/.env.example api/.env
        echo -e "${YELLOW}请编辑 api/.env 文件，设置 OPENAI_API_KEY 和其他配置${NC}"
        exit 1
    else
        echo -e "${RED}错误: api/.env.example 文件也不存在${NC}"
        exit 1
    fi
fi

# 检查必要的环境变量
if ! grep -q "OPENAI_API_KEY=sk-" api/.env 2>/dev/null || grep -q "OPENAI_API_KEY=sk-xxx" api/.env 2>/dev/null; then
    echo -e "${YELLOW}警告: 请在 api/.env 中设置有效的 OPENAI_API_KEY${NC}"
    echo "当前配置可能无效，请检查 api/.env 文件"
    read -p "是否继续启动? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 读取 USER 配置
USER_ID=$(grep "^USER=" api/.env 2>/dev/null | cut -d'=' -f2 || echo "$(whoami)")
export USER="$USER_ID"

# 读取 API 端口
API_PORT=$(grep "^API_PORT=" api/.env 2>/dev/null | cut -d'=' -f2 || echo "8765")
export API_PORT

# 读取 UI 端口
UI_PORT=$(grep "^UI_PORT=" api/.env 2>/dev/null | cut -d'=' -f2 || echo "3000")
export UI_PORT

# 设置 UI 环境变量
export NEXT_PUBLIC_API_URL="http://localhost:${API_PORT}"
export NEXT_PUBLIC_USER_ID="$USER_ID"

echo -e "${GREEN}配置信息:${NC}"
echo -e "  USER_ID: ${USER_ID}"
echo -e "  API 端口: ${API_PORT}"
echo -e "  UI 端口: ${UI_PORT}"
echo -e "  API URL: ${NEXT_PUBLIC_API_URL}"
echo ""

# 检查端口占用
check_port() {
    local port=$1
    local service=$2
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "${YELLOW}警告: 端口 $port ($service) 已被占用${NC}"
        read -p "是否继续? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

check_port $API_PORT "API"
check_port $UI_PORT "UI"

# 停止现有容器
echo -e "${BLUE}停止现有容器...${NC}"
docker compose down 2>/dev/null || true

# 构建镜像（如果需要）
if [ "${1:-}" = "--build" ] || [ "${1:-}" = "-b" ]; then
    echo -e "${BLUE}构建 Docker 镜像...${NC}"
    docker compose build
fi

# 启动服务
echo -e "${BLUE}启动 OpenMemory 服务...${NC}"
docker compose up -d

# 等待服务启动
echo -e "${BLUE}等待服务启动...${NC}"
sleep 5

# 检查 API 健康状态
echo -e "${BLUE}检查 API 健康状态...${NC}"
for i in {1..30}; do
    if curl -fsS "http://localhost:${API_PORT}/api/v1/health" >/dev/null 2>&1 || \
       curl -fsS "http://localhost:${API_PORT}/docs" >/dev/null 2>&1; then
        echo -e "${GREEN}API 服务已启动${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${YELLOW}警告: API 服务可能未完全启动，请检查日志${NC}"
    fi
    sleep 2
done

# 显示服务状态
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OpenMemory 服务已启动${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  ${GREEN}API 服务:${NC}  http://localhost:${API_PORT}"
echo -e "  ${GREEN}API 文档:${NC}  http://localhost:${API_PORT}/docs"
echo -e "  ${GREEN}UI 服务:${NC}   http://localhost:${UI_PORT}"
echo ""
echo -e "查看日志: ${BLUE}docker compose logs -f${NC}"
echo -e "停止服务: ${BLUE}docker compose down${NC}"
echo -e "重启服务: ${BLUE}./start.sh${NC}"
echo ""

# 询问是否打开浏览器
if command -v open > /dev/null; then
    read -p "是否在浏览器中打开 UI? (Y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        open "http://localhost:${UI_PORT}" 2>/dev/null || true
    fi
fi

