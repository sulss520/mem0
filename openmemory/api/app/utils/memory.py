"""
Memory client utilities for OpenMemory.

This module provides functionality to initialize and manage the Mem0 memory client
with automatic configuration management.
"""

import hashlib
import json
import logging
import os
import sys

from app.database import SessionLocal
from app.models import Config as ConfigModel

# 导入统一的超时补丁（支持所有 embedder，默认 60 秒）
try:
    from app.utils.embedder_timeout_patch import patch_embedder_factory_timeout
    patch_embedder_factory_timeout()
except ImportError:
    pass

# 导入 Neo4j 双写补丁（通过 HTTP API 实现双写，不修改 bolt_proxy）
try:
    from app.utils.neo4j_dual_write_patch import patch_neo4j_graph_query
    # patch 会在导入时自动应用
    pass
except ImportError:
    pass

from mem0 import Memory

_memory_client = None
_config_hash = None

# LLM 配置（与 mem0 主分支保持一致，使用 OPENAI_* 环境变量）
# 注意：虽然变量名为 OPENAI_*，但可以用于任何兼容 OpenAI API 格式的服务
# 例如：Ollama (http://localhost:11434/v1)、自定义代理、DeepSeek 等
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Embedder 配置（支持多种 provider）
EMBEDDER_PROVIDER = os.environ.get("EMBEDDER_PROVIDER", "openai")
EMBEDDER_MODEL = os.environ.get("EMBEDDER_MODEL")
EMBEDDER_BASE_URL = os.environ.get("EMBEDDER_BASE_URL")
EMBEDDER_API_KEY = os.environ.get("EMBEDDER_API_KEY")
EMBEDDER_DIMS = os.environ.get("EMBEDDER_DIMS")
EMBEDDER_TIMEOUT = os.environ.get("EMBEDDER_TIMEOUT")  # 超时时间（秒），默认 60 秒

# 如果使用 Ollama 的 OpenAI 兼容 API，需要添加 /v1 路径
# 如果使用 Ollama 原生 API，不需要 /v1 路径
_embedder_base_url = EMBEDDER_BASE_URL or os.environ.get("OPENAI_EMBEDDING_MODEL_BASE_URL")
if _embedder_base_url and EMBEDDER_PROVIDER == "ollama" and not _embedder_base_url.endswith("/v1"):
    # 如果使用 ollama provider，但 base_url 没有 /v1，说明使用原生 API，不需要修改
    # 但如果 mem0 选择了 openai embedder，则需要 /v1
    pass  # 保持原样，让后续逻辑处理

OPENAI_EMBEDDING_MODEL_BASE_URL = _embedder_base_url or "https://api.openai.com/v1"
OPENAI_EMBEDDING_MODEL_API_KEY = EMBEDDER_API_KEY or os.environ.get(
    "OPENAI_EMBEDDING_MODEL_API_KEY", OPENAI_API_KEY
)
OPENAI_EMBEDDING_MODEL = EMBEDDER_MODEL or os.environ.get(
    "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
)

# 计算 embedding 维度（优先级从高到低）：
# 1. 优先使用环境变量 EMBEDDER_DIMS（推荐）
# 2. 其次使用环境变量 OPENAI_EMBEDDING_MODEL_DIMS（向后兼容）
# 3. 如果都没有设置，使用默认值 1536
# 注意：如果维度配置错误，向量数据库会在插入/搜索时直接报错，错误信息会包含详细的维度信息
OPENAI_EMBEDDING_MODEL_DIMS = int(
    EMBEDDER_DIMS 
    or os.environ.get("OPENAI_EMBEDDING_MODEL_DIMS", "1536")
)

# Graph Store 配置（Neo4j）
# 代理模式配置（推荐，使用 graphs_proxy）
NEO4J_USE_PROXY = os.environ.get("NEO4J_USE_PROXY", "false").lower() == "true"
NEO4J_PROXY_URL = os.environ.get("NEO4J_PROXY_URL")
# 直连模式配置（备选）
NEO4J_URL = os.environ.get("NEO4J_URL")
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
# 清理数据库名称（去除可能的额外内容）
_neo4j_database_raw = os.environ.get("NEO4J_DATABASE", "neo4j")
# 如果包含异常内容（如环境变量定义），只取第一部分（在遇到大写字母或等号之前）
if _neo4j_database_raw and "NEO4J_URL" in _neo4j_database_raw:
    # 如果包含环境变量定义，只取 "neo4j" 部分
    NEO4J_DATABASE = "neo4j"
elif _neo4j_database_raw:
    # 去除空格和换行，只取第一个单词
    NEO4J_DATABASE = _neo4j_database_raw.strip().split()[0] if _neo4j_database_raw.strip() else "neo4j"
else:
    NEO4J_DATABASE = "neo4j"


def _get_config_hash(config_dict):
    """Generate a hash of the config to detect changes."""
    config_str = json.dumps(config_dict, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()


def reset_memory_client():
    """Reset the global memory client to force reinitialization with new config."""
    global _memory_client, _config_hash
    _memory_client = None
    _config_hash = None


def get_default_memory_config():
    """Get default memory client configuration with sensible defaults.
    
    与 mem0 主干保持一致：不设置 vector_store，由数据库配置或 Pydantic 默认值处理。
    参考 mem0/server/main.py 的实现方式：直接构建配置字典，明确指定 provider。
    """
    # 注意：不设置 vector_store，让数据库配置或 Pydantic 默认值处理（与 mem0 主干一致）

    # 构建 LLM 配置（与 mem0 主分支保持一致）
    # 使用 OPENAI_* 环境变量，支持所有兼容 OpenAI API 格式的服务
    # 注意：provider 固定为 "openai"，因为所有兼容 OpenAI API 的服务都使用 openai provider
    llm_config = {
        "provider": "openai",
        "config": {
            "model": OPENAI_MODEL,
            "base_url": OPENAI_BASE_URL,
            "api_key": OPENAI_API_KEY,
            # temperature、max_tokens、top_p 使用 LLMConfig 的默认值
            # 如果需要自定义，请在数据库配置中设置
        },
    }

    # 构建 Embedder 配置
    embedder_config = {
        "provider": EMBEDDER_PROVIDER,
        "config": {
            "api_key": EMBEDDER_API_KEY or OPENAI_EMBEDDING_MODEL_API_KEY,
            "model": EMBEDDER_MODEL or OPENAI_EMBEDDING_MODEL,
            "embedding_dims": OPENAI_EMBEDDING_MODEL_DIMS,
        },
    }
    
    # 注意：timeout 不能直接传递给 BaseEmbedderConfig，因为 BaseEmbedderConfig 不接受 timeout 参数
    # timeout 配置会在超时补丁中从环境变量读取，不需要在这里设置
    
    # 根据 provider 设置 base_url
    # 注意：如果 mem0 选择了 openai embedder（即使配置了 ollama provider），
    # 需要使用 openai_base_url 并添加 /v1 路径（Ollama 的 OpenAI 兼容 API）
    if EMBEDDER_BASE_URL:
        if EMBEDDER_PROVIDER == "openai":
            embedder_config["config"]["openai_base_url"] = EMBEDDER_BASE_URL
        elif EMBEDDER_PROVIDER == "ollama":
            embedder_config["config"]["ollama_base_url"] = EMBEDDER_BASE_URL
        else:
            embedder_config["config"]["base_url"] = EMBEDDER_BASE_URL
    elif EMBEDDER_PROVIDER == "openai":
        embedder_config["config"]["openai_base_url"] = OPENAI_EMBEDDING_MODEL_BASE_URL
    elif EMBEDDER_PROVIDER == "ollama":
        embedder_config["config"]["ollama_base_url"] = "http://localhost:11434"

    # 调试日志：输出 embedder 配置
    logging.info("=" * 80)
    logging.info("🔍 Embedder 配置信息:")
    logging.info(f"   Provider: {embedder_config.get('provider')}")
    logging.info(f"   Model: {embedder_config.get('config', {}).get('model')}")
    logging.info(f"   Base URL: {embedder_config.get('config', {}).get('ollama_base_url') or embedder_config.get('config', {}).get('openai_base_url')}")
    logging.info(f"   Embedding Dims: {embedder_config.get('config', {}).get('embedding_dims')}")
    logging.info(f"   Timeout: {embedder_config.get('config', {}).get('timeout', '未设置')}")
    logging.info("=" * 80)

    # 构建 Graph Store 配置（Neo4j）
    graph_store_config = None
    
    # Neo4j 配置策略：
    # 1. 如果启用了双写模式（NEO4J_ENABLE_DUAL_WRITE=true），使用 HTTP API 双写
    #    - mem0 直接连接到 Neo4j（作为降级备用）
    #    - 写操作通过 HTTP API 执行（支持双写和故障转移）
    # 2. 如果启用了 Bolt 代理（NEO4J_BOLT_PROXY_URL），使用 Bolt 代理
    # 3. 否则，使用直连模式
    
    enable_dual_write = os.environ.get("NEO4J_ENABLE_DUAL_WRITE", "true").lower() == "true"
    bolt_proxy_url = os.environ.get("NEO4J_BOLT_PROXY_URL")  # 例如: bolt://localhost:7688
    
    if enable_dual_write and NEO4J_PROXY_URL:
        # 双写模式：mem0 直接连接 Neo4j（作为降级备用），写操作通过 HTTP API 双写
        # 优先使用环境变量，否则根据运行环境选择默认值
        neo4j_url = os.environ.get("NEO4J_URL")
        if not neo4j_url:
            # 检查是否在 Docker 环境中（通过检查 /proc/self/cgroup 或环境变量）
            is_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "true"
            if is_docker:
                neo4j_url = "bolt://neo4j_primary:7687"  # Docker 容器名称
            else:
                neo4j_url = "bolt://localhost:7687"  # 本地环境
        graph_store_config = {
            "provider": "neo4j",
            "config": {
                "url": neo4j_url,  # 直连 Neo4j（降级备用）
                "username": NEO4J_USERNAME or "neo4j",
                "password": NEO4J_PASSWORD,
                "database": NEO4J_DATABASE,
            },
        }
        print(f"✅ [GRAPH STORE] Auto-detected graph store: neo4j (双写模式 - HTTP API)")
        print(f"   直连地址（降级备用）: {neo4j_url}")
        print(f"   HTTP 代理地址（双写）: {NEO4J_PROXY_URL}")
        print(f"   用户名: {NEO4J_USERNAME or 'neo4j'}")
        print(f"   数据库: {NEO4J_DATABASE}")
        print(f"   💡 提示: 写操作通过 HTTP API 双写，读操作和降级时使用直连")
    elif bolt_proxy_url:
        # Bolt 代理模式：通过 Bolt 代理连接
        graph_store_config = {
            "provider": "neo4j",
            "config": {
                "url": bolt_proxy_url,  # Bolt 代理端口
                "username": NEO4J_USERNAME or "neo4j",
                "password": NEO4J_PASSWORD,
                "database": NEO4J_DATABASE,
            },
        }
        print(f"✅ [GRAPH STORE] Auto-detected graph store: neo4j (代理模式 - Bolt 代理)")
        print(f"   Bolt 代理地址: {bolt_proxy_url}")
        print(f"   HTTP 代理地址: {NEO4J_PROXY_URL or '未配置'}")
        print(f"   用户名: {NEO4J_USERNAME or 'neo4j'}")
        print(f"   数据库: {NEO4J_DATABASE}")
        print(f"   💡 提示: 通过 graphs_proxy Bolt 代理连接，支持双写和故障转移")
    elif NEO4J_USE_PROXY and NEO4J_PROXY_URL:
        # 旧版代理模式（兼容性）
        neo4j_url = os.environ.get("NEO4J_URL", "bolt://neo4j_primary:7687")
        graph_store_config = {
            "provider": "neo4j",
            "config": {
                "url": neo4j_url,
                "username": NEO4J_USERNAME or "neo4j",
                "password": NEO4J_PASSWORD,
                "database": NEO4J_DATABASE,
            },
        }
        print(f"⚠️  [GRAPH STORE] 代理模式已启用，但未配置双写或 Bolt 代理")
        print(f"   使用直连模式连接到 Neo4j: {neo4j_url}")
        print(f"   💡 提示: 建议设置 NEO4J_ENABLE_DUAL_WRITE=true 启用双写模式")
    # 备选：直连模式（不使用代理）
    elif NEO4J_URL and NEO4J_USERNAME and NEO4J_PASSWORD:
        graph_store_config = {
            "provider": "neo4j",
            "config": {
                "url": NEO4J_URL,
                "username": NEO4J_USERNAME,
                "password": NEO4J_PASSWORD,
                "database": NEO4J_DATABASE,
            },
        }
        print(f"✅ [GRAPH STORE] Auto-detected graph store: neo4j (直连模式)")
        print(f"   URL: {NEO4J_URL}")
        print(f"   Username: {NEO4J_USERNAME}")
        print(f"   Database: {NEO4J_DATABASE}")
    else:
        print("ℹ️  [GRAPH STORE] Graph store 未配置")
        if NEO4J_USE_PROXY and not NEO4J_PROXY_URL:
            print("   ⚠️  已启用代理模式但未配置 NEO4J_PROXY_URL")
        elif not NEO4J_USE_PROXY and not NEO4J_URL:
            print("   ⚠️  未配置 NEO4J_URL 或 NEO4J_PROXY_URL")

    config_dict = {
        "llm": llm_config,
        "embedder": embedder_config,
        "version": "v1.1",
    }
    
    # 只有在配置了 Neo4j 时才添加 graph_store
    if graph_store_config:
        config_dict["graph_store"] = graph_store_config
    
    # 注意：不设置 vector_store，由数据库配置或 Pydantic 默认值处理（与 mem0 主干一致）
    return config_dict


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
                    print(f"Loaded {env_var} from environment for {key}")
                else:
                    print(
                        f"Warning: Environment variable {env_var} not found, keeping original value"
                    )
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
        try:
            db = SessionLocal()
            db_config = db.query(ConfigModel).filter(ConfigModel.key == "main").first()

            if db_config:
                json_config = db_config.value

                # Extract custom instructions from openmemory settings
                if (
                    "openmemory" in json_config
                    and "custom_instructions" in json_config["openmemory"]
                ):
                    db_custom_instructions = json_config["openmemory"][
                        "custom_instructions"
                    ]

                # Override defaults with configurations from the database
                # 与 mem0 主干一致：数据库配置优先，直接使用配置字典，由 Pydantic 验证
                if "mem0" in json_config:
                    mem0_config = json_config["mem0"]

                    # Update LLM configuration if available
                    # 数据库配置优先（与 mem0 主干一致）
                    if "llm" in mem0_config and mem0_config["llm"] is not None:
                        config["llm"] = mem0_config["llm"]
                        provider = config["llm"].get("provider", "未指定")
                        print(f"✅ [CONFIG] 使用数据库中的 llm 配置 (provider: {provider})")

                    # Update Embedder configuration if available
                    # 数据库配置优先（与 mem0 主干一致）
                    if (
                        "embedder" in mem0_config
                        and mem0_config["embedder"] is not None
                    ):
                        config["embedder"] = mem0_config["embedder"]
                        provider = config["embedder"].get("provider", "未指定")
                        print(f"✅ [CONFIG] 使用数据库中的 embedder 配置 (provider: {provider})")

                    # Vector Store 配置：数据库配置优先（与 mem0 主干一致）
                    # mem0 主干的逻辑：直接使用配置字典中的 vector_store，由 Pydantic 验证
                    if (
                        "vector_store" in mem0_config
                        and mem0_config["vector_store"] is not None
                    ):
                        config["vector_store"] = mem0_config["vector_store"]
                        provider = "未指定"
                        if isinstance(mem0_config["vector_store"], dict):
                            provider = mem0_config["vector_store"].get("provider", "未指定")
                        print(f"✅ [CONFIG] 使用数据库中的 vector_store 配置 (provider: {provider})")
                    
                    # Graph Store 配置：数据库配置优先（与 mem0 主干一致）
                    if (
                        "graph_store" in mem0_config
                        and mem0_config["graph_store"] is not None
                    ):
                        config["graph_store"] = mem0_config["graph_store"]
                        provider = "未指定"
                        if isinstance(mem0_config["graph_store"], dict):
                            provider = mem0_config["graph_store"].get("provider", "未指定")
                        print(f"✅ [CONFIG] 使用数据库中的 graph_store 配置 (provider: {provider})")
            else:
                print("No configuration found in database, using defaults")

            db.close()

        except Exception as e:
            print(f"Warning: Error loading configuration from database: {e}")
            print("Using default configuration")
            # Continue with default configuration if database config can't be loaded

        # Use custom_instructions parameter first, then fall back to database value
        instructions_to_use = custom_instructions or db_custom_instructions
        if instructions_to_use:
            config["custom_fact_extraction_prompt"] = instructions_to_use

        # ALWAYS parse environment variables in the final config
        # This ensures that even default config values like "env:OPENAI_API_KEY" get parsed
        print("Parsing environment variables in final config...")
        config = _parse_environment_variables(config)

        # Check if config has changed by comparing hashes
        current_config_hash = _get_config_hash(config)

        # Only reinitialize if config changed or client doesn't exist
        if _memory_client is None or _config_hash != current_config_hash:
            print(f"🔄 [MEMORY CLIENT] Initializing memory client with config hash: {current_config_hash}")
            try:
                _memory_client = Memory.from_config(config_dict=config)
                _config_hash = current_config_hash
                
                # 验证 graph store 状态
                enable_graph = getattr(_memory_client, 'enable_graph', False)
                has_graph = getattr(_memory_client, 'graph', None) is not None
                
                print("✅ [MEMORY CLIENT] Memory client initialized successfully")
                print(f"   enable_graph: {enable_graph}")
                print(f"   graph 实例存在: {has_graph}")
                
                if enable_graph and has_graph:
                    print("   ✅ Graph store 已启用并初始化成功")
                elif enable_graph:
                    print("   ⚠️  Graph store 配置已启用但实例未创建")
                else:
                    print("   ℹ️  Graph store 未启用")
                    
            except Exception as init_error:
                import traceback
                error_trace = traceback.format_exc()
                print(f"❌ [MEMORY CLIENT] Failed to initialize memory client: {init_error}")
                print(f"   错误堆栈:\n{error_trace}")
                print("⚠️  Server will continue running with limited memory functionality")
                _memory_client = None
                _config_hash = None
                return None

        return _memory_client

    except Exception as e:
        print(f"Warning: Exception occurred while initializing memory client: {e}")
        print("Server will continue running with limited memory functionality")
        return None


def get_default_user_id():
    return "default_user"
