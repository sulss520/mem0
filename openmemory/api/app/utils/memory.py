"""
Memory client utilities for OpenMemory.

This module provides functionality to initialize and manage the Mem0 memory client
with automatic configuration management and Docker environment support.

Docker Ollama Configuration:
When running inside a Docker container and using Ollama as the LLM or embedder provider,
the system automatically detects the Docker environment and adjusts localhost URLs
to properly reach the host machine where Ollama is running.

Supported Docker host resolution (in order of preference):
1. OLLAMA_HOST environment variable (if set)
2. host.docker.internal (Docker Desktop for Mac/Windows)
3. Docker bridge gateway IP (typically 172.17.0.1 on Linux)
4. Fallback to 172.17.0.1

Example configuration that will be automatically adjusted:
{
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "llama3.1:latest",
            "ollama_base_url": "http://localhost:11434"  # Auto-adjusted in Docker
        }
    }
}
"""

import hashlib
import json
import logging
import os
import socket

from app.database import SessionLocal
from app.models import Config as ConfigModel

from mem0 import Memory

logger = logging.getLogger(__name__)

# 导入 Neo4j 双写补丁（通过 HTTP API 实现双写，不修改 bolt_proxy）
try:
    from app.utils.neo4j_dual_write_patch import patch_neo4j_graph_query
    # patch 会在导入时自动应用
    pass
except ImportError:
    pass

_memory_client = None
_config_hash = None


def _get_config_hash(config_dict):
    """Generate a hash of the config to detect changes."""
    config_str = json.dumps(config_dict, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()


def _get_docker_host_url():
    """
    Determine the appropriate host URL to reach host machine from inside Docker container.
    Returns the best available option for reaching the host from inside a container.
    """
    # Check for custom environment variable first
    custom_host = os.environ.get('OLLAMA_HOST')
    if custom_host:
        logger.debug("Using custom Ollama host from OLLAMA_HOST environment variable")
        return custom_host.replace('http://', '').replace('https://', '').split(':')[0]
    
    # Check if we're running inside Docker
    if not os.path.exists('/.dockerenv'):
        # Not in Docker, return localhost as-is
        return "localhost"
    
    logger.debug("Detected Docker environment, adjusting host URL for Ollama")
    
    # Try different host resolution strategies
    host_candidates = []
    
    # 1. host.docker.internal (works on Docker Desktop for Mac/Windows)
    try:
        socket.gethostbyname('host.docker.internal')
        host_candidates.append('host.docker.internal')
        logger.debug("Found host.docker.internal")
    except socket.gaierror:
        pass
    
    # 2. Docker bridge gateway (typically 172.17.0.1 on Linux)
    try:
        with open('/proc/net/route', 'r') as f:
            for line in f:
                fields = line.strip().split()
                if fields[1] == '00000000':  # Default route
                    gateway_hex = fields[2]
                    gateway_ip = socket.inet_ntoa(bytes.fromhex(gateway_hex)[::-1])
                    host_candidates.append(gateway_ip)
                    logger.debug(f"Found Docker gateway: {gateway_ip}")
                    break
    except (FileNotFoundError, IndexError, ValueError):
        pass
    
    # 3. Fallback to common Docker bridge IP
    if not host_candidates:
        host_candidates.append('172.17.0.1')
        logger.debug("Using fallback Docker bridge IP: 172.17.0.1")
    
    # Return the first available candidate
    return host_candidates[0]


def _fix_ollama_urls(config_section):
    """
    Fix Ollama URLs for Docker environment.
    Replaces localhost URLs with appropriate Docker host URLs.
    Sets default ollama_base_url if not provided.
    """
    if not config_section or "config" not in config_section:
        return config_section
    
    ollama_config = config_section["config"]
    
    # Set default ollama_base_url if not provided
    if "ollama_base_url" not in ollama_config:
        ollama_config["ollama_base_url"] = "http://host.docker.internal:11434"
    else:
        # Check for ollama_base_url and fix if it's localhost
        url = ollama_config["ollama_base_url"]
        if "localhost" in url or "127.0.0.1" in url:
            docker_host = _get_docker_host_url()
            if docker_host != "localhost":
                new_url = url.replace("localhost", docker_host).replace("127.0.0.1", docker_host)
                ollama_config["ollama_base_url"] = new_url
                logger.debug(f"Adjusted Ollama URL for Docker environment")
    
    return config_section


def reset_memory_client():
    """Reset the global memory client to force reinitialization with new config."""
    global _memory_client, _config_hash
    _memory_client = None
    _config_hash = None


def _get_tencent_vectordb_config(default_collection_name: str) -> dict:
    """获取腾讯云向量数据库配置
    
    Args:
        default_collection_name: 默认集合名称
        
    Returns:
        dict: 腾讯云向量数据库配置字典
        
    Raises:
        ValueError: 如果必填配置项缺失
    """
    # 必填项验证
    url = os.environ.get('TENCENT_VECTORDB_URL')
    key = os.environ.get('TENCENT_VECTORDB_KEY')
    
    if not url:
        raise ValueError("TENCENT_VECTORDB_URL 环境变量未设置，这是必填项")
    if not key:
        raise ValueError("TENCENT_VECTORDB_KEY 环境变量未设置，这是必填项")
    
    # 可选配置项（支持环境变量配置，不硬编码默认值）
    username = os.environ.get('TENCENT_VECTORDB_USERNAME')
    if not username:
        raise ValueError("TENCENT_VECTORDB_USERNAME 环境变量未设置，这是必填项")
    
    config = {
        "url": url,
        "key": key,
        "username": username,
        "database_name": os.environ.get('TENCENT_VECTORDB_DATABASE_NAME', default_collection_name),
        "collection_name": os.environ.get('TENCENT_VECTORDB_COLLECTION_NAME', f"{default_collection_name}_collection"),
        "embedding_model_dims": int(os.environ.get('TENCENT_VECTORDB_EMBEDDING_MODEL_DIMS', '1536')),
        "metric_type": os.environ.get('TENCENT_VECTORDB_METRIC_TYPE', 'cosine'),
        "timeout": int(os.environ.get('TENCENT_VECTORDB_TIMEOUT', '60'))
    }
    
    # 验证metric_type
    valid_metrics = ['cosine', 'l2', 'ip']
    if config["metric_type"] not in valid_metrics:
        raise ValueError(f"TENCENT_VECTORDB_METRIC_TYPE 必须是以下之一: {', '.join(valid_metrics)}")
    
    return config


def _get_chroma_config() -> dict:
    """获取 Chroma 配置"""
    return {
        "host": os.environ.get('CHROMA_HOST'),
        "port": int(os.environ.get('CHROMA_PORT'))
    }


def _get_qdrant_config() -> dict:
    """获取 Qdrant 配置"""
    return {
        "host": os.environ.get('QDRANT_HOST'),
        "port": int(os.environ.get('QDRANT_PORT'))
    }


def _get_weaviate_config(default_collection_name: str) -> dict:
    """获取 Weaviate 配置"""
    cluster_url = os.environ.get('WEAVIATE_CLUSTER_URL')
    if not cluster_url:
        weaviate_host = os.environ.get('WEAVIATE_HOST')
        weaviate_port = int(os.environ.get('WEAVIATE_PORT'))
        cluster_url = f"http://{weaviate_host}:{weaviate_port}"
    return {
        "collection_name": default_collection_name,
        "cluster_url": cluster_url
    }


def _get_redis_config(default_collection_name: str) -> dict:
    """获取 Redis 配置"""
    return {
        "collection_name": default_collection_name,
        "redis_url": os.environ.get('REDIS_URL')
    }


def _get_pgvector_config() -> dict:
    """获取 PGVector 配置"""
    config = {
        "host": os.environ.get('PG_HOST'),
        "port": int(os.environ.get('PG_PORT')),
    }
    # 只在提供了值时才添加配置
    for key, env_key in [("dbname", "PG_DB"), ("user", "PG_USER"), ("password", "PG_PASSWORD")]:
        value = os.environ.get(env_key)
        if value:
            config[key] = value
    return config


def _get_milvus_config(default_collection_name: str) -> dict:
    """获取 Milvus 配置"""
    milvus_host = os.environ.get('MILVUS_HOST')
    milvus_port = int(os.environ.get('MILVUS_PORT'))
    return {
        "collection_name": default_collection_name,
        "url": f"http://{milvus_host}:{milvus_port}",
        "token": os.environ.get('MILVUS_TOKEN', ''),
        "db_name": os.environ.get('MILVUS_DB_NAME', ''),
        "embedding_model_dims": int(os.environ.get('MILVUS_EMBEDDING_DIMS', '1536')),
        "metric_type": "COSINE"
    }


def _get_elasticsearch_config() -> dict:
    """获取 Elasticsearch 配置"""
    elasticsearch_host = os.environ.get('ELASTICSEARCH_HOST')
    elasticsearch_port = int(os.environ.get('ELASTICSEARCH_PORT'))
    config = {
        "host": f"http://{elasticsearch_host}",
        "port": elasticsearch_port,
        "verify_certs": False,
        "use_ssl": False,
        "embedding_model_dims": int(os.environ.get('ELASTICSEARCH_EMBEDDING_DIMS', '1536'))
    }
    # 只在提供了用户名和密码时才添加认证信息
    for key, env_key in [("user", "ELASTICSEARCH_USER"), ("password", "ELASTICSEARCH_PASSWORD")]:
        value = os.environ.get(env_key)
        if value:
            config[key] = value
    return config


def _get_opensearch_config() -> dict:
    """获取 OpenSearch 配置"""
    return {
        "host": os.environ.get('OPENSEARCH_HOST'),
        "port": int(os.environ.get('OPENSEARCH_PORT'))
    }


def _get_faiss_config(default_collection_name: str) -> dict:
    """获取 FAISS 配置"""
    return {
        "collection_name": default_collection_name,
        "path": os.environ.get('FAISS_PATH'),
        "embedding_model_dims": int(os.environ.get('FAISS_EMBEDDING_DIMS', '1536')),
        "distance_strategy": "cosine"
    }


def _get_default_qdrant_config() -> dict:
    """获取默认 Qdrant 配置（fallback）"""
    return {
        "port": int(os.environ.get('QDRANT_PORT', '6333'))
    }


def _detect_vector_store_config(default_collection_name: str) -> tuple[str, dict]:
    """
    检测并配置向量数据库
    
    Args:
        default_collection_name: 默认集合名称
        
    Returns:
        tuple: (provider, config)
        
    Raises:
        ValueError: 如果配置验证失败
    """
    # 优先检查是否明确指定了向量数据库 Provider
    explicit_provider = os.environ.get('VECTOR_STORE_PROVIDER')
    
    # Tencent VectorDB（最高优先级）
    if explicit_provider == 'tencent_vectordb' or (
        os.environ.get('TENCENT_VECTORDB_URL') and os.environ.get('TENCENT_VECTORDB_KEY')
    ):
        try:
            return "tencent_vectordb", _get_tencent_vectordb_config(default_collection_name)
        except ValueError as e:
            logger.error(f"腾讯云向量数据库配置验证失败: {e}")
            raise
    
    # Chroma
    if os.environ.get('CHROMA_HOST') and os.environ.get('CHROMA_PORT'):
        return "chroma", _get_chroma_config()
    
    # Qdrant
    if os.environ.get('QDRANT_HOST') and os.environ.get('QDRANT_PORT'):
        return "qdrant", _get_qdrant_config()
    
    # Weaviate
    if os.environ.get('WEAVIATE_CLUSTER_URL') or (
        os.environ.get('WEAVIATE_HOST') and os.environ.get('WEAVIATE_PORT')
    ):
        return "weaviate", _get_weaviate_config(default_collection_name)
    
    # Redis
    if os.environ.get('REDIS_URL'):
        return "redis", _get_redis_config(default_collection_name)
    
    # PGVector
    if os.environ.get('PG_HOST') and os.environ.get('PG_PORT'):
        return "pgvector", _get_pgvector_config()
    
    # Milvus
    if os.environ.get('MILVUS_HOST') and os.environ.get('MILVUS_PORT'):
        return "milvus", _get_milvus_config(default_collection_name)
    
    # Elasticsearch
    if os.environ.get('ELASTICSEARCH_HOST') and os.environ.get('ELASTICSEARCH_PORT'):
        return "elasticsearch", _get_elasticsearch_config()
    
    # OpenSearch
    if os.environ.get('OPENSEARCH_HOST') and os.environ.get('OPENSEARCH_PORT'):
        return "opensearch", _get_opensearch_config()
    
    # FAISS
    if os.environ.get('FAISS_PATH'):
        return "faiss", _get_faiss_config(default_collection_name)
    
    # 默认 fallback 到 Qdrant
    return "qdrant", _get_default_qdrant_config()


def _get_llm_config() -> tuple[str, dict]:
    """获取 LLM 配置"""
    provider = os.environ.get('LLM_PROVIDER', 'openai')
    config = {
        "model": os.environ.get('OPENAI_MODEL', 'gpt-4o-mini'),
        "temperature": float(os.environ.get('LLM_TEMPERATURE', '0.1')),
        "max_tokens": int(os.environ.get('LLM_MAX_TOKENS', '2000')),
        "api_key": "env:OPENAI_API_KEY"
    }
    
    # 如果配置了自定义 Base URL，添加到配置中
    base_url = os.environ.get('OPENAI_BASE_URL')
    if base_url:
        config["base_url"] = base_url
    
    return provider, config


def _get_embedder_config() -> tuple[str, dict]:
    """获取 Embedder 配置"""
    provider = os.environ.get('EMBEDDER_PROVIDER', 'openai')
    embedder_dims = int(os.environ.get('EMBEDDER_DIMS', '1536'))
    
    config = {
        "model": os.environ.get('EMBEDDER_MODEL', 'text-embedding-3-small'),
        "api_key": "env:OPENAI_API_KEY"
    }
    
    # 如果配置了自定义 Base URL，添加到配置中
    base_url = os.environ.get('EMBEDDER_BASE_URL')
    if base_url:
        config["base_url"] = base_url
    
    # 如果 Embedder 是 Ollama，添加维度配置
    if provider == 'ollama' and embedder_dims:
        config["embedding_model_dims"] = embedder_dims
    
    return provider, config


def get_default_memory_config():
    """Get default memory client configuration with sensible defaults."""
    # 默认向量数据库主机和集合名称（可通过环境变量覆盖）
    default_vector_store_host = os.environ.get('VECTOR_STORE_HOST', 'mem0_store')
    default_collection_name = os.environ.get('VECTOR_STORE_COLLECTION_NAME', 'openmemory')
    
    # 基础配置
    base_vector_store_config = {
        "collection_name": default_collection_name,
        "host": default_vector_store_host,
    }
    
    # 检测并配置向量数据库
    vector_store_provider, vector_store_config = _detect_vector_store_config(default_collection_name)
    
    # 如果检测到的配置没有覆盖基础配置，合并它们
    if "collection_name" not in vector_store_config:
        vector_store_config.setdefault("collection_name", default_collection_name)
    if "host" not in vector_store_config and vector_store_provider != "tencent_vectordb":
        vector_store_config.setdefault("host", default_vector_store_host)
    
    logger.info(f"Auto-detected vector store: {vector_store_provider}")
    
    # 获取 LLM 和 Embedder 配置
    llm_provider, llm_config = _get_llm_config()
    embedder_provider, embedder_config = _get_embedder_config()
    
    return {
        "vector_store": {
            "provider": vector_store_provider,
            "config": vector_store_config
        },
        "llm": {
            "provider": llm_provider,
            "config": llm_config
        },
        "embedder": {
            "provider": embedder_provider,
            "config": embedder_config
        },
        "version": "v1.1"
    }


def _parse_environment_variables(config_dict):
    """
    Parse environment variables in config values.
    Converts 'env:VARIABLE_NAME' to actual environment variable values.
    """
    if isinstance(config_dict, dict):
        parsed_config = {}
        for key, value in config_dict.items():
            if isinstance(value, str) and value.startswith("env:"):
                env_var = value.split(":", 1)[1]
                env_value = os.environ.get(env_var)
                if env_value:
                    parsed_config[key] = env_value
                    # 避免在日志中输出敏感信息（API key、密码等）
                    if any(sensitive in key.lower() for sensitive in ['key', 'password', 'secret', 'token', 'api_key']):
                        logger.debug(f"Loaded environment variable {env_var} for {key} (sensitive, value hidden)")
                    else:
                        logger.debug(f"Loaded environment variable {env_var} for {key}")
                else:
                    logger.warning(f"Environment variable {env_var} not found for {key}, keeping original value")
                    parsed_config[key] = value
            elif isinstance(value, dict):
                parsed_config[key] = _parse_environment_variables(value)
            else:
                parsed_config[key] = value
        return parsed_config
    return config_dict


def get_memory_client(custom_instructions: str = None):
    """
    Get or initialize the Mem0 client.

    Args:
        custom_instructions: Optional instructions for the memory project.

    Returns:
        Initialized Mem0 client instance or None if initialization fails.

    Raises:
        Exception: If required API keys are not set or critical configuration is missing.
    """
    global _memory_client, _config_hash

    try:
        # Start with default configuration
        config = get_default_memory_config()
        
        # Variable to track custom instructions
        db_custom_instructions = None
        
        # Load configuration from database
        db = None
        try:
            db = SessionLocal()
            db_config = db.query(ConfigModel).filter(ConfigModel.key == "main").first()
            
            if db_config:
                json_config = db_config.value
                
                # Extract custom instructions from openmemory settings
                if "openmemory" in json_config and "custom_instructions" in json_config["openmemory"]:
                    db_custom_instructions = json_config["openmemory"]["custom_instructions"]
                
                # Override defaults with configurations from the database
                if "mem0" in json_config:
                    mem0_config = json_config["mem0"]
                    
                    # Update LLM configuration if available
                    if "llm" in mem0_config and mem0_config["llm"] is not None:
                        config["llm"] = mem0_config["llm"]
                        
                        # Fix Ollama URLs for Docker if needed
                        if config["llm"].get("provider") == "ollama":
                            config["llm"] = _fix_ollama_urls(config["llm"])
                    
                    # Update Embedder configuration if available
                    if "embedder" in mem0_config and mem0_config["embedder"] is not None:
                        config["embedder"] = mem0_config["embedder"]
                        
                        # Fix Ollama URLs for Docker if needed
                        if config["embedder"].get("provider") == "ollama":
                            config["embedder"] = _fix_ollama_urls(config["embedder"])

                    if "vector_store" in mem0_config and mem0_config["vector_store"] is not None:
                        config["vector_store"] = mem0_config["vector_store"]
            else:
                logger.debug("No configuration found in database, using defaults")
                            
        except Exception as e:
            # 统一错误处理：记录详细错误信息，但不中断流程
            logger.error(
                f"Error loading configuration from database: {type(e).__name__}: {str(e)}",
                exc_info=True
            )
            logger.info("Using default configuration as fallback")
            # Continue with default configuration if database config can't be loaded
        finally:
            # 确保数据库连接总是被关闭
            if db is not None:
                try:
                    db.close()
                except Exception as close_error:
                    logger.warning(f"Error closing database connection: {close_error}", exc_info=True)

        # Use custom_instructions parameter first, then fall back to database value
        instructions_to_use = custom_instructions or db_custom_instructions
        if instructions_to_use:
            config["custom_fact_extraction_prompt"] = instructions_to_use

        # ALWAYS parse environment variables in the final config
        # This ensures that even default config values like "env:OPENAI_API_KEY" get parsed
        logger.debug("Parsing environment variables in final config")
        config = _parse_environment_variables(config)

        # Check if config has changed by comparing hashes
        current_config_hash = _get_config_hash(config)
        
        # Only reinitialize if config changed or client doesn't exist
        if _memory_client is None or _config_hash != current_config_hash:
            logger.info(f"Initializing memory client with config hash: {current_config_hash[:8]}...")
            try:
                _memory_client = Memory.from_config(config_dict=config)
                _config_hash = current_config_hash
                logger.info("Memory client initialized successfully")
            except Exception as init_error:
                logger.error(f"Failed to initialize memory client: {init_error}", exc_info=True)
                logger.warning("Server will continue running with limited memory functionality")
                _memory_client = None
                _config_hash = None
                return None
        
        return _memory_client
        
    except Exception as e:
        logger.error(f"Exception occurred while initializing memory client: {e}", exc_info=True)
        logger.warning("Server will continue running with limited memory functionality")
        return None


def get_default_user_id():
    return "default_user"
