import logging
import os
import json
from typing import List

from app.utils.prompts import MEMORY_CATEGORIZATION_PROMPT
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

# 从环境变量获取配置
openai_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
openai_api_key = os.getenv("OPENAI_API_KEY")

# 初始化 OpenAI 客户端
openai_client = OpenAI(
    api_key=openai_api_key,
    base_url=openai_base_url
)


class MemoryCategories(BaseModel):
    categories: List[str]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
def get_categories_for_memory(memory: str) -> List[str]:
    """
    获取 Memory 的分类
    
    使用环境变量配置的模型（OPENAI_MODEL）和基础 URL（OPENAI_BASE_URL）
    如果模型支持 structured output（如 OpenAI），使用 parse 功能
    否则使用普通调用并手动解析 JSON
    """
    try:
        messages = [
            {"role": "system", "content": MEMORY_CATEGORIZATION_PROMPT},
            {"role": "user", "content": memory}
        ]

        logging.info(f"[Categorization] 使用模型: {openai_model}, Base URL: {openai_base_url}")
        
        # 尝试使用 structured output（如果支持）
        try:
            # 检查是否是 OpenAI API（支持 beta.chat.completions.parse）
            if "api.openai.com" in openai_base_url:
                completion = openai_client.beta.chat.completions.parse(
                    model=openai_model,
                    messages=messages,
                    response_format=MemoryCategories,
                    temperature=0
                )
                parsed: MemoryCategories = completion.choices[0].message.parsed
                categories = [cat.strip().lower() for cat in parsed.categories]
                logging.info(f"[Categorization] ✅ 使用 structured output 获取分类: {categories}")
                return categories
        except AttributeError:
            # 如果不支持 beta.chat.completions.parse，使用普通调用
            logging.debug("[Categorization] 模型不支持 structured output，使用普通调用")
        except Exception as parse_error:
            logging.warning(f"[Categorization] structured output 失败: {parse_error}，尝试普通调用")

        # 使用普通调用并手动解析 JSON
        completion = openai_client.chat.completions.create(
            model=openai_model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"}  # 请求 JSON 格式响应
        )

        content = completion.choices[0].message.content
        logging.debug(f"[Categorization] 原始响应: {content}")
        
        # 解析 JSON 响应
        try:
            response_data = json.loads(content)
            if "categories" in response_data:
                categories = [cat.strip().lower() for cat in response_data["categories"]]
            else:
                # 如果响应格式不同，尝试直接解析
                categories = [cat.strip().lower() for cat in response_data.get("category", [])] if isinstance(response_data.get("category"), list) else []
            
            if not categories:
                logging.warning(f"[Categorization] 未能从响应中提取分类，响应: {response_data}")
                return []
            
            logging.info(f"[Categorization] ✅ 获取分类成功: {categories}")
            return categories
        except json.JSONDecodeError as json_error:
            logging.error(f"[Categorization] JSON 解析失败: {json_error}, 原始内容: {content}")
            raise

    except Exception as e:
        logging.error(f"[ERROR] Failed to get categories: {e}")
        try:
            if 'completion' in locals():
                logging.debug(f"[DEBUG] Raw response: {completion.choices[0].message.content}")
        except Exception as debug_e:
            logging.debug(f"[DEBUG] Could not extract raw response: {debug_e}")
        raise
