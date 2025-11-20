"""
Neo4j 代理装饰器实现

通过装饰器模式拦截 Neo4jGraph.query 方法，所有操作（读和写）都通过 graphs_proxy HTTP API 执行。
graph_proxy 会处理：
- 读操作：负载均衡和故障转移
- 写操作：双写和故障转移

注意：如果 graph_proxy 不可用，操作会直接失败并抛出异常，不会降级到 Bolt 连接（保证数据一致性和双写机制）。

优点：
- 使用装饰器模式，代码更清晰
- 对 mem0 完全透明
- 所有操作统一通过代理，负载均匀分布
- 自动故障转移，提高可用性
- 线程安全的统计信息
- 避免单点压力，充分利用多实例
- 强制使用代理，保证数据一致性

缺点：
- 仍然依赖 langchain_neo4j 的内部实现
- 性能略低于直接 Bolt 连接（多一次 HTTP 请求）
- 代理不可用时无法降级，必须保证代理可用性（这是设计选择，保证数据一致性）
"""

import os
import logging
import requests
import threading
from typing import List, Dict, Any, Optional, Callable
from functools import wraps

logger = logging.getLogger(__name__)

# 配置
ENABLE_DUAL_WRITE = os.getenv("NEO4J_ENABLE_DUAL_WRITE", "true").lower() == "true"
DUAL_WRITE_HTTP_URL = os.getenv("NEO4J_PROXY_URL", "http://localhost:8090")
# 超时时间可配置，默认60秒（对于复杂查询可能需要更长时间）
DUAL_WRITE_TIMEOUT = int(os.getenv("NEO4J_DUAL_WRITE_TIMEOUT", "60"))

# 线程安全的统计信息
_stats_lock = threading.Lock()
DUAL_WRITE_STATS = {
    "read_total": 0,        # 读操作总数
    "read_success": 0,      # 读操作成功次数（通过代理）
    "read_errors": 0,       # 读操作错误次数
    "write_total": 0,       # 写操作总数
    "write_success": 0,     # 写操作成功次数（通过代理）
    "write_errors": 0       # 写操作错误次数
}

# 写操作关键字（用于判断是否需要双写）
# 使用更精确的匹配，避免误判
WRITE_KEYWORDS = ['CREATE', 'MERGE', 'SET', 'DELETE', 'DETACH DELETE', 'REMOVE']


def _update_stats(key: str, increment: int = 1):
    """线程安全地更新统计信息"""
    with _stats_lock:
        DUAL_WRITE_STATS[key] = DUAL_WRITE_STATS.get(key, 0) + increment


def should_dual_write(query: str) -> bool:
    """
    判断是否需要双写（只对写操作进行双写）
    
    使用更精确的判断逻辑，避免误判。
    例如："MATCH (n) WHERE n.name SET n.value = 1" 不会被误判为写操作
    
    Args:
        query: Cypher 查询语句
        
    Returns:
        bool: 是否需要双写
    """
    if not ENABLE_DUAL_WRITE:
        return False
    
    # 移除注释和多余空白
    query_clean = ' '.join(query.strip().split())
    if not query_clean:
        return False
    
    query_upper = query_clean.upper()
    
    # 检查是否以写操作关键字开头（更精确的判断）
    # 使用正则表达式或简单的字符串匹配
    for keyword in WRITE_KEYWORDS:
        # 检查是否以关键字开头，后面跟空格或换行
        if query_upper.startswith(keyword):
            # 确保关键字后面是空格、换行或查询结束
            next_char = query_upper[len(keyword):len(keyword)+1] if len(query_upper) > len(keyword) else ''
            if not next_char or next_char in (' ', '\n', '\t', '\r'):
                return True
    
    return False


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


def dual_write_decorator(original_method: Callable) -> Callable:
    """
    装饰器：为 Neo4jGraph.query 方法添加双写功能
    
    Args:
        original_method: 原始的 query 方法
        
    Returns:
        装饰后的方法
    """
    @wraps(original_method)
    def wrapper(
        self,
        query: str,
        params: dict = None,
        session_params: dict = None
    ) -> List[Dict[str, Any]]:
        """
        装饰后的 query 方法，实现双写功能
        """
        # 处理默认参数
        params = params or {}
        session_params = session_params or {}
        
        is_write = should_dual_write(query)
        
        # 所有操作都通过 graph_proxy 执行（读操作负载均衡，写操作双写）
        if is_write:
            _update_stats("write_total")
            logger.debug(f"🔄 写操作（通过代理）: {query[:100]}...")
        else:
            _update_stats("read_total")
            logger.debug(f"📖 读操作（通过代理，负载均衡）: {query[:100]}...")
        
        # 通过 HTTP API 执行（graph_proxy 会处理读操作的负载均衡和写操作的双写）
        http_result = execute_via_http_api(query, params)
        
        # HTTP API 成功，返回结果
        if http_result is not None:
            if is_write:
                _update_stats("write_success")
                logger.debug(f"✅ 写操作成功（通过代理）: {query[:50]}...")
            else:
                _update_stats("read_success")
                logger.debug(f"✅ 读操作成功（通过代理）: {query[:50]}...")
            return http_result
        
        # HTTP API 失败，直接抛出异常（不使用直连降级，保证数据一致性）
        if is_write:
            _update_stats("write_errors")
            operation_type = "写操作"
        else:
            _update_stats("read_errors")
            operation_type = "读操作"
        
        error_msg = f"{operation_type}失败: graph_proxy不可用 ({DUAL_WRITE_HTTP_URL})"
        logger.error(f"❌ {error_msg}")
        raise Exception(error_msg)
    
    return wrapper


def patch_neo4j_graph_query():
    """
    对 Neo4jGraph.query 方法进行装饰器模式的 Patch，实现双写功能
    """
    try:
        from langchain_neo4j.graphs.neo4j_graph import Neo4jGraph
        
        # 早期返回：如果已经 patch 过了，跳过
        if hasattr(Neo4jGraph, '_original_query'):
            logger.debug("Neo4jGraph.query 已经被 patch 过了，跳过")
            return
        
        # 保存原始方法
        _original_query = Neo4jGraph.query
        Neo4jGraph._original_query = _original_query
        
        # 使用装饰器模式应用 patch
        Neo4jGraph.query = dual_write_decorator(_original_query)
        
        logger.info("✅ Neo4jGraph.query 已成功 patch（装饰器模式），所有操作通过 graph_proxy")
        logger.info(f"   配置: ENABLE_DUAL_WRITE={ENABLE_DUAL_WRITE}, HTTP_URL={DUAL_WRITE_HTTP_URL}")
        logger.info(f"   读操作: 通过代理负载均衡 | 写操作: 通过代理双写")
        logger.info(f"   降级模式: 已禁用 (代理失败时直接抛出异常，保证数据一致性)")
        
    except ImportError as e:
        logger.warning(f"⚠️  无法导入 Neo4jGraph，跳过 patch: {e}")
    except Exception as e:
        logger.error(f"❌ Patch Neo4jGraph.query 失败: {e}", exc_info=True)


def get_dual_write_stats() -> Dict[str, int]:
    """获取双写统计信息（线程安全）"""
    with _stats_lock:
        return DUAL_WRITE_STATS.copy()


# 自动应用 patch（当模块被导入时）
patch_neo4j_graph_query()
