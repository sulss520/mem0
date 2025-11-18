"""
统一的 Embedder 超时补丁

在 EmbedderFactory 层面统一为所有 embedder 添加超时支持。
默认 60 秒，可通过 EMBEDDER_TIMEOUT 环境变量覆盖。
"""

import os
import httpx
from typing import Optional

try:
    from mem0.utils.factory import EmbedderFactory
except ImportError:
    EmbedderFactory = None


def _get_timeout_seconds(config):
    """获取超时配置（优先级：config > 环境变量 > 默认值）"""
    # 1. 从 config 中获取 timeout
    if config and isinstance(config, dict) and "timeout" in config:
        return int(config["timeout"])
    
    # 2. 从环境变量获取
    timeout_env = os.environ.get("EMBEDDER_TIMEOUT")
    if timeout_env:
        return int(timeout_env)
    
    # 3. 默认 60 秒
    return 60


def _get_api_key(embedder):
    """获取 API key（优先级：config > 环境变量）"""
    api_key = getattr(embedder.config, 'api_key', None)
    if api_key:
        return api_key
    return os.environ.get("OPENAI_API_KEY") or os.environ.get("EMBEDDER_API_KEY")


def _normalize_ollama_url(url, provider_name):
    """规范化 Ollama URL，确保有 /v1 路径"""
    if not url:
        return url
    url = url.rstrip('/')
    if provider_name == "ollama" and not url.endswith('/v1'):
        url = url + '/v1'
    return url


def _get_base_url_for_openai(client, embedder, provider_name):
    """获取 OpenAI 兼容 API 的 base_url"""
    # 优先使用 client 当前的 base_url（最准确）
    if hasattr(client, 'base_url'):
        current_base_url = str(client.base_url).rstrip('/')
        if current_base_url and current_base_url != 'https://api.openai.com/v1':
            return _normalize_ollama_url(current_base_url, provider_name)
    
    # 从 config 获取
    base_url = getattr(embedder.config, 'openai_base_url', None)
    if base_url:
        return _normalize_ollama_url(base_url, provider_name)
    
    # 尝试从 ollama_base_url 转换
    ollama_url = getattr(embedder.config, 'ollama_base_url', None)
    if ollama_url:
        return _normalize_ollama_url(ollama_url, provider_name)
    
    # 从环境变量获取
    base_url = os.environ.get("EMBEDDER_BASE_URL") or os.environ.get("OPENAI_EMBEDDING_MODEL_BASE_URL")
    if base_url:
        return _normalize_ollama_url(base_url, provider_name)
    
    # 默认值
    return "https://api.openai.com/v1"


def _apply_timeout_to_openai_client(embedder, provider_name, timeout_obj, timeout_seconds):
    """为 OpenAI SDK 客户端应用超时配置"""
    from openai import OpenAI
    
    api_key = _get_api_key(embedder)
    base_url = _get_base_url_for_openai(embedder.client, embedder, provider_name)
    
    embedder.client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_obj
    )
    print(f"✅ Embedder ({provider_name}) 超时已设置为: {timeout_seconds} 秒")
    print(f"   使用 base_url: {base_url}")


def _apply_timeout_to_ollama_client(embedder, provider_name, timeout_obj, timeout_seconds):
    """为 Ollama 客户端应用超时配置"""
    from ollama import Client
    
    base_url = getattr(embedder.config, 'ollama_base_url', None)
    embedder.client = Client(host=base_url, timeout=timeout_obj)
    print(f"✅ Embedder ({provider_name}) 超时已设置为: {timeout_seconds} 秒")


def patch_embedder_factory_timeout():
    """
    为 EmbedderFactory 添加统一的超时支持。
    
    在创建 embedder 后，统一检查并应用超时配置。
    """
    if EmbedderFactory is None:
        return
    
    # 保存原始的 create 方法
    original_create = EmbedderFactory.create
    
    @classmethod
    def create_with_timeout(cls, provider_name, config, vector_config=None):
        """带超时支持的 create 方法"""
        # 调用原始创建方法
        embedder = original_create(provider_name, config, vector_config)
        
        # 获取超时配置
        timeout_seconds = _get_timeout_seconds(config)
        
        # 早期返回：如果没有 client，无法设置超时
        if not hasattr(embedder, 'client'):
            return embedder
        
        timeout_obj = httpx.Timeout(timeout_seconds)
        client = embedder.client
        
        # 尝试为不同类型的客户端设置超时
        try:
            # OpenAI SDK（OpenAIEmbedding 使用的情况）
            if hasattr(client, 'embeddings') and type(client).__name__ == 'OpenAI':
                _apply_timeout_to_openai_client(embedder, provider_name, timeout_obj, timeout_seconds)
                return embedder
            
            # Ollama Client
            if hasattr(client, 'embeddings') or hasattr(client, 'list'):
                _apply_timeout_to_ollama_client(embedder, provider_name, timeout_obj, timeout_seconds)
                return embedder
                
        except Exception as e:
            # 如果设置超时失败，不影响功能，只记录警告
            import logging
            logging.warning(f"无法为 {provider_name} embedder 设置超时: {e}")
        
        return embedder
    
    # 应用 monkey patch
    EmbedderFactory.create = create_with_timeout


# 自动应用补丁
if EmbedderFactory is not None:
    patch_embedder_factory_timeout()

