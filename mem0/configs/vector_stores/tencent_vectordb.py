from typing import Any, Dict

from pydantic import BaseModel, Field, model_validator


class TencentVectorDBConfig(BaseModel):
    url: str = Field(None, description="腾讯云向量数据库连接地址 (URL)")
    username: str = Field(None, description="用户名 (Username)")
    key: str = Field(None, description="API密钥 (API Key)")
    database_name: str = Field("mem0", description="数据库名称 (Database name)")
    collection_name: str = Field("mem0", description="集合名称 (Collection name)")
    embedding_model_dims: int = Field(1536, description="向量维度 (Embedding dimensions)")
    metric_type: str = Field("cosine", description="距离度量类型: cosine, l2, ip (Metric type: cosine, l2, ip)")
    timeout: int = Field(30, description="连接超时时间（秒）(Connection timeout in seconds)")

    @model_validator(mode="before")
    @classmethod
    def validate_extra_fields(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        allowed_fields = set(cls.model_fields.keys())
        input_fields = set(values.keys())
        extra_fields = input_fields - allowed_fields
        if extra_fields:
            raise ValueError(
                f"Extra fields not allowed: {', '.join(extra_fields)}. Please input only the following fields: {', '.join(allowed_fields)}"
            )
        return values

    model_config = {
        "arbitrary_types_allowed": True,
    }

