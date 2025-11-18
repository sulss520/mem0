# OpenMemory - Neo4j 图数据库集成指南

OpenMemory 支持 Neo4j 作为图数据库（Graph Store），用于存储实体关系，提供强大的图查询和关系分析能力。

## 目录

- [快速开始](#快速开始)
- [配置方式](#配置方式)
- [代理模式配置](#代理模式配置)
- [执行流程](#执行流程)
- [功能说明](#功能说明)
- [故障排查](#故障排查)
- [相关文档](#相关文档)

## 快速开始

### 前置条件

- Neo4j 服务（本地或云端）
- OpenMemory API 服务
- Python 依赖：`langchain-neo4j>=0.4.0`, `rank-bm25>=0.2.2`

### 快速配置

1. **启动 Neo4j 服务**（如果使用 Docker）：
```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  neo4j:latest
```

2. **配置环境变量**（在 `api/.env` 文件中）：
```env
NEO4J_URL=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

3. **重启 OpenMemory 服务**：
```bash
./admin.sh local-stop
./admin.sh local
```

## 配置方式

### 方式 1: 通过环境变量配置（推荐）

在 `api/.env` 文件中添加以下配置：

```env
# Neo4j 配置
NEO4J_URL=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

**支持的 URL 格式：**
- `bolt://localhost:7687` - 本地 Neo4j（无加密）
- `neo4j://localhost:7687` - 本地 Neo4j（有加密）
- `neo4j+s://your-instance.databases.neo4j.io` - Neo4j Aura（云端）

**Docker 环境：**
- Docker Compose 网络：`bolt://neo4j:7687`（如果 Neo4j 在同一个 docker-compose 中）
- 主机访问：`bolt://host.docker.internal:7687`（从容器访问主机上的 Neo4j）

### 方式 2: 通过 API 配置

#### 获取当前配置
```bash
GET /api/v1/config/mem0/graph-store
```

#### 更新配置
```bash
PUT /api/v1/config/mem0/graph-store
Content-Type: application/json

{
  "provider": "neo4j",
  "config": {
    "url": "bolt://localhost:7687",
    "username": "neo4j",
    "password": "your_password",
    "database": "neo4j"
  }
}
```

#### 删除配置
```bash
DELETE /api/v1/config/mem0/graph-store
```

### 方式 3: 通过完整配置 API

```bash
PUT /api/v1/config
Content-Type: application/json

{
  "mem0": {
    "graph_store": {
      "provider": "neo4j",
      "config": {
        "url": "bolt://localhost:7687",
        "username": "neo4j",
        "password": "env:NEO4J_PASSWORD",
        "database": "neo4j"
      }
    }
  }
}
```

**注意事项：**
- 密码可以使用 `env:NEO4J_PASSWORD` 格式从环境变量读取
- 配置更改后，需要重启服务或调用 `reset_memory_client()` 来生效
- Neo4j 配置是可选的，如果不配置，系统将只使用向量数据库

## 代理模式配置

OpenMemory 支持通过 `graphs_proxy` 代理服务连接 Neo4j，提供双写、故障转移等高级功能。

### 代理模式的优势

- ✅ 支持双写（数据同步到多个 Neo4j 节点）
- ✅ 自动故障转移
- ✅ 健康监控
- ✅ 负载均衡

### 配置代理模式

#### 1. 在 OpenMemory 中启用代理

在 `openmemory/api/.env` 文件中添加：

```env
# 启用代理模式
NEO4J_USE_PROXY=true

# 代理服务地址（graphs_proxy 服务地址）
NEO4J_PROXY_URL=http://localhost:8090

# Neo4j 认证信息（用于代理认证）
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j123

# 数据库名称（可选，默认 neo4j）
NEO4J_DATABASE=neo4j
```

#### 2. 配置并启动 graphs_proxy 服务

在 `graphs_proxy` 目录下创建或编辑 `.env` 文件：

```env
# Neo4j 节点配置
NEO4J_PRIMARY_URI=neo4j://localhost:7687
NEO4J_SECONDARY_URI=neo4j://localhost:7787
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j123

# 代理服务端口
PORT=8090
```

启动 graphs_proxy：
```bash
cd /path/to/graphs_proxy
python graphs_proxy.py
```

#### 3. 验证代理服务

```bash
# 检查健康状态
curl http://localhost:8090/health

# 查看统计信息
curl http://localhost:8090/stats
```

### 直连模式（备选）

如果不需要代理功能，可以直接连接 Neo4j：

```env
# 不使用代理
NEO4J_USE_PROXY=false

# 直接连接 Neo4j
NEO4J_URL=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j123
NEO4J_DATABASE=neo4j
```

### 配置优先级

1. **代理模式**：如果设置了 `NEO4J_USE_PROXY=true` 和 `NEO4J_PROXY_URL`，优先使用代理模式
2. **直连模式**：如果未启用代理模式，但设置了 `NEO4J_URL`，使用直连模式
3. **未配置**：如果两者都未配置，Graph Store 功能将被禁用

### 验证配置

启动 OpenMemory API 服务后，查看日志确认配置：

```
✅ [GRAPH STORE] Auto-detected graph store: neo4j (代理模式)
   代理地址: http://localhost:8090
   用户名: neo4j
   数据库: neo4j
   💡 提示: 通过 graphs_proxy 代理连接，支持双写和故障转移
```

## 执行流程

### 1. 初始化阶段（服务启动时）

```
启动服务
  ↓
get_memory_client() 被调用
  ↓
get_default_memory_config() 读取配置
  ↓
检测到 NEO4J_URL, NEO4J_USERNAME, NEO4J_PASSWORD
  ↓
构建 graph_store_config
  ↓
Memory.from_config(config_dict) 初始化
  ↓
Mem0 Memory 类检查 config.graph_store.config
  ↓
如果存在，创建 GraphStoreFactory.create(provider="neo4j", config)
  ↓
初始化 MemoryGraph 实例
  ↓
创建 Neo4jGraph 连接
  ↓
设置 self.enable_graph = True
  ↓
self.graph = MemoryGraph 实例
```

### 2. 创建记忆时（调用 add 方法）

```
用户调用 POST /api/v1/memories/
  ↓
create_memory() 函数
  ↓
memory_client.add(text, user_id=..., metadata=...)
  ↓
Mem0 Memory.add() 方法
  ↓
【关键步骤】检查 self.enable_graph 和 self.graph
  ↓
如果 enable_graph = True 且 self.graph 存在：
  ├─ 提取实体和关系（使用 LLM）
  ├─ 调用 self.graph.add(data, filters)
  ├─ MemoryGraph.add() 执行：
  │   ├─ _retrieve_nodes_from_data() - 提取实体节点
  │   ├─ _establish_nodes_relations_from_data() - 建立关系
  │   ├─ _search_graph_db() - 搜索现有节点
  │   ├─ _get_delete_entities_from_search_output() - 确定要删除的实体
  │   ├─ _delete_entities() - 删除过时实体
  │   └─ _add_entities() - 添加新实体和关系到 Neo4j
  │       └─ 执行 Cypher 查询：
  │           - MERGE (n:Entity {name: $name, user_id: $user_id})
  │           - MERGE (m:Entity {name: $name, user_id: $user_id})
  │           - MERGE (n)-[r:RELATIONSHIP]->(m)
  └─ 返回 graph_result（包含 relations）
  ↓
同时保存到向量数据库（Milvus/Qdrant 等）
  ↓
保存到 MySQL 数据库
  ↓
返回结果
```

### 触发条件

数据保存到 Neo4j 需要满足以下条件：

1. **配置正确**：
   - ✅ graph_store 配置存在
   - ✅ graph_store.config 不为空
   - ✅ Neo4j 连接信息正确

2. **调用 add 方法**：
   - 通过 API: `POST /api/v1/memories/`
   - 通过 MCP: `add_memories(text)`
   - 直接调用: `memory_client.add(text, user_id=...)`

3. **Mem0 自动处理**：
   - 如果 `self.enable_graph = True`
   - 如果 `self.graph` 实例存在
   - Mem0 会自动调用 `self.graph.add()`

### 保存的内容

Neo4j 中会保存：

1. **实体节点（Entity Nodes）**：
   - 标签：`Entity` 或 `__Entity__`（取决于配置）
   - 属性：
     - `name`: 实体名称
     - `user_id`: 用户ID
     - `embedding`: 实体的向量嵌入（可选）
     - `created`: 创建时间戳

2. **关系（Relationships）**：
   - 类型：根据文本提取的关系类型（如 "KNOWS", "LIKES", "WORKS_AT" 等）
   - 属性：
     - `created_at`: 创建时间戳
     - `updated_at`: 更新时间戳

### 示例

假设你添加记忆：
```
"Alice 喜欢 Python 编程，她在 Google 工作"
```

Mem0 会：
1. 提取实体：Alice, Python, Google
2. 提取关系：
   - Alice -[LIKES]-> Python
   - Alice -[WORKS_AT]-> Google
3. 在 Neo4j 中创建：
   ```
   (Alice:Entity {name: "Alice", user_id: "shallin"})
   (Python:Entity {name: "Python", user_id: "shallin"})
   (Google:Entity {name: "Google", user_id: "shallin"})
   (Alice)-[:LIKES]->(Python)
   (Alice)-[:WORKS_AT]->(Google)
   ```

## 功能说明

配置 Neo4j 后，OpenMemory 将：
- 自动检测环境变量中的 Neo4j 配置
- 在创建记忆时，自动提取实体关系并存储到 Neo4j
- 支持通过图查询来增强记忆检索

### 验证配置是否生效

#### 方法 1: 查看日志

启动服务时应该看到：
```
Auto-detected graph store: neo4j with URL: bolt://localhost:7687
Initializing memory client with config hash: ...
Memory client initialized successfully
```

#### 方法 2: 检查 Memory 实例

在代码中，如果配置正确：
- `memory_client.enable_graph` 应该为 `True`
- `memory_client.graph` 应该不为 `None`

#### 方法 3: 测试添加记忆

添加一个记忆后，检查 Neo4j：
```cypher
MATCH (n:Entity)
RETURN n LIMIT 10;
```

或者查看关系：
```cypher
MATCH (a:Entity)-[r]->(b:Entity)
RETURN a.name, type(r), b.name LIMIT 10;
```

## 故障排查

### 1. 为什么没有数据保存到 Neo4j？

检查以下几点：
1. Neo4j 服务是否运行：`neo4j status` 或 `docker ps | grep neo4j`
2. 连接信息是否正确：测试连接 `bolt://localhost:7687`
3. 密码是否正确：检查环境变量 `NEO4J_PASSWORD`
4. 查看 API 日志：是否有错误信息
5. 确认 `enable_graph` 是否为 `True`

### 2. 代理服务未启动

**错误信息：** 连接失败或超时

**解决方案：**
```bash
# 检查 graphs_proxy 是否运行
curl http://localhost:8090/health

# 如果未运行，启动服务
cd /path/to/graphs_proxy
python graphs_proxy.py
```

### 3. 环境变量未正确配置

**错误信息：** `Graph store 未配置`

**解决方案：**
- 检查 `openmemory/api/.env` 文件
- 确认 `NEO4J_USE_PROXY=true` 和 `NEO4J_PROXY_URL` 已设置（代理模式）
- 或确认 `NEO4J_URL` 已设置（直连模式）
- 重启 API 服务

### 4. Neo4j 节点连接失败

**错误信息：** graphs_proxy 健康检查失败

**解决方案：**
- 检查 Neo4j 节点是否运行（端口 7687 和 7787）
- 验证 `graphs_proxy` 的 `.env` 配置中的 Neo4j URI 是否正确
- 检查 Neo4j 认证信息

### 5. 如何确认 graph_store 配置已加载？

在 `memory.py` 的 `get_default_memory_config()` 中添加日志：
```python
if graph_store_config:
    print(f"✅ Graph store configured: {graph_store_config}")
```

### 6. 数据什么时候保存？

每次调用 `memory_client.add()` 时，如果配置了 graph_store，数据会**同时**保存到：
- 向量数据库（用于语义搜索）
- MySQL 数据库（用于元数据管理）
- Neo4j 图数据库（用于实体关系）

## 相关文档

- **[Neo4j 配置指南（简洁版）](docs/neo4j-configuration.md)** - 快速配置和使用 Neo4j
- [graphs_proxy 使用指南](../graphs_proxy/README.md)
- [Neo4j 测试文档](api/tests/README_NEO4J_TEST.md)
- [OpenMemory 主文档](README.md)

## 测试

详细的测试说明和测试用例请参考：
- `api/tests/test_neo4j.py` - Neo4j 单元测试文件
- `api/tests/README_NEO4J_TEST.md` - 测试说明文档

### 运行测试

```bash
cd openmemory/api
pytest tests/test_neo4j.py -v
```

测试覆盖：
- ✅ Neo4j 连接测试
- ✅ 添加实体和关系
- ✅ 查询实体
- ✅ 查询关系
- ✅ 删除实体
- ✅ 批量操作
- ✅ 端到端工作流

