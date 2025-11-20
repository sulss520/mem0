import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# load .env file (make sure you have DATABASE_URL set)
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./openmemory.db")

# 严格验证 DATABASE_URL
if not DATABASE_URL or not DATABASE_URL.strip():
    raise RuntimeError("DATABASE_URL 环境变量未设置或为空")

# 验证 DATABASE_URL 格式（基本格式检查）
DATABASE_URL = DATABASE_URL.strip()
valid_schemes = ['sqlite', 'mysql', 'postgresql', 'postgres', 'mariadb']
url_scheme = DATABASE_URL.split('://')[0] if '://' in DATABASE_URL else None

if url_scheme and not any(DATABASE_URL.startswith(f"{scheme}://") for scheme in valid_schemes):
    raise RuntimeError(
        f"DATABASE_URL 格式无效。支持的数据库类型: {', '.join(valid_schemes)}。"
        f"当前值: {DATABASE_URL[:50]}..."
    )

# to handle SQLite specifically, we need to set connect_args
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# 连接池配置（仅对MySQL/PostgreSQL等数据库有效）
# 从环境变量读取，提供合理的默认值
pool_size = int(os.getenv("DB_POOL_SIZE", "5"))  # 连接池大小
max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))  # 最大溢出连接数
pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))  # 获取连接超时时间（秒）
pool_pre_ping = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"  # 连接健康检查

# 构建engine参数
engine_kwargs = {
    "connect_args": connect_args,
    "pool_pre_ping": pool_pre_ping,  # 自动检测并重新连接断开的连接
}

# 仅对非SQLite数据库配置连接池
if not DATABASE_URL.startswith("sqlite"):
    engine_kwargs.update({
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_timeout": pool_timeout,
        "pool_recycle": 3600,  # 1小时后回收连接，避免长时间连接问题
    })

# SQLAlchemy engine & session
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

# Dependency for FastAPI
def get_db():
    """
    获取数据库session的依赖函数。
    使用上下文管理器确保资源总是被正确释放。
    
    Yields:
        Session: SQLAlchemy数据库session
        
    Note:
        这个函数是FastAPI的依赖注入，会自动处理session的生命周期。
        即使发生异常，finally块也会确保session被关闭。
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # 发生异常时回滚事务
        db.rollback()
        raise
    finally:
        db.close()
