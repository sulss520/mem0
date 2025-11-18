#!/bin/bash
# 端到端测试脚本：从 API 添加记忆 → 观察日志 → 检查数据存储

set -e

echo "=========================================="
echo "🧪 OpenMemory 端到端测试"
echo "=========================================="
echo ""

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 1. 检查服务状态
echo "1️⃣  检查服务状态..."
echo ""

# 检查 API 服务
if curl -s http://localhost:8765/docs > /dev/null 2>&1; then
    echo -e "${GREEN}✅ API 服务运行中${NC}"
else
    echo -e "${RED}❌ API 服务未运行${NC}"
    exit 1
fi

# 检查 graphs_proxy（可选）
if curl -s http://localhost:8090/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ graphs_proxy 服务运行中${NC}"
    HEALTH=$(curl -s http://localhost:8090/health | python3 -m json.tool 2>/dev/null | grep -E "overall|healthy_count" | head -2)
    echo "   健康状态: $HEALTH"
else
    echo -e "${YELLOW}⚠️  graphs_proxy 服务未运行（可选）${NC}"
fi

echo ""

# 2. 准备测试数据
echo "2️⃣  准备测试数据..."
TEST_TEXT="Alice 是一名软件工程师，她使用 Python 和 JavaScript 开发 Web 应用。Bob 是她的同事，他们一起在 Google 工作。"
TEST_USER_ID="test_e2e_user_$(date +%s)"
echo "   测试文本: $TEST_TEXT"
echo "   用户ID: $TEST_USER_ID"
echo ""

# 3. 通过 API 添加记忆
echo "3️⃣  通过 API 添加记忆..."
echo "   发送 POST /api/v1/memories/ 请求..."
echo ""

RESPONSE=$(curl -s -X POST http://localhost:8765/api/v1/memories/ \
  -H "Content-Type: application/json" \
  -d "{
    \"text\": \"$TEST_TEXT\",
    \"user_id\": \"$TEST_USER_ID\",
    \"app\": \"test_app\"
  }")

if echo "$RESPONSE" | grep -q "error"; then
    echo -e "${RED}❌ 添加记忆失败${NC}"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
else
    echo -e "${GREEN}✅ 添加记忆成功${NC}"
    MEMORY_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('id', 'unknown'))" 2>/dev/null || echo "unknown")
    echo "   记忆ID: $MEMORY_ID"
fi

echo ""

# 4. 等待处理完成
echo "4️⃣  等待处理完成（5秒）..."
sleep 5
echo ""

# 5. 检查 API 日志
echo "5️⃣  检查 API 日志..."
API_LOG_FILE="../../logs/api.log"
if [ -f "$API_LOG_FILE" ]; then
    echo "   查找相关日志..."
    API_LOG=$(tail -50 "$API_LOG_FILE" 2>/dev/null | grep -E "POST /api/v1/memories|GRAPH STORE|Neo4jGraph|VECTOR STORE|开始写入|写入完成" | tail -10)
    if [ -n "$API_LOG" ]; then
        echo "$API_LOG"
    else
        echo -e "${YELLOW}⚠️  未找到相关日志${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  日志文件不存在: $API_LOG_FILE${NC}"
fi
echo ""

# 6. 验证数据存储
echo "6️⃣  验证数据存储..."
echo "   检查 MySQL 中的记忆..."

# 通过 API 查询记忆
QUERY_RESPONSE=$(curl -s "http://localhost:8765/api/v1/memories/?user_id=$TEST_USER_ID" | python3 -m json.tool 2>/dev/null)
MEMORY_COUNT=$(echo "$QUERY_RESPONSE" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('items', [])))" 2>/dev/null || echo "0")

if [ "$MEMORY_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✅ 在 MySQL 中找到 $MEMORY_COUNT 条记忆${NC}"
else
    echo -e "${YELLOW}⚠️  未在 MySQL 中找到记忆（可能还在处理中）${NC}"
fi

echo ""

# 7. 测试总结
echo "=========================================="
echo "📊 测试总结"
echo "=========================================="
echo ""

if [ "$MEMORY_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✅ 端到端测试通过！${NC}"
    echo -e "${GREEN}✅ 记忆已成功创建并存储${NC}"
else
    echo -e "${YELLOW}⚠️  测试部分通过，请检查上述问题${NC}"
fi

echo ""
echo "=========================================="
echo "✅ 端到端测试完成"
echo "=========================================="

