#!/bin/bash
# 完整的端到端测试：创建用户 → 添加记忆 → 观察日志 → 检查数据存储

set -e

echo "=========================================="
echo "🧪 Neo4j 双写功能完整端到端测试"
echo "=========================================="
echo ""

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

API_URL="http://localhost:8765"
PROXY_URL="http://localhost:8090"

# 1. 检查服务状态
echo "1️⃣  检查服务状态..."
echo ""

# 检查 API 服务
if curl -s "$API_URL/docs" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ API 服务运行中${NC}"
else
    echo -e "${RED}❌ API 服务未运行${NC}"
    exit 1
fi

# 检查 graphs_proxy
if curl -s "$PROXY_URL/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ graphs_proxy 服务运行中${NC}"
    HEALTH=$(curl -s "$PROXY_URL/health" | python3 -m json.tool 2>/dev/null | grep -E "\"overall\"" | head -1)
    echo "   健康状态: $HEALTH"
else
    echo -e "${RED}❌ graphs_proxy 服务未运行${NC}"
    exit 1
fi

echo ""

# 2. 准备测试数据
echo "2️⃣  准备测试数据..."
TEST_TEXT="Alice 是一名软件工程师，她使用 Python 和 JavaScript 开发 Web 应用。Bob 是她的同事，他们一起在 Google 工作。Alice 和 Bob 经常一起讨论技术问题。"
TEST_USER_ID="test_e2e_$(date +%s)"
TEST_APP="test_app"

echo "   测试文本: ${TEST_TEXT:0:60}..."
echo "   用户ID: $TEST_USER_ID"
echo "   App: $TEST_APP"
echo ""

# 3. 获取默认用户ID（从环境变量）
echo "3️⃣  获取默认用户ID..."
DEFAULT_USER_ID=$(python3 -c "import os; print(os.getenv('USER', 'default_user'))" 2>/dev/null || echo "default_user")
echo "   使用用户ID: $DEFAULT_USER_ID"
echo ""

# 4. 通过 API 添加记忆
echo "4️⃣  通过 API 添加记忆（模拟 UI 操作）..."
echo -e "${BLUE}   发送 POST $API_URL/api/v1/memories/ 请求...${NC}"
echo ""

RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "$API_URL/api/v1/memories/" \
  -H "Content-Type: application/json" \
  -d "{
    \"text\": \"$TEST_TEXT\",
    \"user_id\": \"$DEFAULT_USER_ID\",
    \"app\": \"$TEST_APP\"
  }")

HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS:" | cut -d: -f2)
RESPONSE_BODY=$(echo "$RESPONSE" | sed '/HTTP_STATUS:/d')

echo "   HTTP 状态码: $HTTP_STATUS"
echo ""

# 检查响应
if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
    echo -e "${GREEN}✅ 添加记忆成功${NC}"
    MEMORY_ID=$(echo "$RESPONSE_BODY" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('id', 'unknown'))" 2>/dev/null || echo "unknown")
    echo "   记忆ID: $MEMORY_ID"
else
    ERROR_MSG=$(echo "$RESPONSE_BODY" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', json.load(sys.stdin).get('detail', 'Unknown error')))" 2>/dev/null || echo "$RESPONSE_BODY")
    echo -e "${RED}❌ 添加记忆失败: $ERROR_MSG${NC}"
    echo ""
    echo "完整响应:"
    echo "$RESPONSE_BODY" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE_BODY"
    exit 1
fi

echo ""

# 5. 等待处理完成
echo "5️⃣  等待处理完成（5秒）..."
sleep 5
echo ""

# 6. 检查 API 日志
echo "6️⃣  检查 API 日志..."
API_LOG_FILE="../../logs/api.log"
if [ -f "$API_LOG_FILE" ]; then
    echo "   查找双写相关日志..."
    API_LOG=$(tail -100 "$API_LOG_FILE" 2>/dev/null | grep -E "双写|GRAPH STORE|Neo4jGraph|POST /api/v1/memories|VECTOR STORE|开始写入|写入完成" | tail -15)
    if [ -n "$API_LOG" ]; then
        echo "$API_LOG" | while IFS= read -r line; do
            echo "   $line"
        done
    else
        echo -e "${YELLOW}   ⚠️  未找到相关日志${NC}"
    fi
else
    echo -e "${YELLOW}   ⚠️  日志文件不存在: $API_LOG_FILE${NC}"
fi
echo ""

# 7. 检查 graphs_proxy 日志
echo "7️⃣  检查 graphs_proxy 日志..."
PROXY_LOG=$(cd ../../graphs_proxy && docker-compose logs --tail=100 graphs-proxy 2>/dev/null | grep -E "POST /cypher|151.101.90.132|127.0.0.1.*POST" | tail -10)
if [ -n "$PROXY_LOG" ]; then
    echo "$PROXY_LOG" | while IFS= read -r line; do
        echo "   $line"
    done
else
    echo -e "${YELLOW}   ⚠️  未找到相关日志${NC}"
fi
echo ""

# 8. 验证数据存储
echo "8️⃣  验证数据存储..."
echo "   检查 MySQL 中的记忆..."

QUERY_RESPONSE=$(curl -s "$API_URL/api/v1/memories/?user_id=$DEFAULT_USER_ID" 2>/dev/null)
MEMORY_COUNT=$(echo "$QUERY_RESPONSE" | python3 -c "import sys, json; data=json.load(sys.stdin); items=data.get('items', []); print(len(items))" 2>/dev/null || echo "0")

if [ "$MEMORY_COUNT" -gt 0 ]; then
    echo -e "${GREEN}   ✅ 在 MySQL 中找到 $MEMORY_COUNT 条记忆${NC}"
else
    echo -e "${YELLOW}   ⚠️  未在 MySQL 中找到记忆（可能还在处理中）${NC}"
fi

echo ""

# 9. 测试总结
echo "=========================================="
echo "📊 测试总结"
echo "=========================================="
echo ""

if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "201" ]; then
    echo -e "${GREEN}✅ API 调用成功${NC}"
else
    echo -e "${RED}❌ API 调用失败（HTTP $HTTP_STATUS）${NC}"
fi

if [ "$MEMORY_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✅ 记忆已成功存储（$MEMORY_COUNT 条）${NC}"
    echo -e "${GREEN}✅ 端到端测试通过！${NC}"
else
    echo -e "${YELLOW}⚠️  未在数据库中找到记忆${NC}"
    echo "   可能原因："
    echo "   1. LLM 处理失败（需要 OPENAI_API_KEY）"
    echo "   2. 处理时间较长，需要等待"
    echo "   3. 数据库连接问题"
fi

echo ""
echo "=========================================="
echo "✅ 端到端测试完成"
echo "=========================================="
echo ""
echo "💡 提示："
echo "   - 查看 API 日志: tail -f ../../logs/api.log"
echo "   - 查看 proxy 日志: cd ../../graphs_proxy && docker-compose logs -f graphs-proxy"

