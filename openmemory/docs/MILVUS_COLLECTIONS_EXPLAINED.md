# Milvus 集合说明

## 集合概述

在 Milvus 中，mem0 会创建和使用多个集合，每个集合有不同的用途：

## 1. `openmemory` 集合

### 用途
**存储用户的记忆数据（Memory）**

### 说明
- 这是您在配置中设置的 `collection_name`
- 所有用户的记忆数据都存储在这个集合中
- 包含记忆的向量、元数据、用户ID等信息

### 配置位置
```python
# openmemory/api/app/utils/memory.py
vector_store_config = {
    "collection_name": "openmemory",  # 用户数据集合
    ...
}
```

### 数据结构
- `id`: 记忆的唯一标识符
- `user_id`: 用户ID
- `memory`: 记忆内容
- `embedding`: 向量数据
- `metadata`: 元数据（如 created_at, updated_at 等）

## 2. `mem0migrations` 集合

### 用途
**mem0 内部用于 telemetry（遥测/统计）**

### 说明
- 这是 mem0 自动创建和管理的集合
- 用于记录 mem0 的使用情况，如：
  - 初始化事件（`mem0.init`）
  - 添加记忆事件（`mem0.add`）
  - 搜索事件（`mem0.search`）
  - 删除事件（`mem0.delete_all`）
- **不影响用户数据**，仅用于内部统计

### 代码位置
```python
# mem0/memory/main.py:150-158
telemetry_config = deepcopy(self.config.vector_store.config)
telemetry_config.collection_name = "mem0migrations"
self._telemetry_vector_store = VectorStoreFactory.create(
    self.config.vector_store.provider, telemetry_config
)
capture_event("mem0.init", self, {"sync_type": "sync"})
```

### 数据结构
- 存储 mem0 的使用统计信息
- 包含事件类型、时间戳、配置信息等

## 3. `default` 集合

### 用途
**可能是 Milvus 的默认集合或之前创建的集合**

### 说明
- 如果这个集合存在，可能是：
  - Milvus 的默认集合
  - 之前测试时创建的集合
  - 其他应用创建的集合

## 数据关系

```
用户创建记忆
    ↓
存储到 openmemory 集合（用户数据）
    ↓
mem0 记录使用情况
    ↓
存储到 mem0migrations 集合（统计信息）
```

## 注意事项

1. **`openmemory` 集合**：
   - 这是您的主要数据集合
   - 所有用户的记忆都存储在这里
   - 可以通过 API 查询和管理

2. **`mem0migrations` 集合**：
   - 这是 mem0 内部使用的集合
   - 不建议手动修改或删除
   - 用于 mem0 的内部统计和分析

3. **数据隔离**：
   - 用户数据（`openmemory`）和统计数据（`mem0migrations`）是分开的
   - 不会相互影响

## 相关代码

- 集合配置: `openmemory/api/app/utils/memory.py` (get_default_memory_config)
- Telemetry 实现: `mem0/memory/telemetry.py`
- Memory 初始化: `mem0/memory/main.py` (Memory.__init__)

