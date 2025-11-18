# Neo4j 图数据库配置与双写功能指南

OpenMemory 支持 Neo4j 作为图数据库（Graph Store），用于存储实体关系，增强记忆检索能力。本文档包含 Neo4j 的配置方法、连接方式以及双写功能的详细说明。

## 目录

- [工作原理](#工作原理)
- [连接方式](#连接方式)
- [配置方法](#配置方法)
- [双写功能](#双写功能)
- [使用示例](#使用示例)
- [故障排查](#故障排查)

## 工作原理

### 什么是 Graph Store？

Graph Store 是 Mem0 的一个组件，用于存储实体（Entity）和关系（Relationship）：

- **实体**：从记忆文本中提取的实体（如人名、地点、概念等）
- **关系**：实体之间的关联（如 "Alice 喜欢 Python"）

### 数据存储流程

```
添加记忆 → LLM 提取实体和关系 → 存储到 Neo4j → 增强检索能力
```

**示例：**
- 输入：`"Alice 喜欢 Python 编程，她在 Google 工作"`
- 提取：
  - 实体：Alice, Python, Google
  - 关系：Alice -[LIKES]-> Python, Alice -[WORKS_AT]-> Google
- 存储：在 Neo4j 中创建节点和关系

## 连接方式

OpenMemory 支持三种连接 Neo4j 的方式：

### 1. 直连模式（Direct Connection）

**特点：**
- ✅ 简单直接
- ✅ 性能好
- ❌ 不支持双写和故障转移

**适用场景：** 单节点 Neo4j，不需要高可用

### 2. 代理模式（Proxy Mode）

**特点：**
- ✅ 支持双写（数据同步到多个节点）
- ✅ 自动故障转移
- ✅ 健康监控
- ✅ 负载均衡

**适用场景：** 生产环境，需要高可用

### 3. 双写模式（Dual Write Mode）

**特点：**
- ✅ 通过 HTTP API 实现双写
- ✅ 支持故障转移
- ✅ 直连作为降级备用
- ✅ 读写操作自动分离

**适用场景：** 需要高可用且需要降级方案

## 配置方法

### 方式 1：直连模式（推荐用于开发）

在 `api/.env` 文件中配置：

```env
# Neo4j 直连配置
NEO4J_URL=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

**URL 格式：**
- `bolt://localhost:7687` - 本地（无加密）
- `neo4j://localhost:7687` - 本地（有加密）
- `neo4j+s://your-instance.databases.neo4j.io` - Neo4j Aura（云端）

**Docker 环境：**
- 同一网络：`bolt://neo4j:7687`
- 主机访问：`bolt://host.docker.internal:7687`

### 方式 2：代理模式（推荐用于生产）

#### 步骤 1：启动 graphs_proxy 服务

在 `memory_store/graphs_proxy/` 目录下配置 `.env`：

```env
# Neo4j 节点配置
NEO4J_PRIMARY_URI=neo4j://localhost:7687
NEO4J_SECONDARY_URI=neo4j://localhost:7787  # 可选
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# 代理服务端口
PORT=8090
```

启动服务：
```bash
cd memory_store/graphs_proxy
python graphs_proxy.py
```

#### 步骤 2：配置 OpenMemory

在 `api/.env` 文件中配置：

```env
# 启用代理模式
NEO4J_USE_PROXY=true
NEO4J_PROXY_URL=http://localhost:8090

# Neo4j 认证信息
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

### 方式 3：双写模式（高级）

在 `api/.env` 文件中配置：

```env
# 启用双写
NEO4J_ENABLE_DUAL_WRITE=true
NEO4J_PROXY_URL=http://localhost:8090

# 直连地址（降级备用）
NEO4J_URL=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j

# 可选配置
NEO4J_DUAL_WRITE_TIMEOUT=30  # HTTP API 超时时间（秒）
NEO4J_DUAL_WRITE_FALLBACK=true  # 是否启用降级（默认：true）
```

### 配置优先级

系统按以下优先级选择连接方式：

1. **双写模式**：`NEO4J_ENABLE_DUAL_WRITE=true` + `NEO4J_PROXY_URL`
2. **Bolt 代理**：`NEO4J_BOLT_PROXY_URL`（如果配置）
3. **HTTP 代理**：`NEO4J_USE_PROXY=true` + `NEO4J_PROXY_URL`
4. **直连模式**：`NEO4J_URL` + `NEO4J_USERNAME` + `NEO4J_PASSWORD`
5. **未配置**：Graph Store 功能禁用

## 双写功能

### 概述

OpenMemory 已集成 Neo4j 双写功能，通过 **Monkey Patch** 方式实现，对现有代码**完全透明**，无需修改任何业务逻辑。

### 工作原理

#### 执行流程

```
OpenMemory API (POST /api/v1/memories/)
  ↓
memory_client.add(text, user_id=...)
  ↓
mem0 内部处理
  ├─ 向量化存储（Milvus/Qdrant）
  └─ 图数据库存储（Neo4j）
      ↓
    MemoryGraph.add()
      ↓
    执行多个 Cypher 查询：
      ├─ MATCH (读操作) → 直接使用 Bolt 连接 ✅
      ├─ MERGE (写操作) → HTTP API 双写 ✅
      ├─ CREATE (写操作) → HTTP API 双写 ✅
      └─ DELETE (写操作) → HTTP API 双写 ✅
```

#### 读写分离机制

**读操作**（MATCH、RETURN 等）：
- 直接使用原始的 Bolt 连接
- 不经过 HTTP API
- 性能最优

**写操作**（CREATE、MERGE、DELETE 等）：
- 通过 `graphs_proxy` HTTP API 执行
- 支持双写和故障转移
- 如果 HTTP API 失败，自动降级到单实例 Bolt 连接

### 代码适配

#### ✅ 无需修改

**OpenMemory API 代码**：
- `app/routers/memories.py` - 无需修改
- `app/routers/*.py` - 无需修改
- 所有业务逻辑代码 - 无需修改

**原因**：
- Monkey Patch 在底层拦截 `Neo4jGraph.query()` 方法
- 对上层代码完全透明
- mem0 和 OpenMemory 都感知不到变化

### 监控端点

已添加统计信息 API 端点：

```bash
GET /api/v1/stats/dual-write
```

**响应示例**：
```json
{
  "read_total": 100,
  "write_total": 10,
  "write_success": 10,
  "write_fallback": 0,
  "write_errors": 0,
  "success_rate": 100.0,
  "description": {
    "read_total": "读操作总数（MATCH、RETURN 等，直接使用 Bolt 连接）",
    "write_total": "写操作总数（CREATE、MERGE、DELETE 等，通过 HTTP API 双写）",
    "write_success": "双写成功次数（通过 HTTP API 成功写入）",
    "write_fallback": "降级次数（HTTP API 失败时回退到单实例 Bolt 连接）",
    "write_errors": "写操作错误次数",
    "success_rate": "双写成功率（write_success / write_total * 100）"
  }
}
```

### 使用示例

#### 正常使用（无需任何改动）

```python
# OpenMemory API 中
from app.utils.memory import get_memory_client

memory_client = get_memory_client()
result = memory_client.add(
    "Alice 是 Bob 的朋友",
    user_id="user123"
)
# 自动通过 HTTP API 双写，无需任何额外代码
```

#### 监控双写状态

```python
from app.utils.neo4j_dual_write_patch import get_dual_write_stats

stats = get_dual_write_stats()
print(f"双写成功率: {stats['write_success'] / stats['write_total'] * 100}%")
```

或通过 API：
```bash
curl http://localhost:8765/api/v1/stats/dual-write
```

### 优势

1. **零侵入**：不需要修改任何业务代码
2. **自动分离**：读写操作自动识别和处理
3. **高可用**：支持故障转移和降级
4. **可监控**：提供详细的统计信息
5. **易维护**：代码简单（约 150 行）

### 注意事项

1. **性能影响**：
   - 写操作多一次 HTTP 请求（约 10-50ms）
   - 读操作无影响（直接 Bolt 连接）

2. **依赖关系**：
   - 依赖 `langchain_neo4j` 的内部实现
   - 如果 `langchain_neo4j` 升级，可能需要调整 patch

3. **降级机制**：
   - HTTP API 失败时自动降级到单实例
   - 确保 `NEO4J_URL` 配置正确

## 连接流程

### 初始化流程

```
OpenMemory API 启动
    ↓
读取环境变量
    ↓
检测 Neo4j 配置
    ↓
构建 graph_store_config
    ↓
Memory.from_config(config_dict)
    ↓
GraphStoreFactory.create(provider="neo4j", config)
    ↓
创建 Neo4jGraph 连接
    ↓
设置 enable_graph = True
```

### 数据写入流程

```
用户调用 POST /api/v1/memories/
    ↓
memory_client.add(text, user_id, metadata)
    ↓
检查 enable_graph 和 graph 实例
    ↓
LLM 提取实体和关系
    ↓
存储到 Neo4j（通过代理或直连）
    ↓
同时保存到向量数据库和 MySQL
    ↓
返回结果
```

### 代理模式详细流程

```
OpenMemory → HTTP POST → graphs_proxy → Neo4j 节点
              /execute
```

**请求示例：**
```json
POST http://localhost:8090/execute
{
    "query": "MATCH (n:Entity) RETURN n LIMIT 10",
    "parameters": {}
}
```

## 使用示例

### 1. 启动 Neo4j（Docker）

```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:latest
```

### 2. 配置 OpenMemory

在 `api/.env` 中添加：
```env
NEO4J_URL=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
```

### 3. 重启服务

```bash
cd openmemory
./admin.sh local-stop
./admin.sh local
```

### 4. 验证配置

查看启动日志，应该看到：
```
✅ [GRAPH STORE] Auto-detected graph store: neo4j (直连模式)
   URL: bolt://localhost:7687
   Username: neo4j
   Database: neo4j
```

### 5. 添加记忆

通过 API 添加记忆：
```bash
curl -X POST http://localhost:8765/api/v1/memories/ \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Alice 喜欢 Python 编程，她在 Google 工作",
    "user_id": "test_user"
  }'
```

### 6. 查询 Neo4j

在 Neo4j Browser（http://localhost:7474）中查询：

```cypher
// 查看所有实体
MATCH (n:Entity) RETURN n LIMIT 10;

// 查看关系
MATCH (a:Entity)-[r]->(b:Entity)
RETURN a.name, type(r), b.name LIMIT 10;
```

## 故障排查

### 问题 1：没有数据保存到 Neo4j

**检查清单：**
1. ✅ Neo4j 服务是否运行：`docker ps | grep neo4j`
2. ✅ 连接信息是否正确：测试 `bolt://localhost:7687`
3. ✅ 密码是否正确：检查 `NEO4J_PASSWORD`
4. ✅ 查看 API 日志：是否有错误信息
5. ✅ 确认 `enable_graph` 是否为 `True`

**验证方法：**
```python
# 在代码中检查
print(f"enable_graph: {memory_client.enable_graph}")
print(f"graph: {memory_client.graph}")
```

### 问题 2：代理服务连接失败

**错误信息：** 连接超时或失败

**解决方案：**
```bash
# 1. 检查 graphs_proxy 是否运行
curl http://localhost:8090/health

# 2. 如果未运行，启动服务
cd memory_store/graphs_proxy
python graphs_proxy.py

# 3. 检查环境变量
echo $NEO4J_PROXY_URL
```

### 问题 3：配置未生效

**解决方案：**
1. 确认 `.env` 文件在 `api/` 目录下
2. 重启 OpenMemory 服务
3. 或调用 `reset_memory_client()` API

### 问题 4：Docker 环境连接问题

**问题：** 容器内无法连接 `localhost:7687`

**解决方案：**
- 使用容器名称：`bolt://neo4j:7687`
- 或使用：`bolt://host.docker.internal:7687`

### 问题 5：检查双写是否启用

```python
from app.utils.neo4j_dual_write_patch import get_dual_write_stats

stats = get_dual_write_stats()
if stats['write_total'] > 0:
    print(f"双写已启用，成功率: {stats['write_success'] / stats['write_total'] * 100}%")
else:
    print("未检测到写操作")
```

### 问题 6：检查 HTTP API 是否可用

```bash
curl http://localhost:8090/health
```

### 问题 7：查看日志

```bash
# API 日志
tail -f openmemory/.local_pids/api.log | grep -E "双写|dual-write"

# graphs_proxy 日志
docker-compose logs graphs-proxy | grep -E "POST /cypher"
```

## 总结

**OpenMemory 无需任何适配**，双写功能已自动集成：

- ✅ 读写操作自动分离
- ✅ 写操作自动双写
- ✅ 读操作直接连接
- ✅ 支持故障转移
- ✅ 提供监控端点

只需配置环境变量即可使用！

## 相关文档

- [README-zh.md](../README-zh.md) - 完整的中文文档
- [Neo4j 测试文档](../api/tests/README_NEO4J_TEST.md) - 测试说明
- [graphs_proxy 文档](../../../memory_store/graphs_proxy/README.md) - 代理服务文档

