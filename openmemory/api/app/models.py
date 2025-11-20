import datetime
import enum
import uuid

import sqlalchemy as sa
from sqlalchemy import (
    Column,
    String,
    Boolean,
    ForeignKey,
    Enum,
    Table,
    DateTime,
    JSON,
    Integer,
    Index,
    Text,
    event,
)
from sqlalchemy.orm import Session, relationship
from app.database import Base
from app.utils.categorization import get_categories_for_memory


def get_current_utc_time():
    """Get current UTC time"""
    return datetime.datetime.now(datetime.UTC)


class MemoryState(enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"
    deleted = "deleted"


class User(Base):
    __tablename__ = "users"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format (36 chars with hyphens)
    user_id = Column(
        String(255), nullable=False, unique=True, index=True
    )  # Modified: specify length
    name = Column(String(255), nullable=True, index=True)  # Modified: specify length
    email = Column(
        String(255), unique=True, nullable=True, index=True
    )  # Modified: specify length
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )

    apps = relationship("App", back_populates="owner")
    memories = relationship("Memory", back_populates="user")


class App(Base):
    __tablename__ = "apps"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format
    owner_id = Column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )  # Standard UUID format
    name = Column(String(255), nullable=False, index=True)  # Modified: specify length
    description = Column(Text)  # Changed to Text instead of String(1000)
    metadata_ = Column("metadata", JSON, default=dict)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )

    owner = relationship("User", back_populates="apps")
    memories = relationship("Memory", back_populates="app")

    __table_args__ = (
        sa.UniqueConstraint("owner_id", "name", name="idx_app_owner_name"),
    )


class Config(Base):
    __tablename__ = "configs"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format
    key = Column(
        String(255), unique=True, nullable=False, index=True
    )  # Modified: specify length
    value = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=get_current_utc_time)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )


class Memory(Base):
    __tablename__ = "memories"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    app_id = Column(String(36), ForeignKey("apps.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)  # Modified: use Text type
    vector = Column(Text)  # Modified: use Text type
    metadata_ = Column("metadata", JSON, default=dict)
    state = Column(Enum(MemoryState), default=MemoryState.active, index=True)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )
    archived_at = Column(DateTime, nullable=True, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)

    user = relationship("User", back_populates="memories")
    app = relationship("App", back_populates="memories")
    categories = relationship(
        "Category", secondary="memory_categories", back_populates="memories"
    )

    __table_args__ = (
        Index("idx_memory_user_state", "user_id", "state"),
        Index("idx_memory_app_state", "app_id", "state"),
        Index("idx_memory_user_app", "user_id", "app_id"),
    )


class Category(Base):
    __tablename__ = "categories"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format
    name = Column(
        String(255), unique=True, nullable=False, index=True
    )  # Modified: specify length
    description = Column(Text)  # Changed to Text instead of String(1000)
    created_at = Column(
        DateTime, default=datetime.datetime.now(datetime.UTC), index=True
    )
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )

    memories = relationship(
        "Memory", secondary="memory_categories", back_populates="categories"
    )


memory_categories = Table(
    "memory_categories",
    Base.metadata,
    Column(
        "memory_id", String(36), ForeignKey("memories.id"), primary_key=True, index=True
    ),  # Standard UUID format
    Column(
        "category_id",
        String(36),
        ForeignKey("categories.id"),
        primary_key=True,
        index=True,
    ),  # Standard UUID format
    Index("idx_memory_category", "memory_id", "category_id"),
)


class AccessControl(Base):
    __tablename__ = "access_controls"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format
    subject_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    subject_id = Column(String(36), nullable=True, index=True)  # Standard UUID format
    object_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    object_id = Column(String(36), nullable=True, index=True)  # Standard UUID format
    effect = Column(String(50), nullable=False, index=True)  # Modified: specify length
    created_at = Column(DateTime, default=get_current_utc_time, index=True)

    __table_args__ = (
        Index("idx_access_subject", "subject_type", "subject_id"),
        Index("idx_access_object", "object_type", "object_id"),
    )


class ArchivePolicy(Base):
    __tablename__ = "archive_policies"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format
    criteria_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    criteria_id = Column(String(36), nullable=True, index=True)  # Standard UUID format
    days_to_archive = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)

    __table_args__ = (Index("idx_policy_criteria", "criteria_type", "criteria_id"),)


class MemoryStatusHistory(Base):
    __tablename__ = "memory_status_history"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format
    memory_id = Column(
        String(36), nullable=False, index=True
    )  # Standard UUID format - 移除外键约束，保留索引（历史记录应保留，即使 Memory 被删除）
    changed_by = Column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )  # Standard UUID format
    old_state = Column(Enum(MemoryState), nullable=False, index=True)
    new_state = Column(Enum(MemoryState), nullable=False, index=True)
    changed_at = Column(DateTime, default=get_current_utc_time, index=True)

    __table_args__ = (
        Index("idx_history_memory_state", "memory_id", "new_state"),
        Index("idx_history_user_time", "changed_by", "changed_at"),
    )


class MemoryAccessLog(Base):
    __tablename__ = "memory_access_logs"
    id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )  # Standard UUID format
    memory_id = Column(
        String(36), ForeignKey("memories.id"), nullable=False, index=True
    )  # Standard UUID format
    app_id = Column(
        String(36), ForeignKey("apps.id"), nullable=False, index=True
    )  # Standard UUID format
    accessed_at = Column(DateTime, default=get_current_utc_time, index=True)
    access_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    metadata_ = Column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("idx_access_memory_time", "memory_id", "accessed_at"),
        Index("idx_access_app_time", "app_id", "accessed_at"),
    )


def categorize_memory(memory: Memory, db: Session) -> None:
    """Categorize a memory using OpenAI and store the categories in the database."""
    try:
        # Get categories from OpenAI
        categories = get_categories_for_memory(memory.content)

        # Get or create categories in the database
        for category_name in categories:
            category = db.query(Category).filter(Category.name == category_name).first()
            if not category:
                category = Category(
                    name=category_name,
                    description=f"Automatically created category for {category_name}",
                )
                db.add(category)
                # Flush to get the category ID (needed for foreign key in association table)
                # This is safe because we're in a separate transaction
                db.flush()

            # Check if the memory-category association already exists
            existing = db.execute(
                memory_categories.select().where(
                    (memory_categories.c.memory_id == memory.id)
                    & (memory_categories.c.category_id == category.id)
                )
            ).first()

            if not existing:
                # Create the association
                db.execute(
                    memory_categories.insert().values(
                        memory_id=memory.id, category_id=category.id
                    )
                )

        db.commit()
    except Exception as e:
        db.rollback()
        # Use logging instead of print for better error tracking
        import logging
        logging.error(f"Error categorizing memory: {e}", exc_info=True)


@event.listens_for(Memory, "after_insert")
def after_memory_insert(mapper, connection, target):
    """Trigger categorization after a memory is inserted.
    
    注意：使用独立的session和事务，避免与主事务冲突。
    如果分类失败，不会影响主事务的提交。
    """
    from app.database import SessionLocal
    import logging
    logger = logging.getLogger(__name__)
    
    # 创建独立的session，不绑定到当前connection，避免事务冲突
    db = SessionLocal()
    try:
        # 重新查询memory对象，确保使用新的session
        memory = db.query(Memory).filter(Memory.id == target.id).first()
        if memory:
            categorize_memory(memory, db)
        else:
            logger.warning(f"Memory {target.id} not found in new session, skipping categorization")
    except Exception as e:
        # 错误已在 categorize_memory 中记录，这里只确保 session 关闭
        logger.error(f"Error in after_memory_insert event handler: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass  # 忽略rollback错误
    finally:
        # 确保session总是被关闭，避免资源泄漏
        try:
            db.close()
        except Exception:
            pass  # 忽略close错误


@event.listens_for(Memory, "after_update")
def after_memory_update(mapper, connection, target):
    """Trigger categorization after a memory is updated.
    
    注意：使用独立的session和事务，避免与主事务冲突。
    如果分类失败，不会影响主事务的提交。
    """
    from app.database import SessionLocal
    import logging
    logger = logging.getLogger(__name__)
    
    # 创建独立的session，不绑定到当前connection，避免事务冲突
    db = SessionLocal()
    try:
        # 重新查询memory对象，确保使用新的session
        memory = db.query(Memory).filter(Memory.id == target.id).first()
        if memory:
            categorize_memory(memory, db)
        else:
            logger.warning(f"Memory {target.id} not found in new session, skipping categorization")
    except Exception as e:
        # 错误已在 categorize_memory 中记录，这里只确保 session 关闭
        logger.error(f"Error in after_memory_update event handler: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass  # 忽略rollback错误
    finally:
        # 确保session总是被关闭，避免资源泄漏
        try:
            db.close()
        except Exception:
            pass  # 忽略close错误
