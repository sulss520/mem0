import logging
import os
from datetime import UTC, datetime
from typing import List, Optional, Set
from uuid import UUID

from app.database import get_db
from app.models import (
    AccessControl,
    App,
    Category,
    Memory,
    MemoryAccessLog,
    MemoryState,
    MemoryStatusHistory,
    User,
)
from app.schemas import MemoryResponse
from app.utils.memory import get_memory_client
from app.utils.permissions import check_memory_access_permissions
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination import Page, Params
from fastapi_pagination.ext.sqlalchemy import paginate as sqlalchemy_paginate
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


def get_memory_or_404(db: Session, memory_id: UUID) -> Memory:
    """Get memory by ID, converting UUID to string for String(36) column."""
    # Convert UUID to string for String(36) column query
    memory_id_str = str(memory_id)
    memory = db.query(Memory).filter(Memory.id == memory_id_str).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


def update_memory_state(db: Session, memory_id: UUID, new_state: MemoryState, user_id: UUID):
    """Update memory state and record history with proper error handling."""
    try:
        memory = get_memory_or_404(db, memory_id)
        old_state = memory.state

        # Update memory state
        memory.state = new_state
        if new_state == MemoryState.archived:
            memory.archived_at = datetime.now(UTC)
        elif new_state == MemoryState.deleted:
            memory.deleted_at = datetime.now(UTC)

        # Record state change - convert UUID to string for String(36) column
        history = MemoryStatusHistory(
            memory_id=str(memory_id),  # Convert UUID to string for String(36) column
            changed_by=str(user_id),    # Convert UUID to string for String(36) column
            old_state=old_state,
            new_state=new_state
        )
        db.add(history)
        db.commit()
        return memory
    except Exception as e:
        db.rollback()
        logging.error(f"Failed to update memory state: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update memory state: {str(e)}")


def get_accessible_memory_ids(db: Session, app_id: UUID) -> Set[UUID]:
    """
    Get the set of memory IDs that the app has access to based on app-level ACL rules.
    Returns all memory IDs if no specific restrictions are found.
    """
    # Get app-level access controls
    app_access = db.query(AccessControl).filter(
        AccessControl.subject_type == "app",
        AccessControl.subject_id == app_id,
        AccessControl.object_type == "memory"
    ).all()

    # If no app-level rules exist, return None to indicate all memories are accessible
    if not app_access:
        return None

    # Initialize sets for allowed and denied memory IDs
    allowed_memory_ids = set()
    denied_memory_ids = set()

    # Process app-level rules
    for rule in app_access:
        if rule.effect == "allow":
            if rule.object_id:  # Specific memory access
                allowed_memory_ids.add(rule.object_id)
            else:  # All memories access
                return None  # All memories allowed
        elif rule.effect == "deny":
            if rule.object_id:  # Specific memory denied
                denied_memory_ids.add(rule.object_id)
            else:  # All memories denied
                return set()  # No memories accessible

    # Remove denied memories from allowed set
    if allowed_memory_ids:
        allowed_memory_ids -= denied_memory_ids

    return allowed_memory_ids


# List all memories with filtering
@router.get("/", response_model=Page[MemoryResponse])
async def list_memories(
    user_id: str,
    app_id: Optional[UUID] = None,
    from_date: Optional[int] = Query(
        None,
        description="Filter memories created after this date (timestamp)",
        examples=[1718505600]
    ),
    to_date: Optional[int] = Query(
        None,
        description="Filter memories created before this date (timestamp)",
        examples=[1718505600]
    ),
    categories: Optional[str] = None,
    params: Params = Depends(),
    search_query: Optional[str] = None,
    sort_column: Optional[str] = Query(None, description="Column to sort by (memory, categories, app_name, created_at)"),
    sort_direction: Optional[str] = Query(None, description="Sort direction (asc or desc)"),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Build base query
    query = db.query(Memory).filter(
        Memory.user_id == user.id,
        Memory.state != MemoryState.deleted,
        Memory.state != MemoryState.archived,
        Memory.content.ilike(f"%{search_query}%") if search_query else True
    )

    # Apply filters
    if app_id:
        query = query.filter(Memory.app_id == app_id)

    if from_date:
        from_datetime = datetime.fromtimestamp(from_date, tz=UTC)
        query = query.filter(Memory.created_at >= from_datetime)

    if to_date:
        to_datetime = datetime.fromtimestamp(to_date, tz=UTC)
        query = query.filter(Memory.created_at <= to_datetime)

    # Add joins for app and categories after filtering
    query = query.outerjoin(App, Memory.app_id == App.id)
    query = query.outerjoin(Memory.categories)

    # Apply category filter if provided
    if categories:
        category_list = [c.strip() for c in categories.split(",")]
        query = query.filter(Category.name.in_(category_list))

    # Apply sorting if specified
    if sort_column:
        sort_field = getattr(Memory, sort_column, None)
        if sort_field:
            query = query.order_by(sort_field.desc()) if sort_direction == "desc" else query.order_by(sort_field.asc())


    # Get paginated results
    paginated_results = sqlalchemy_paginate(query, params)

    # Filter results based on permissions
    filtered_items = []
    for item in paginated_results.items:
        if check_memory_access_permissions(db, item, app_id):
            filtered_items.append(item)

    # Update paginated results with filtered items
    paginated_results.items = filtered_items
    paginated_results.total = len(filtered_items)

    return paginated_results


# Get all categories
@router.get("/categories")
async def get_categories(
    user_id: str,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get unique categories associated with the user's memories
    # Get all memories
    memories = db.query(Memory).filter(Memory.user_id == user.id, Memory.state != MemoryState.deleted, Memory.state != MemoryState.archived).all()
    # Get all categories from memories
    categories = [category for memory in memories for category in memory.categories]
    # Get unique categories
    unique_categories = list(set(categories))

    return {
        "categories": unique_categories,
        "total": len(unique_categories)
    }


class CreateMemoryRequest(BaseModel):
    user_id: str
    text: str
    metadata: dict = {}
    infer: bool = True
    app: str = "openmemory"


# Create new memory
@router.post("/")
async def create_memory(
    request: CreateMemoryRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Get or create app
    app_obj = db.query(App).filter(App.name == request.app,
                                   App.owner_id == user.id).first()
    if not app_obj:
        app_obj = App(name=request.app, owner_id=user.id)
        db.add(app_obj)
        db.commit()
        db.refresh(app_obj)

    # Check if app is active
    if not app_obj.is_active:
        raise HTTPException(status_code=403, detail=f"App {request.app} is currently paused on OpenMemory. Cannot create new memories.")

    # ============================================================================
    # 详细日志记录：Memory 创建流程
    # ============================================================================
    logging.info("=" * 60)
    logging.info(f"📝 [Memory Creation] 开始创建 Memory")
    logging.info(f"   User ID: {request.user_id}")
    logging.info(f"   App: {request.app}")
    logging.info(f"   Content: {request.text[:100]}..." if len(request.text) > 100 else f"   Content: {request.text}")
    logging.info(f"   User DB ID: {user.id}")
    logging.info(f"   App DB ID: {app_obj.id}")
    
    # Try to get memory client safely
    try:
        logging.info("🔧 [Memory Client] 初始化 Memory Client...")
        memory_client = get_memory_client()
        if not memory_client:
            raise Exception("Memory client is not available")
        logging.info("✅ [Memory Client] Memory Client 初始化成功")
    except Exception as client_error:
        logging.error(f"❌ [Memory Client] Memory Client 初始化失败: {client_error}")
        logging.warning("⚠️  [Memory Client] 无法创建 Memory，仅保存到数据库")
        return {
            "error": str(client_error)
        }

    # Try to save to vector store via memory_client
    try:
        logging.info("📦 [Vector Store] 开始保存到向量数据库...")
        logging.info(f"   Provider: {os.getenv('VECTOR_STORE_PROVIDER', 'unknown')}")
        
        vector_store_response = memory_client.add(
            request.text,
            user_id=request.user_id,  # Use string user_id to match search
            metadata={
                "source_app": "openmemory",
                "mcp_client": request.app,
            }
        )
        
        logging.info("✅ [Vector Store] 向量数据库保存成功")
        logging.info(f"   Response: {vector_store_response}")
        
        # Process vector store response
        if isinstance(vector_store_response, dict) and 'results' in vector_store_response:
            created_memories = []
            
            logging.info(f"📊 [Vector Store] 处理 {len(vector_store_response['results'])} 个结果")
            
            for idx, result in enumerate(vector_store_response['results']):
                if result['event'] == 'ADD':
                    # Get the vector store-generated ID and convert to string
                    memory_id_str = str(result['id'])  # Convert UUID to string for String(36) column
                    
                    logging.info(f"🆔 [Memory ID] 向量数据库生成的 ID: {memory_id_str}")
                    
                    # Check if memory already exists
                    existing_memory = db.query(Memory).filter(Memory.id == memory_id_str).first()
                    
                    if existing_memory:
                        logging.info(f"🔄 [MySQL] Memory 已存在，更新现有记录 (ID: {memory_id_str})")
                        # Update existing memory
                        existing_memory.state = MemoryState.active
                        existing_memory.content = result['memory']
                        memory = existing_memory
                        logging.info(f"   ✅ Memory 更新已添加到 Session")
                    else:
                        logging.info(f"➕ [MySQL] 创建新的 Memory 记录 (ID: {memory_id_str})")
                        # Create memory with the EXACT SAME ID from vector store
                        memory = Memory(
                            id=memory_id_str,  # Use string ID for String(36) column
                            user_id=user.id,
                            app_id=app_obj.id,
                            content=result['memory'],
                            metadata_=request.metadata,
                            state=MemoryState.active
                        )
                        db.add(memory)
                        logging.info(f"   ✅ Memory 对象已添加到 Session")
                    
                    # Create history entry (use string for String(36) column)
                    # 注意：已移除外键约束，不再需要 flush 来保证顺序
                    logging.info(f"📜 [MySQL] 创建状态历史记录 (Memory ID: {memory_id_str})")
                    # 修复：根据实际情况设置old_state
                    old_state = existing_memory.state if existing_memory else MemoryState.deleted
                    history = MemoryStatusHistory(
                        memory_id=memory_id_str,  # History table uses String(36) type
                        changed_by=user.id,
                        old_state=old_state,
                        new_state=MemoryState.active
                    )
                    db.add(history)
                    logging.info(f"   ✅ History 对象已添加到 Session")
                    
                    created_memories.append(memory)
            
            # Commit all changes at once
            if created_memories:
                try:
                    logging.info(f"💾 [MySQL] 提交事务，保存 {len(created_memories)} 个 Memory 到数据库...")
                    db.commit()
                    logging.info("✅ [MySQL] 事务提交成功")
                    
                    for memory in created_memories:
                        db.refresh(memory)
                        logging.info(f"   ✅ Memory (ID: {memory.id}) 已刷新")
                    
                    logging.info("=" * 60)
                    logging.info(f"🎉 [Memory Creation] Memory 创建完成 (ID: {created_memories[0].id})")
                    logging.info("=" * 60)
                    
                    # Return the first memory (for API compatibility)
                    # but all memories are now saved to the database
                    return created_memories[0]
                except Exception as commit_error:
                    logging.error("=" * 60)
                    logging.error(f"❌ [MySQL] 事务提交失败")
                    logging.error(f"   Error: {commit_error}")
                    logging.error("=" * 60)
                    db.rollback()
                    logging.info("🔄 [MySQL] 事务已回滚")
                    import traceback
                    logging.error(traceback.format_exc())
                    raise HTTPException(status_code=500, detail=f"Database commit failed: {commit_error}")
            else:
                logging.warning("⚠️  [Memory Creation] 没有需要保存的 Memory")
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as vector_store_error:
        logging.error("=" * 60)
        logging.error(f"❌ [Vector Store] 向量数据库操作失败")
        logging.error(f"   Error: {vector_store_error}")
        logging.error("=" * 60)
        db.rollback()
        logging.info("🔄 [MySQL] 事务已回滚")
        import traceback
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Vector store operation failed: {vector_store_error}")




# Get memory by ID
@router.get("/{memory_id}")
async def get_memory(
    memory_id: UUID,
    db: Session = Depends(get_db)
):
    memory = get_memory_or_404(db, memory_id)
    return {
        "id": memory.id,
        "text": memory.content,
        "created_at": int(memory.created_at.timestamp()),
        "state": memory.state.value,
        "app_id": memory.app_id,
        "app_name": memory.app.name if memory.app else None,
        "categories": [category.name for category in memory.categories],
        "metadata_": memory.metadata_
    }


class DeleteMemoriesRequest(BaseModel):
    memory_ids: List[UUID]
    user_id: str

# Delete multiple memories
@router.delete("/")
async def delete_memories(
    request: DeleteMemoriesRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for memory_id in request.memory_ids:
        update_memory_state(db, memory_id, MemoryState.deleted, user.id)
    return {"message": f"Successfully deleted {len(request.memory_ids)} memories"}


# Archive memories
@router.post("/actions/archive")
async def archive_memories(
    memory_ids: List[UUID],
    user_id: UUID,
    db: Session = Depends(get_db)
):
    for memory_id in memory_ids:
        update_memory_state(db, memory_id, MemoryState.archived, user_id)
    return {"message": f"Successfully archived {len(memory_ids)} memories"}


class PauseMemoriesRequest(BaseModel):
    memory_ids: Optional[List[UUID]] = None
    category_ids: Optional[List[UUID]] = None
    app_id: Optional[UUID] = None
    all_for_app: bool = False
    global_pause: bool = False
    state: Optional[MemoryState] = None
    user_id: str

# Pause access to memories
@router.post("/actions/pause")
async def pause_memories(
    request: PauseMemoriesRequest,
    db: Session = Depends(get_db)
):
    
    global_pause = request.global_pause
    all_for_app = request.all_for_app
    app_id = request.app_id
    memory_ids = request.memory_ids
    category_ids = request.category_ids
    state = request.state or MemoryState.paused

    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user_id = user.id
    
    if global_pause:
        # Pause all memories
        memories = db.query(Memory).filter(
            Memory.state != MemoryState.deleted,
            Memory.state != MemoryState.archived
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": "Successfully paused all memories"}

    if app_id:
        # Pause all memories for an app
        memories = db.query(Memory).filter(
            Memory.app_id == app_id,
            Memory.user_id == user.id,
            Memory.state != MemoryState.deleted,
            Memory.state != MemoryState.archived
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": f"Successfully paused all memories for app {app_id}"}
    
    if all_for_app and memory_ids:
        # Pause all memories for an app
        memories = db.query(Memory).filter(
            Memory.user_id == user.id,
            Memory.state != MemoryState.deleted,
            Memory.id.in_(memory_ids)
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": "Successfully paused all memories"}

    if memory_ids:
        # Pause specific memories
        for memory_id in memory_ids:
            update_memory_state(db, memory_id, state, user_id)
        return {"message": f"Successfully paused {len(memory_ids)} memories"}

    if category_ids:
        # Pause memories by category
        memories = db.query(Memory).join(Memory.categories).filter(
            Category.id.in_(category_ids),
            Memory.state != MemoryState.deleted,
            Memory.state != MemoryState.archived
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": f"Successfully paused memories in {len(category_ids)} categories"}

    raise HTTPException(status_code=400, detail="Invalid pause request parameters")


# Get memory access logs
@router.get("/{memory_id}/access-log")
async def get_memory_access_log(
    memory_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    query = db.query(MemoryAccessLog).filter(MemoryAccessLog.memory_id == memory_id)
    total = query.count()
    logs = query.order_by(MemoryAccessLog.accessed_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # Get app name
    for log in logs:
        app = db.query(App).filter(App.id == log.app_id).first()
        log.app_name = app.name if app else None

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "logs": logs
    }


class UpdateMemoryRequest(BaseModel):
    memory_content: str
    user_id: str

# Update a memory
@router.put("/{memory_id}")
async def update_memory(
    memory_id: UUID,
    request: UpdateMemoryRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    memory = get_memory_or_404(db, memory_id)
    memory.content = request.memory_content
    db.commit()
    db.refresh(memory)
    return memory

class FilterMemoriesRequest(BaseModel):
    user_id: str
    page: int = 1
    size: int = 10
    search_query: Optional[str] = None
    app_ids: Optional[List[UUID]] = None
    category_ids: Optional[List[UUID]] = None
    sort_column: Optional[str] = None
    sort_direction: Optional[str] = None
    from_date: Optional[int] = None
    to_date: Optional[int] = None
    show_archived: Optional[bool] = False

@router.post("/filter", response_model=Page[MemoryResponse])
async def filter_memories(
    request: FilterMemoriesRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Build base query
    query = db.query(Memory).filter(
        Memory.user_id == user.id,
        Memory.state != MemoryState.deleted,
    )

    # Filter archived memories based on show_archived parameter
    if not request.show_archived:
        query = query.filter(Memory.state != MemoryState.archived)

    # Apply search filter
    if request.search_query:
        query = query.filter(Memory.content.ilike(f"%{request.search_query}%"))

    # Apply app filter
    if request.app_ids:
        query = query.filter(Memory.app_id.in_(request.app_ids))

    # Add joins for app and categories
    query = query.outerjoin(App, Memory.app_id == App.id)

    # Apply category filter
    if request.category_ids:
        query = query.join(Memory.categories).filter(Category.id.in_(request.category_ids))
    else:
        query = query.outerjoin(Memory.categories)

    # Apply date filters
    if request.from_date:
        from_datetime = datetime.fromtimestamp(request.from_date, tz=UTC)
        query = query.filter(Memory.created_at >= from_datetime)

    if request.to_date:
        to_datetime = datetime.fromtimestamp(request.to_date, tz=UTC)
        query = query.filter(Memory.created_at <= to_datetime)

    # Apply sorting
    if request.sort_column and request.sort_direction:
        sort_direction = request.sort_direction.lower()
        if sort_direction not in ['asc', 'desc']:
            raise HTTPException(status_code=400, detail="Invalid sort direction")

        sort_mapping = {
            'memory': Memory.content,
            'app_name': App.name,
            'created_at': Memory.created_at
        }

        if request.sort_column not in sort_mapping:
            raise HTTPException(status_code=400, detail="Invalid sort column")

        sort_field = sort_mapping[request.sort_column]
        if sort_direction == 'desc':
            query = query.order_by(sort_field.desc())
        else:
            query = query.order_by(sort_field.asc())
    else:
        # Default sorting
        query = query.order_by(Memory.created_at.desc())

    # Add eager loading for categories and make the query distinct
    query = query.options(
        joinedload(Memory.categories)
    ).distinct(Memory.id)

    # Use fastapi-pagination's paginate function
    return sqlalchemy_paginate(
        query,
        Params(page=request.page, size=request.size),
        transformer=lambda items: [
            MemoryResponse(
                id=memory.id,
                content=memory.content,
                created_at=memory.created_at,
                state=memory.state.value,
                app_id=memory.app_id,
                app_name=memory.app.name if memory.app else None,
                categories=[category.name for category in memory.categories],
                metadata_=memory.metadata_
            )
            for memory in items
        ]
    )


@router.get("/{memory_id}/related", response_model=Page[MemoryResponse])
async def get_related_memories(
    memory_id: UUID,
    user_id: str,
    params: Params = Depends(),
    db: Session = Depends(get_db)
):
    # Validate user
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get the source memory
    memory = get_memory_or_404(db, memory_id)
    
    # Extract category IDs from the source memory
    category_ids = [category.id for category in memory.categories]
    
    if not category_ids:
        return Page.create([], total=0, params=params)
    
    # Build query for related memories
    query = db.query(Memory).distinct(Memory.id).filter(
        Memory.user_id == user.id,
        Memory.id != memory_id,
        Memory.state != MemoryState.deleted
    ).join(Memory.categories).filter(
        Category.id.in_(category_ids)
    ).options(
        joinedload(Memory.categories),
        joinedload(Memory.app)
    ).order_by(
        func.count(Category.id).desc(),
        Memory.created_at.desc()
    ).group_by(Memory.id)
    
    # ⚡ Force page size to be 5
    params = Params(page=params.page, size=5)
    
    return sqlalchemy_paginate(
        query,
        params,
        transformer=lambda items: [
            MemoryResponse(
                id=memory.id,
                content=memory.content,
                created_at=memory.created_at,
                state=memory.state.value,
                app_id=memory.app_id,
                app_name=memory.app.name if memory.app else None,
                categories=[category.name for category in memory.categories],
                metadata_=memory.metadata_
            )
            for memory in items
        ]
    )