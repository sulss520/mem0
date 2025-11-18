"""
Neo4j 双写 Monkey Patch

通过拦截 Neo4jGraph.query 方法，实现双写功能。
查询会先通过 graphs_proxy HTTP API 执行（支持双写和故障转移），
然后返回结果给 mem0。

优点：
- 不需要实现复杂的 Bolt 协议
- 代码简单（约 50 行）
- 对 mem0 完全透明
- 自动支持双写和故障转移

缺点：
- 使用 Monkey Patch，依赖 langchain_neo4j 的内部实现
- 性能略低于直接 Bolt 连接（多一次 HTTP 请求）
"""

import os
import logging
import requests
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 配置
ENABLE_DUAL_WRITE = os.getenv("NEO4J_ENABLE_DUAL_WRITE", "true").lower() == "true"
DUAL_WRITE_HTTP_URL = os.getenv("NEO4J_PROXY_URL", "http://localhost:8090")
DUAL_WRITE_TIMEOUT = int(os.getenv("NEO4J_DUAL_WRITE_TIMEOUT", "30"))
DUAL_WRITE_FALLBACK = os.getenv("NEO4J_DUAL_WRITE_FALLBACK", "true").lower() == "true"

# 统计信息（分开统计读写操作）
DUAL_WRITE_STATS = {
    "read_total": 0,      # 读操作总数
    "write_total": 0,    # 写操作总数
    "write_success": 0,  # 双写成功次数
    "write_fallback": 0, # 降级次数（HTTP失败时回退到Bolt）
    "write_errors": 0    # 写操作错误次数
}

# 写操作关键字（用于判断是否需要双写）
WRITE_KEYWORDS = ['CREATE', 'MERGE', 'SET', 'DELETE', 'DETACH DELETE', 'REMOVE']


def should_dual_write(query: str) -> bool:
    """
    判断是否需要双写（只对写操作进行双写）
    
    Args:
        query: Cypher 查询语句
        
    Returns:
        bool: 是否需要双写
    """
    if not ENABLE_DUAL_WRITE:
        return False
    
    query_upper = query.strip().upper()
    return any(query_upper.startswith(kw) for kw in WRITE_KEYWORDS)


def execute_via_http_api(query: str, params: dict = None) -> Optional[List[Dict[str, Any]]]:
    """
    通过 HTTP API 执行查询（支持双写和故障转移）
    
    Args:
        query: Cypher 查询语句
        params: 查询参数
        
    Returns:
        查询结果列表，如果失败返回 None
    """
    try:
        response = requests.post(
            f"{DUAL_WRITE_HTTP_URL}/cypher",
            json={
                "query": query,
                "parameters": params or {}
            },
            timeout=DUAL_WRITE_TIMEOUT
        )
        
        # 早期返回：非 200 状态码
        if response.status_code != 200:
            logger.warning(f"HTTP API 请求失败: {response.status_code} - {response.text}")
            return None
        
        result = response.json()
        if result.get("success"):
            # graphs_proxy 返回格式：{"success": True, "result": [...]}
            return result.get("result", [])
        
        # 早期返回：API 返回失败
        logger.warning(f"HTTP API 返回失败: {result.get('error', 'Unknown error')}")
        return None
            
    except requests.exceptions.Timeout:
        logger.warning(f"HTTP API 请求超时（{DUAL_WRITE_TIMEOUT}秒）")
        return None
    except requests.exceptions.ConnectionError:
        logger.warning(f"无法连接到 HTTP API: {DUAL_WRITE_HTTP_URL}")
        return None
    except Exception as e:
        logger.error(f"HTTP API 请求异常: {e}", exc_info=True)
        return None


def _handle_read_operation(_original_query, self, query: str, params: dict, session_params: dict):
    """处理读操作：直接使用原始方法"""
    DUAL_WRITE_STATS["read_total"] += 1
    return _original_query(self, query, params, session_params)


def _handle_write_operation(_original_query, self, query: str, params: dict, session_params: dict):
    """处理写操作：通过 HTTP API 双写，失败时降级"""
    DUAL_WRITE_STATS["write_total"] += 1
    logger.debug(f"🔄 双写查询: {query[:100]}...")
    
    http_result = execute_via_http_api(query, params)
    
    # HTTP API 成功，返回结果
    if http_result is not None:
        DUAL_WRITE_STATS["write_success"] += 1
        logger.debug(f"✅ 双写成功: {query[:50]}...")
        return http_result
    
    # HTTP API 失败，根据配置决定是否降级
    DUAL_WRITE_STATS["write_errors"] += 1
    
    if not DUAL_WRITE_FALLBACK:
        # 不降级，抛出异常
        raise Exception(f"双写失败且未启用降级模式: HTTP API 不可用")
    
    # 降级：使用原始 Bolt 连接（单实例）
    DUAL_WRITE_STATS["write_fallback"] += 1
    logger.warning(f"⚠️  双写失败，降级到单实例: {query[:50]}...")
    return _original_query(self, query, params, session_params)


def patch_neo4j_graph_query():
    """
    对 Neo4jGraph.query 方法进行 Monkey Patch，实现双写功能
    """
    try:
        from langchain_neo4j.graphs.neo4j_graph import Neo4jGraph
        
        # 早期返回：如果已经 patch 过了，跳过
        if hasattr(Neo4jGraph, '_original_query'):
            logger.debug("Neo4jGraph.query 已经被 patch 过了，跳过")
            return
        
        _original_query = Neo4jGraph.query
        Neo4jGraph._original_query = _original_query
        
        def patched_query(
            self,
            query: str,
            params: dict = {},
            session_params: dict = {}
        ) -> List[Dict[str, Any]]:
            """
            拦截的 query 方法，实现双写功能
            """
            # 读操作：直接使用原始方法
            if not should_dual_write(query):
                return _handle_read_operation(_original_query, self, query, params, session_params)
            
            # 写操作：通过 HTTP API 双写
            return _handle_write_operation(_original_query, self, query, params, session_params)
        
        # 应用 patch
        Neo4jGraph.query = patched_query
        logger.info("✅ Neo4jGraph.query 已成功 patch，启用双写功能")
        logger.info(f"   配置: ENABLE_DUAL_WRITE={ENABLE_DUAL_WRITE}, HTTP_URL={DUAL_WRITE_HTTP_URL}")
        logger.info(f"   降级模式: {DUAL_WRITE_FALLBACK}")
        
    except ImportError as e:
        logger.warning(f"⚠️  无法导入 Neo4jGraph，跳过 patch: {e}")
    except Exception as e:
        logger.error(f"❌ Patch Neo4jGraph.query 失败: {e}", exc_info=True)


def get_dual_write_stats() -> Dict[str, int]:
    """获取双写统计信息"""
    return DUAL_WRITE_STATS.copy()


# 自动应用 patch（当模块被导入时）
patch_neo4j_graph_query()

