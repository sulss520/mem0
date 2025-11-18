import json
import logging
import os
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from app.utils.prompts import MEMORY_CATEGORIZATION_PROMPT

load_dotenv()

# 超时配置
CATEGORIZATION_TIMEOUT = int(os.environ.get("CATEGORIZATION_TIMEOUT", "30"))

def _get_llm_config() -> Dict[str, Any]:
    """延迟导入 LLM 配置以避免循环导入"""
    from app.utils.memory import (
        OPENAI_BASE_URL,
        OPENAI_MODEL,
        OPENAI_API_KEY,
    )
    return {
        "provider": "openai",  # 所有兼容 OpenAI API 的服务都使用 openai provider
        "base_url": OPENAI_BASE_URL,
        "api_key": OPENAI_API_KEY,
        "model": OPENAI_MODEL or "gpt-4o-mini",
    }


def _normalize_base_url(base_url: Optional[str]) -> str:
    """规范化 base_url，确保格式正确"""
    if not base_url:
        return ""
    
    # 移除 /chat/completions 后缀（OpenAI SDK 会自动添加）
    if base_url.endswith("/chat/completions"):
        base_url = base_url[:-len("/chat/completions")]
    elif base_url.endswith("/chat/completions/"):
        base_url = base_url[:-len("/chat/completions/")]
    
    # 确保以 /v1 结尾（OpenAI 兼容格式）
    if not base_url.endswith("/v1"):
        if base_url.endswith("/"):
            base_url = base_url + "v1"
        else:
            base_url = base_url + "/v1"
    
    return base_url


def _get_default_base_url(provider: str) -> str:
    """根据 provider 获取默认 base_url"""
    defaults = {
        "openai": "https://api.openai.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "ollama": "http://localhost:11434/v1",
    }
    return defaults.get(provider, defaults["openai"])

# Initialize LLM client for categorization (lazy initialization).
llm_client: Optional[OpenAI] = None

def get_llm_client() -> OpenAI:
    """Get or initialize LLM client for categorization using unified LLM configuration."""
    global llm_client
    if llm_client is None:
        # 延迟获取配置以避免循环导入
        config = _get_llm_config()
        
        if not config["api_key"]:
            raise ValueError(
                "OPENAI_API_KEY must be set in environment variables"
            )
        
        provider = config["provider"]
        base_url = config["base_url"]
        api_key = config["api_key"]
        model = config["model"]
        
        # 规范化 base_url
        if base_url:
            base_url = _normalize_base_url(base_url)
        else:
            base_url = _get_default_base_url(provider)
        
        # Ollama 不需要真实的 API key
        if provider == "ollama":
            api_key = "ollama"
        
        # 初始化客户端（所有 provider 都使用 OpenAI 兼容的 API）
        llm_client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=CATEGORIZATION_TIMEOUT,
        )
        
        logging.info(f"Categorization LLM Client initialized: provider={provider}, base_url={base_url}, model={model}")
    
    return llm_client


class MemoryCategories(BaseModel):
    """用于验证分类结果的 Pydantic 模型"""
    categories: List[str]


def _parse_categories_response(response_content: Optional[str]) -> List[str]:
    """解析 LLM 返回的分类结果"""
    if not response_content:
        logging.warning("Empty response content from LLM")
        return []
    
    try:
        response_json = json.loads(response_content)
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse JSON response: {e}, content: {response_content[:200]}")
        raise ValueError(f"Invalid JSON response from LLM: {e}") from e
    
    if "categories" not in response_json:
        logging.warning(f"Response missing 'categories' key: {response_json}")
        return []
    
    categories = response_json["categories"]
    
    # 验证 categories 是否为列表
    if not isinstance(categories, list):
        logging.warning(f"Categories is not a list: {type(categories)}, value: {categories}")
        return []
    
    # 清理和规范化分类名称
    normalized_categories = []
    for cat in categories:
        if isinstance(cat, str):
            normalized = cat.strip().lower()
            if normalized:  # 忽略空字符串
                normalized_categories.append(normalized)
    
    return normalized_categories


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
def get_categories_for_memory(memory: str) -> List[str]:
    """
    使用 LLM 为记忆内容获取分类标签
    
    Args:
        memory: 需要分类的记忆内容
        
    Returns:
        分类标签列表（小写，已去空格）
        
    Raises:
        ValueError: 当 API key 未设置或响应格式无效时
        Exception: 当 LLM API 调用失败时（会自动重试3次）
    """
    if not memory or not memory.strip():
        logging.warning("Empty memory content provided for categorization")
        return []
    
    try:
        client = get_llm_client()
        
        # 获取模型配置
        config = _get_llm_config()
        model = config["model"]
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MEMORY_CATEGORIZATION_PROMPT},
                {"role": "user", "content": memory},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=CATEGORIZATION_TIMEOUT,
        )

        # 验证响应结构
        if not response.choices or len(response.choices) == 0:
            logging.error("Empty choices in LLM response")
            return []
        
        if not response.choices[0].message:
            logging.error("Empty message in LLM response")
            return []
        
        # 解析分类结果
        content = response.choices[0].message.content
        categories = _parse_categories_response(content)
        
        return categories
        
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error in categorization: {e}")
        raise
    except ValueError as e:
        # 重新抛出 ValueError（如 API key 未设置）
        logging.error(f"Value error in categorization: {e}")
        raise
    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        
        # 记录详细的错误信息
        logging.error(f"[ERROR] Failed to get categories: {error_type}: {error_msg}")
        
        # 如果是超时错误，提供更友好的提示
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            config = _get_llm_config()
            logging.warning(
                f"[WARNING] Categorization request timed out after {CATEGORIZATION_TIMEOUT}s. "
                f"Provider: {config['provider']}, Base URL: {config['base_url']}. "
                f"Consider increasing CATEGORIZATION_TIMEOUT or checking your OPENAI_API_KEY"
            )
        
        raise
