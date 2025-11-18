import os
from typing import Any, Dict, Optional

from app.database import get_db
from app.models import Config as ConfigModel
from app.utils.memory import reset_memory_client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/config", tags=["config"])

class LLMConfig(BaseModel):
    model: str = Field(..., description="LLM model name")
    temperature: float = Field(..., description="Temperature setting for the model")
    max_tokens: int = Field(..., description="Maximum tokens to generate")
    api_key: Optional[str] = Field(None, description="API key or 'env:API_KEY' to use environment variable")
    ollama_base_url: Optional[str] = Field(None, description="Base URL for Ollama server (e.g., http://host.docker.internal:11434)")

class LLMProvider(BaseModel):
    provider: str = Field(..., description="LLM provider name")
    config: LLMConfig

class EmbedderConfig(BaseModel):
    model: str = Field(..., description="Embedder model name")
    api_key: Optional[str] = Field(None, description="API key or 'env:API_KEY' to use environment variable")
    ollama_base_url: Optional[str] = Field(None, description="Base URL for Ollama server (e.g., http://host.docker.internal:11434)")

class EmbedderProvider(BaseModel):
    provider: str = Field(..., description="Embedder provider name")
    config: EmbedderConfig

class GraphStoreConfig(BaseModel):
    # 代理模式配置（推荐，使用 graphs_proxy）
    use_proxy: Optional[bool] = Field(False, description="Whether to use proxy mode (graphs_proxy)")
    proxy_url: Optional[str] = Field(None, description="Proxy service URL (e.g., http://localhost:8090)")
    # 直连模式配置（备选）
    url: Optional[str] = Field(None, description="Neo4j connection URL (e.g., bolt://localhost:7687 or neo4j+s://...)")
    # 通用配置
    username: Optional[str] = Field("neo4j", description="Neo4j username")
    password: Optional[str] = Field(None, description="Neo4j password or 'env:NEO4J_PASSWORD' to use environment variable")
    database: Optional[str] = Field("neo4j", description="Neo4j database name")

class GraphStoreProvider(BaseModel):
    provider: str = Field("neo4j", description="Graph store provider name (currently only 'neo4j' is supported)")
    config: GraphStoreConfig

class OpenMemoryConfig(BaseModel):
    custom_instructions: Optional[str] = Field(None, description="Custom instructions for memory management and fact extraction")

class Mem0Config(BaseModel):
    llm: Optional[LLMProvider] = None
    embedder: Optional[EmbedderProvider] = None
    graph_store: Optional[GraphStoreProvider] = None

class ConfigSchema(BaseModel):
    openmemory: Optional[OpenMemoryConfig] = None
    mem0: Mem0Config

def get_default_configuration():
    """Get the default configuration with sensible defaults for LLM and embedder."""
    config = {
        "openmemory": {
            "custom_instructions": None
        },
        "mem0": {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "gpt-4o-mini",
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "api_key": "env:OPENAI_API_KEY"
                }
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "text-embedding-3-small",
                    "api_key": "env:OPENAI_API_KEY"
                }
            }
        }
    }
    
    # 如果环境变量中配置了 Neo4j，添加 graph_store 配置
    # 优先使用代理模式
    neo4j_use_proxy = os.environ.get("NEO4J_USE_PROXY", "false").lower() == "true"
    neo4j_proxy_url = os.environ.get("NEO4J_PROXY_URL")
    neo4j_url = os.environ.get("NEO4J_URL")
    neo4j_username = os.environ.get("NEO4J_USERNAME")
    neo4j_password = os.environ.get("NEO4J_PASSWORD")
    
    if neo4j_use_proxy and neo4j_proxy_url and neo4j_username and neo4j_password:
        # 代理模式
        config["mem0"]["graph_store"] = {
            "provider": "neo4j",
            "config": {
                "use_proxy": True,
                "proxy_url": neo4j_proxy_url,
                "username": neo4j_username,
                "password": "env:NEO4J_PASSWORD",
                "database": os.environ.get("NEO4J_DATABASE", "neo4j")
            }
        }
    elif neo4j_url and neo4j_username and neo4j_password:
        # 直连模式
        config["mem0"]["graph_store"] = {
            "provider": "neo4j",
            "config": {
                "use_proxy": False,
                "url": neo4j_url,
                "username": neo4j_username,
                "password": "env:NEO4J_PASSWORD",
                "database": os.environ.get("NEO4J_DATABASE", "neo4j")
            }
        }
    
    return config

def get_config_from_db(db: Session, key: str = "main"):
    """Get configuration from database."""
    config = db.query(ConfigModel).filter(ConfigModel.key == key).first()
    
    if not config:
        # Create default config with proper provider configurations
        default_config = get_default_configuration()
        db_config = ConfigModel(key=key, value=default_config)
        db.add(db_config)
        db.commit()
        db.refresh(db_config)
        return default_config
    
    # Ensure the config has all required sections with defaults
    config_value = config.value
    default_config = get_default_configuration()
    
    # Merge with defaults to ensure all required fields exist
    if "openmemory" not in config_value:
        config_value["openmemory"] = default_config["openmemory"]
    
    if "mem0" not in config_value:
        config_value["mem0"] = default_config["mem0"]
    else:
        # Ensure LLM config exists with defaults
        if "llm" not in config_value["mem0"] or config_value["mem0"]["llm"] is None:
            config_value["mem0"]["llm"] = default_config["mem0"]["llm"]
        
        # Ensure embedder config exists with defaults
        if "embedder" not in config_value["mem0"] or config_value["mem0"]["embedder"] is None:
            config_value["mem0"]["embedder"] = default_config["mem0"]["embedder"]
        
        # Graph store is optional, so we don't set a default if it's not present
        # But if it exists in default_config, we should preserve it
        if "graph_store" in default_config["mem0"] and "graph_store" not in config_value["mem0"]:
            config_value["mem0"]["graph_store"] = default_config["mem0"]["graph_store"]
    
    # Save the updated config back to database if it was modified
    if config_value != config.value:
        config.value = config_value
        db.commit()
        db.refresh(config)
    
    return config_value

def save_config_to_db(db: Session, config: Dict[str, Any], key: str = "main"):
    """Save configuration to database."""
    db_config = db.query(ConfigModel).filter(ConfigModel.key == key).first()
    
    if db_config:
        db_config.value = config
        db_config.updated_at = None  # Will trigger the onupdate to set current time
    else:
        db_config = ConfigModel(key=key, value=config)
        db.add(db_config)
        
    db.commit()
    db.refresh(db_config)
    return db_config.value

@router.get("/", response_model=ConfigSchema)
async def get_configuration(db: Session = Depends(get_db)):
    """Get the current configuration."""
    config = get_config_from_db(db)
    return config

@router.put("/", response_model=ConfigSchema)
async def update_configuration(config: ConfigSchema, db: Session = Depends(get_db)):
    """Update the configuration."""
    current_config = get_config_from_db(db)
    
    # Convert to dict for processing
    updated_config = current_config.copy()
    
    # Update openmemory settings if provided
    if config.openmemory is not None:
        if "openmemory" not in updated_config:
            updated_config["openmemory"] = {}
        updated_config["openmemory"].update(config.openmemory.dict(exclude_none=True))
    
    # Update mem0 settings
    updated_config["mem0"] = config.mem0.dict(exclude_none=True)
    
    # Save the configuration to database
    save_config_to_db(db, updated_config)
    reset_memory_client()
    return updated_config

@router.post("/reset", response_model=ConfigSchema)
async def reset_configuration(db: Session = Depends(get_db)):
    """Reset the configuration to default values."""
    try:
        # Get the default configuration with proper provider setups
        default_config = get_default_configuration()
        
        # Save it as the current configuration in the database
        save_config_to_db(db, default_config)
        reset_memory_client()
        return default_config
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to reset configuration: {str(e)}"
        )

@router.get("/mem0/llm", response_model=LLMProvider)
async def get_llm_configuration(db: Session = Depends(get_db)):
    """Get only the LLM configuration."""
    config = get_config_from_db(db)
    llm_config = config.get("mem0", {}).get("llm", {})
    return llm_config

@router.put("/mem0/llm", response_model=LLMProvider)
async def update_llm_configuration(llm_config: LLMProvider, db: Session = Depends(get_db)):
    """Update only the LLM configuration."""
    current_config = get_config_from_db(db)
    
    # Ensure mem0 key exists
    if "mem0" not in current_config:
        current_config["mem0"] = {}
    
    # Update the LLM configuration
    current_config["mem0"]["llm"] = llm_config.dict(exclude_none=True)
    
    # Save the configuration to database
    save_config_to_db(db, current_config)
    reset_memory_client()
    return current_config["mem0"]["llm"]

@router.get("/mem0/embedder", response_model=EmbedderProvider)
async def get_embedder_configuration(db: Session = Depends(get_db)):
    """Get only the Embedder configuration."""
    config = get_config_from_db(db)
    embedder_config = config.get("mem0", {}).get("embedder", {})
    return embedder_config

@router.put("/mem0/embedder", response_model=EmbedderProvider)
async def update_embedder_configuration(embedder_config: EmbedderProvider, db: Session = Depends(get_db)):
    """Update only the Embedder configuration."""
    current_config = get_config_from_db(db)
    
    # Ensure mem0 key exists
    if "mem0" not in current_config:
        current_config["mem0"] = {}
    
    # Update the Embedder configuration
    current_config["mem0"]["embedder"] = embedder_config.dict(exclude_none=True)
    
    # Save the configuration to database
    save_config_to_db(db, current_config)
    reset_memory_client()
    return current_config["mem0"]["embedder"]

@router.get("/openmemory", response_model=OpenMemoryConfig)
async def get_openmemory_configuration(db: Session = Depends(get_db)):
    """Get only the OpenMemory configuration."""
    config = get_config_from_db(db)
    openmemory_config = config.get("openmemory", {})
    return openmemory_config

@router.put("/openmemory", response_model=OpenMemoryConfig)
async def update_openmemory_configuration(openmemory_config: OpenMemoryConfig, db: Session = Depends(get_db)):
    """Update only the OpenMemory configuration."""
    current_config = get_config_from_db(db)
    
    # Ensure openmemory key exists
    if "openmemory" not in current_config:
        current_config["openmemory"] = {}
    
    # Update the OpenMemory configuration
    current_config["openmemory"].update(openmemory_config.dict(exclude_none=True))
    
    # Save the configuration to database
    save_config_to_db(db, current_config)
    reset_memory_client()
    return current_config["openmemory"]

@router.get("/mem0/graph-store", response_model=GraphStoreProvider)
async def get_graph_store_configuration(db: Session = Depends(get_db)):
    """Get only the Graph Store configuration."""
    config = get_config_from_db(db)
    graph_store_config = config.get("mem0", {}).get("graph_store")
    if graph_store_config is None:
        raise HTTPException(status_code=404, detail="Graph store configuration not found")
    return graph_store_config

@router.put("/mem0/graph-store", response_model=GraphStoreProvider)
async def update_graph_store_configuration(graph_store_config: GraphStoreProvider, db: Session = Depends(get_db)):
    """Update only the Graph Store configuration."""
    current_config = get_config_from_db(db)
    
    # Ensure mem0 key exists
    if "mem0" not in current_config:
        current_config["mem0"] = {}
    
    # Update the Graph Store configuration
    current_config["mem0"]["graph_store"] = graph_store_config.dict(exclude_none=True)
    
    # Save the configuration to database
    save_config_to_db(db, current_config)
    reset_memory_client()
    return current_config["mem0"]["graph_store"]

@router.delete("/mem0/graph-store")
async def delete_graph_store_configuration(db: Session = Depends(get_db)):
    """Delete the Graph Store configuration."""
    current_config = get_config_from_db(db)
    
    # Ensure mem0 key exists
    if "mem0" not in current_config:
        raise HTTPException(status_code=404, detail="Graph store configuration not found")
    
    # Remove the Graph Store configuration
    if "graph_store" in current_config["mem0"]:
        del current_config["mem0"]["graph_store"]
        save_config_to_db(db, current_config)
        reset_memory_client()
        return {"message": "Graph store configuration deleted successfully"}
    else:
        raise HTTPException(status_code=404, detail="Graph store configuration not found") 