# OpenMemory API 测试文档

## 测试原则

本项目采用**黑盒测试**方式，遵循以下原则：

1. **只通过 HTTP API 接口测试**：不直接调用内部函数或类
2. **只关注输入和输出**：不关心内部实现细节
3. **测试功能是否按预期工作**：验证 API 的行为是否符合预期
4. **使用标准测试框架**：使用 pytest 进行 Python 测试，使用 shell 脚本进行集成测试

## 测试文件结构

```
tests/
├── test_api_blackbox.py      # Python 黑盒测试（pytest）
├── test_e2e_blackbox.sh       # 端到端黑盒测试（shell）
├── test_e2e_complete.sh       # 完整端到端测试（shell）
└── test_e2e_dual_write.sh    # 双写功能测试（shell）
```

## 运行测试

### 前置条件

1. **启动 API 服务**：
   ```bash
   cd openmemory/api
   python main.py
   # 或使用 admin.sh
   cd ../..
   ./admin.sh start
   ```

2. **环境变量配置**：
   确保 `api/.env` 文件已配置必要的环境变量：
   - `OPENAI_API_KEY` - OpenAI API 密钥（必需）
   - `USER` - 用户 ID（必需）
   - `NEO4J_*` - Neo4j 配置（可选）

### Python 黑盒测试（pytest）

```bash
cd openmemory/api

# 运行所有测试
pytest tests/test_api_blackbox.py -v

# 运行特定测试类
pytest tests/test_api_blackbox.py::TestMemoriesAPI -v

# 运行单个测试
pytest tests/test_api_blackbox.py::TestMemoriesAPI::test_create_memory -v

# 显示详细输出
pytest tests/test_api_blackbox.py -v -s

# 只运行失败的测试
pytest tests/test_api_blackbox.py --lf
```

### Shell 脚本测试

```bash
cd openmemory/api/tests

# 运行黑盒端到端测试
./test_e2e_blackbox.sh

# 运行完整端到端测试
./test_e2e_complete.sh

# 运行双写功能测试
./test_e2e_dual_write.sh
```

## 测试覆盖范围

### Python 黑盒测试（test_api_blackbox.py）

#### 1. API 健康检查（TestAPIHealth）
- API 文档可访问性
- OpenAPI Schema 可访问性

#### 2. 记忆 API（TestMemoriesAPI）
- 创建记忆
- 列出记忆
- 根据 ID 获取记忆
- 更新记忆
- 删除记忆
- 搜索记忆
- 获取分类

#### 3. 配置 API（TestConfigAPI）
- 获取配置
- 获取 LLM 配置
- 获取 Embedder 配置
- 获取 Graph Store 配置
- 更新 LLM 配置

#### 4. 统计 API（TestStatsAPI）
- 获取双写统计信息

#### 5. Neo4j 集成测试（TestNeo4jIntegration）
- 创建记忆时是否写入 Neo4j
- 验证双写统计信息

#### 6. 应用 API（TestAppsAPI）
- 列出应用
- 根据 ID 获取应用

### Shell 脚本测试

#### test_e2e_blackbox.sh
- 完整的端到端黑盒测试
- 通过 API 创建记忆
- 验证 Neo4j 双写功能
- 检查统计信息

#### test_e2e_complete.sh
- 完整的端到端测试流程
- 包含更多验证步骤

#### test_e2e_dual_write.sh
- 专门测试 Neo4j 双写功能
- 验证双写统计和降级机制

## 测试配置

### 环境变量

测试可以通过环境变量进行配置：

```bash
# API 地址（默认: http://localhost:8765）
export API_URL=http://localhost:8765

# 测试用户 ID（默认: test_user_<timestamp>）
export TEST_USER_ID=test_user_123

# 测试应用名称（默认: test_app）
export TEST_APP=test_app
```

### 跳过测试

如果某些服务未配置，测试会自动跳过：

- Graph Store 未配置时，Neo4j 相关测试会被跳过
- API 服务不可用时，所有测试会被跳过

## 测试最佳实践

1. **独立测试**：每个测试应该是独立的，不依赖其他测试的执行顺序
2. **清理数据**：测试后自动清理测试数据，避免影响后续测试
3. **错误处理**：测试应该验证错误情况，而不仅仅是成功情况
4. **可读性**：测试名称应该清晰描述测试内容
5. **快速执行**：测试应该尽可能快速执行

## 故障排除

### API 服务不可用

```
pytest.skip("API 服务不可用，请先启动服务")
```

**解决方案**：
1. 检查 API 服务是否运行：`curl http://localhost:8765/docs`
2. 检查端口是否被占用
3. 查看 API 日志文件

### Graph Store 未配置

```
pytest.skip("Graph Store 未配置，跳过 Neo4j 测试")
```

**解决方案**：
1. 配置 Neo4j 环境变量（见 `.env.example`）
2. 或通过 API 配置 Graph Store

### 测试超时

**解决方案**：
1. 增加等待时间（`time.sleep()`）
2. 检查服务性能
3. 检查网络连接

### 测试数据未清理

**解决方案**：
1. 手动清理测试数据
2. 检查 `cleanup_memories` fixture 是否正常工作
3. 使用测试数据库

## 持续集成

测试可以在 CI/CD 流程中运行：

```yaml
# GitHub Actions 示例
- name: Run API Tests
  run: |
    cd openmemory/api
    pytest tests/test_api_blackbox.py -v
```

## 贡献指南

添加新测试时，请遵循以下原则：

1. **使用黑盒测试方式**：只通过 HTTP API 测试
2. **添加适当的清理逻辑**：确保测试后清理数据
3. **添加文档**：在测试中添加清晰的注释
4. **遵循命名规范**：测试类名和测试方法名应该清晰描述测试内容

