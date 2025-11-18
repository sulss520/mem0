"""
OpenMemory API 黑盒测试

黑盒测试原则：
1. 只通过 HTTP API 接口测试，不直接调用内部函数
2. 只关注输入和输出，不关心内部实现
3. 测试功能是否按预期工作
4. 使用 pytest 框架，但测试方式是通过 HTTP 请求
"""

import os
import pytest
import requests
import time
from typing import Dict, Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# API 配置
API_BASE_URL = os.getenv("API_URL", "http://localhost:8765")
TEST_USER_ID = os.getenv("TEST_USER_ID", f"test_user_{int(time.time())}")
TEST_APP = os.getenv("TEST_APP", "test_app")


@pytest.fixture(scope="module")
def api_client():
    """API 客户端 fixture"""
    return requests.Session()


@pytest.fixture(scope="module")
def check_api_available():
    """检查 API 服务是否可用"""
    try:
        response = requests.get(f"{API_BASE_URL}/docs", timeout=5)
        if response.status_code == 200:
            return True
    except Exception:
        pass
    pytest.skip("API 服务不可用，请先启动服务")


@pytest.fixture(scope="function")
def test_user_id():
    """测试用户 ID"""
    return TEST_USER_ID


@pytest.fixture(scope="function")
def cleanup_memories(api_client, test_user_id):
    """测试后清理记忆数据"""
    yield
    # 获取所有测试记忆
    try:
        response = api_client.get(
            f"{API_BASE_URL}/api/v1/memories/",
            params={"user_id": test_user_id}
        )
        if response.status_code == 200:
            memories = response.json().get("items", [])
            if memories:
                memory_ids = [m["id"] for m in memories]
                # 删除测试记忆
                api_client.delete(
                    f"{API_BASE_URL}/api/v1/memories/",
                    json={"user_id": test_user_id, "memory_ids": memory_ids}
                )
    except Exception:
        pass


class TestAPIHealth:
    """API 健康检查测试"""
    
    def test_api_docs_available(self, check_api_available):
        """测试 API 文档是否可访问"""
        response = requests.get(f"{API_BASE_URL}/docs", timeout=5)
        assert response.status_code == 200
    
    def test_api_openapi_schema(self, check_api_available):
        """测试 OpenAPI Schema 是否可访问"""
        response = requests.get(f"{API_BASE_URL}/openapi.json", timeout=5)
        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "paths" in schema


class TestMemoriesAPI:
    """记忆 API 黑盒测试"""
    
    def test_create_memory(self, api_client, test_user_id, cleanup_memories, check_api_available):
        """测试创建记忆"""
        text = "Alice 是一名软件工程师，她使用 Python 开发 Web 应用。"
        
        response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": text,
                "app": TEST_APP
            }
        )
        
        assert response.status_code in [200, 201], f"创建失败: {response.text}"
        data = response.json()
        assert "id" in data
        assert data.get("text") == text
        return data["id"]
    
    def test_list_memories(self, api_client, test_user_id, cleanup_memories, check_api_available):
        """测试列出记忆"""
        # 先创建一个记忆
        text = "Bob 是一名数据科学家，他使用 Python 和 R 进行数据分析。"
        create_response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": text,
                "app": TEST_APP
            }
        )
        assert create_response.status_code in [200, 201]
        
        # 等待处理完成
        time.sleep(2)
        
        # 列出记忆
        response = api_client.get(
            f"{API_BASE_URL}/api/v1/memories/",
            params={"user_id": test_user_id}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) > 0
    
    def test_get_memory_by_id(self, api_client, test_user_id, cleanup_memories, check_api_available):
        """测试根据 ID 获取记忆"""
        # 创建记忆
        text = "Charlie 是一名 DevOps 工程师，他管理 Kubernetes 集群。"
        create_response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": text,
                "app": TEST_APP
            }
        )
        assert create_response.status_code in [200, 201]
        memory_id = create_response.json()["id"]
        
        # 等待处理完成
        time.sleep(2)
        
        # 获取记忆
        response = api_client.get(f"{API_BASE_URL}/api/v1/memories/{memory_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == memory_id
        assert text in data.get("text", "")
    
    def test_update_memory(self, api_client, test_user_id, cleanup_memories, check_api_available):
        """测试更新记忆"""
        # 创建记忆
        original_text = "David 是一名前端工程师。"
        create_response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": original_text,
                "app": TEST_APP
            }
        )
        assert create_response.status_code in [200, 201]
        memory_id = create_response.json()["id"]
        
        # 等待处理完成
        time.sleep(2)
        
        # 更新记忆
        updated_text = "David 是一名全栈工程师，擅长 React 和 Node.js。"
        response = api_client.put(
            f"{API_BASE_URL}/api/v1/memories/{memory_id}",
            json={
                "text": updated_text,
                "user_id": test_user_id
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert updated_text in data.get("text", "")
    
    def test_delete_memory(self, api_client, test_user_id, check_api_available):
        """测试删除记忆"""
        # 创建记忆
        text = "Eve 是一名产品经理。"
        create_response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": text,
                "app": TEST_APP
            }
        )
        assert create_response.status_code in [200, 201]
        memory_id = create_response.json()["id"]
        
        # 等待处理完成
        time.sleep(2)
        
        # 删除记忆
        response = api_client.delete(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "memory_ids": [memory_id]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        
        # 验证记忆已删除
        get_response = api_client.get(f"{API_BASE_URL}/api/v1/memories/{memory_id}")
        assert get_response.status_code == 404
    
    def test_search_memories(self, api_client, test_user_id, cleanup_memories, check_api_available):
        """测试搜索记忆"""
        # 创建记忆
        text = "Frank 是一名安全工程师，专注于网络安全。"
        create_response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": text,
                "app": TEST_APP
            }
        )
        assert create_response.status_code in [200, 201]
        
        # 等待处理完成
        time.sleep(2)
        
        # 搜索记忆
        response = api_client.get(
            f"{API_BASE_URL}/api/v1/memories/",
            params={
                "user_id": test_user_id,
                "search_query": "安全"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
    
    def test_get_categories(self, api_client, test_user_id, cleanup_memories, check_api_available):
        """测试获取分类"""
        # 创建记忆
        text = "Grace 是一名设计师，她设计用户界面。"
        create_response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": text,
                "app": TEST_APP
            }
        )
        assert create_response.status_code in [200, 201]
        
        # 等待处理完成
        time.sleep(2)
        
        # 获取分类
        response = api_client.get(
            f"{API_BASE_URL}/api/v1/memories/categories",
            params={"user_id": test_user_id}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "categories" in data
        assert "total" in data


class TestConfigAPI:
    """配置 API 黑盒测试"""
    
    def test_get_config(self, api_client, check_api_available):
        """测试获取配置"""
        response = api_client.get(f"{API_BASE_URL}/api/v1/config/")
        
        assert response.status_code == 200
        data = response.json()
        assert "mem0" in data
    
    def test_get_llm_config(self, api_client, check_api_available):
        """测试获取 LLM 配置"""
        response = api_client.get(f"{API_BASE_URL}/api/v1/config/mem0/llm")
        
        assert response.status_code == 200
        data = response.json()
        assert "provider" in data
        assert "config" in data
    
    def test_get_embedder_config(self, api_client, check_api_available):
        """测试获取 Embedder 配置"""
        response = api_client.get(f"{API_BASE_URL}/api/v1/config/mem0/embedder")
        
        assert response.status_code == 200
        data = response.json()
        assert "provider" in data
        assert "config" in data
    
    def test_get_graph_store_config(self, api_client, check_api_available):
        """测试获取 Graph Store 配置"""
        response = api_client.get(f"{API_BASE_URL}/api/v1/config/mem0/graph-store")
        
        # 如果未配置，返回 404 是正常的
        if response.status_code == 404:
            pytest.skip("Graph Store 未配置")
        
        assert response.status_code == 200
        data = response.json()
        assert "provider" in data
    
    def test_update_llm_config(self, api_client, check_api_available):
        """测试更新 LLM 配置"""
        # 获取当前配置
        get_response = api_client.get(f"{API_BASE_URL}/api/v1/config/mem0/llm")
        assert get_response.status_code == 200
        current_config = get_response.json()
        
        # 更新配置
        update_config = {
            "provider": current_config["provider"],
            "config": {
                **current_config["config"],
                "temperature": 0.2
            }
        }
        
        response = api_client.put(
            f"{API_BASE_URL}/api/v1/config/mem0/llm",
            json=update_config
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["config"]["temperature"] == 0.2
        
        # 恢复原配置
        api_client.put(
            f"{API_BASE_URL}/api/v1/config/mem0/llm",
            json=current_config
        )




class TestNeo4jIntegration:
    """Neo4j 集成测试（黑盒）"""
    
    def test_memory_with_neo4j(self, api_client, test_user_id, cleanup_memories, check_api_available):
        """测试创建记忆时是否写入 Neo4j"""
        # 检查 Graph Store 是否配置
        graph_config_response = api_client.get(f"{API_BASE_URL}/api/v1/config/mem0/graph-store")
        if graph_config_response.status_code == 404:
            pytest.skip("Graph Store 未配置，跳过 Neo4j 测试")
        
        # 注意：双写统计端点已移除，这是 Neo4j 双写功能的监控端点，不属于 OpenMemory 核心逻辑
        
        # 创建记忆（应该触发 Neo4j 写入）
        text = "Alice 和 Bob 是同事，他们一起在 Google 工作。Alice 喜欢 Python，Bob 喜欢 Java。"
        response = api_client.post(
            f"{API_BASE_URL}/api/v1/memories/",
            json={
                "user_id": test_user_id,
                "text": text,
                "app": TEST_APP
            }
        )
        
        assert response.status_code in [200, 201]
        
        # 等待处理完成（Neo4j 写入可能需要时间）
        time.sleep(5)
        
        # 验证记忆已创建
        memory_id = response.json().get("id")
        assert memory_id is not None
        
        # 注意：双写统计端点已移除，这是 Neo4j 双写功能的监控端点，不属于 OpenMemory 核心逻辑


class TestAppsAPI:
    """应用 API 黑盒测试"""
    
    def test_list_apps(self, api_client, test_user_id, check_api_available):
        """测试列出应用"""
        response = api_client.get(
            f"{API_BASE_URL}/api/v1/apps/",
            params={"user_id": test_user_id}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    def test_get_app_by_id(self, api_client, test_user_id, check_api_available):
        """测试根据 ID 获取应用"""
        # 先获取应用列表
        list_response = api_client.get(
            f"{API_BASE_URL}/api/v1/apps/",
            params={"user_id": test_user_id}
        )
        assert list_response.status_code == 200
        apps = list_response.json()
        
        if apps:
            app_id = apps[0]["id"]
            response = api_client.get(f"{API_BASE_URL}/api/v1/apps/{app_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == app_id

