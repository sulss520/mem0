import datetime
from uuid import uuid4

# 导入日志配置（必须在其他导入之前）
from app.logging_config import logger, LOG_LEVEL
import logging

from app.config import DEFAULT_APP_ID, USER_ID
from app.database import Base, SessionLocal, engine
from app.mcp_server import setup_mcp_server
from app.models import App, User
from app.routers import apps_router, backup_router, config_router, memories_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_pagination import add_pagination

app = FastAPI(title="OpenMemory API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create all tables
Base.metadata.create_all(bind=engine)

# Check for USER_ID and create default user if needed
def create_default_user():
    db = SessionLocal()
    try:
        # Check if user exists
        user = db.query(User).filter(User.user_id == USER_ID).first()
        if not user:
            # Create default user
            user = User(
                id=uuid4(),
                user_id=USER_ID,
                name="Default User",
                created_at=datetime.datetime.now(datetime.UTC)
            )
            db.add(user)
            db.commit()
    finally:
        db.close()


def create_default_app():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.user_id == USER_ID).first()
        if not user:
            return

        # Check if app already exists
        existing_app = db.query(App).filter(
            App.name == DEFAULT_APP_ID,
            App.owner_id == user.id
        ).first()

        if existing_app:
            return

        app = App(
            id=uuid4(),
            name=DEFAULT_APP_ID,
            owner_id=user.id,
            created_at=datetime.datetime.now(datetime.UTC),
            updated_at=datetime.datetime.now(datetime.UTC),
        )
        db.add(app)
        db.commit()
    finally:
        db.close()

# Create default user on startup
create_default_user()
create_default_app()

# Setup MCP server
setup_mcp_server(app)

# Include routers
app.include_router(memories_router)
app.include_router(apps_router)
app.include_router(config_router)
app.include_router(backup_router)

# Add pagination support
add_pagination(app)

# 启动方法：可以直接运行 python main.py 启动服务
if __name__ == "__main__":
    import uvicorn
    import os
    
    # 从环境变量读取配置，默认值
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8765))
    reload = os.getenv("RELOAD", "true").lower() == "true"
    
    logger.info("🚀 Starting OpenMemory API server...")
    logger.info(f"📍 API URL: http://{host}:{port}")
    logger.info(f"📚 API Docs: http://{host}:{port}/docs")
    logger.info(f"🔄 Reload: {reload}")
    
    # uvicorn 的日志级别（使用小写）
    uvicorn_log_level = LOG_LEVEL.lower()
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=uvicorn_log_level,
        log_config=None  # 使用我们自己的日志配置
    )
