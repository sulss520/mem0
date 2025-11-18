import logging
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

# 记忆事件类型常量
MEMORY_EVENT_ADD = "ADD"
MEMORY_EVENT_UPDATE = "UPDATE"
MEMORY_EVENT_DELETE = "DELETE"
MEMORY_EVENT_NONE = "NONE"


def get_memory_or_404(db: Session, memory_id: UUID) -> Memory:
    memory = db.query(Memory).filter(Memory.id == memory_id).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


def update_memory_state(db: Session, memory_id: UUID, new_state: MemoryState, user_id: UUID):
    memory = get_memory_or_404(db, memory_id)
    old_state = memory.state

    # Update memory state
    memory.state = new_state
    if new_state == MemoryState.archived:
        memory.archived_at = datetime.now(UTC)
    elif new_state == MemoryState.deleted:
        memory.deleted_at = datetime.now(UTC)

    # Record state change
    history = MemoryStatusHistory(
        memory_id=memory_id,
        changed_by=user_id,
        old_state=old_state,
        new_state=new_state
    )
    db.add(history)
    db.commit()
    return memory


# 事件处理函数
def _create_or_update_memory(memory_id, existing_memory, user, app_obj, request, result, db, old_state=None):
    """创建或更新记忆"""
    if existing_memory:
        existing_memory.state = MemoryState.active
        existing_memory.content = result['memory']
        return existing_memory, existing_memory.state if old_state is None else old_state
    
    memory = Memory(
        id=memory_id,
        user_id=user.id,
        app_id=app_obj.id,
        content=result['memory'],
        metadata_=request.metadata,
        state=MemoryState.active
    )
    db.add(memory)
    return memory, MemoryState.active


def _handle_add_event(result, memory_id, existing_memory, user, app_obj, request, db, **kwargs):
    """处理 ADD 事件"""
    memory, old_state = _create_or_update_memory(memory_id, existing_memory, user, app_obj, request, result, db)
    
    db.add(MemoryStatusHistory(
        memory_id=memory_id,
        changed_by=user.id,
        old_state=old_state,
        new_state=MemoryState.active
    ))
    return memory, "created"


def _handle_update_event(result, memory_id, existing_memory, user, app_obj, request, db, **kwargs):
    """处理 UPDATE 事件"""
    memory, old_state = _create_or_update_memory(memory_id, existing_memory, user, app_obj, request, result, db, MemoryState.active)
    action = "updated" if existing_memory else "created"
    
    db.add(MemoryStatusHistory(
        memory_id=memory_id,
        changed_by=user.id,
        old_state=old_state,
        new_state=MemoryState.active
    ))
    return memory, action


def _handle_delete_event(result, memory_id, existing_memory, user, app_obj=None, request=None, db=None, **kwargs):
    """处理 DELETE 事件"""
    if not existing_memory:
        return None, "skipped"
    
    existing_memory.state = MemoryState.deleted
    existing_memory.deleted_at = datetime.now(UTC)
    
    db.add(MemoryStatusHistory(
        memory_id=memory_id,
        changed_by=user.id,
        old_state=MemoryState.active,
        new_state=MemoryState.deleted
    ))
    return existing_memory, "deleted"


def _handle_none_event(result=None, memory_id=None, existing_memory=None, user=None, app_obj=None, request=None, db=None, **kwargs):
    """处理 NONE 事件"""
    return None, "skipped"


# 事件处理函数映射
EVENT_HANDLERS = {
    MEMORY_EVENT_ADD: _handle_add_event,
    MEMORY_EVENT_UPDATE: _handle_update_event,
    MEMORY_EVENT_DELETE: _handle_delete_event,
    MEMORY_EVENT_NONE: _handle_none_event,
}


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

    # Add eager loading for app and categories
    query = query.options(
        joinedload(Memory.app),
        joinedload(Memory.categories)
    ).distinct(Memory.id)

    # Get paginated results with transformer
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
                metadata_=memory.metadata_ or {}
            )
            for memory in items
            if check_memory_access_permissions(db, memory, app_id)
        ]
    )


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

    # Log what we're about to do
    logging.info("=" * 80)
    logging.info(f"📝 [MEMORY CREATE] 开始创建记忆")
    logging.info(f"   User ID: {request.user_id}")
    logging.info(f"   App: {request.app}")
    logging.info(f"   Text: {request.text[:100]}..." if len(request.text) > 100 else f"   Text: {request.text}")
    logging.info("=" * 80)
    
    # Try to get memory client safely
    try:
        memory_client = get_memory_client()
        if not memory_client:
            raise Exception("Memory client is not available")
        
        # 检查 graph store 状态
        enable_graph = getattr(memory_client, 'enable_graph', False)
        has_graph = getattr(memory_client, 'graph', None) is not None
        logging.info(f"✅ [MEMORY CLIENT] Memory client 已就绪")
        logging.info(f"   enable_graph: {enable_graph}")
        logging.info(f"   graph 实例存在: {has_graph}")
        
    except Exception as client_error:
        logging.error(f"❌ [MEMORY CLIENT] Memory client 不可用: {client_error}")
        logging.warning("   将仅在数据库中创建记录（无向量存储和图形存储）")
        # Return a json response with the error
        return {
            "error": str(client_error)
        }

    # Try to save to vector store and graph store via memory_client
    try:
        import time
        start_time = time.time()
        
        logging.info("🚀 [VECTOR STORE] 开始写入向量数据库...")
        logging.info(f"   文本长度: {len(request.text)} 字符")
        
        mem0_response = memory_client.add(
            request.text,
            user_id=request.user_id,  # Use string user_id to match search
            metadata={
                "source_app": "openmemory",
                "mcp_client": request.app,
            }
        )
        
        elapsed_time = time.time() - start_time
        logging.info(f"✅ [VECTOR STORE] 向量数据库写入完成 (耗时: {elapsed_time:.2f}秒)")
        
        # 详细记录响应内容
        logging.info("=" * 80)
        logging.info(f"📊 [MEM0 RESPONSE] Mem0 响应详情:")
        logging.info(f"   响应类型: {type(mem0_response)}")
        
        if isinstance(mem0_response, dict):
            logging.info(f"   响应键: {list(mem0_response.keys())}")
            
            # 检查向量存储结果
            if 'results' in mem0_response:
                results = mem0_response['results']
                logging.info(f"   ✅ [VECTOR STORE] 向量存储结果: {len(results)} 条记录")
                for i, result in enumerate(results, 1):
                    logging.info(f"      结果 {i}: event={result.get('event')}, id={result.get('id')}, memory={result.get('memory', '')[:50]}...")
            else:
                logging.warning("   ⚠️  [VECTOR STORE] 响应中缺少 'results' 字段")
            
            # 检查图形存储结果
            # mem0 master 分支返回格式: {"deleted_entities": [...], "added_entities": [...]}
            if 'relations' in mem0_response:
                relations = mem0_response.get('relations')
                if relations:
                    added_entities = relations.get('added_entities', [])
                    deleted_entities = relations.get('deleted_entities', [])
                    total_relations = len(added_entities) + len(deleted_entities)
                    
                    if total_relations > 0:
                        logging.info(f"   ✅ [GRAPH STORE] 图形存储结果: {total_relations} 个关系")
                        if added_entities:
                            logging.info(f"      添加的实体: {len(added_entities)} 个")
                            for i, rel in enumerate(added_entities[:3], 1):  # 只显示前3个
                                logging.info(f"        关系 {i}: {rel}")
                        if deleted_entities:
                            logging.info(f"      删除的实体: {len(deleted_entities)} 个")
                    else:
                        logging.warning("   ⚠️  [GRAPH STORE] relations 字段为空（可能未提取到关系）")
                else:
                    logging.warning("   ⚠️  [GRAPH STORE] relations 字段为空（可能未提取到关系）")
            else:
                logging.warning("   ⚠️  [GRAPH STORE] 响应中缺少 'relations' 字段（可能 graph store 未启用）")
        else:
            logging.warning(f"   ⚠️  响应格式异常: {mem0_response}")
        
        logging.info("=" * 80)
        
        # Process Mem0 response
        if isinstance(mem0_response, dict) and 'results' in mem0_response:
            created_memories = []
            updated_memories = []
            deleted_memories = []
            skipped_memories = []
            
            logging.info("💾 [MYSQL DB] 开始写入 MySQL 数据库...")
            logging.info(f"   待处理记录数: {len(mem0_response['results'])}")
            
            # 统计事件类型分布
            event_types = {}
            for result in mem0_response['results']:
                event_type = result.get('event', 'UNKNOWN')
                event_types[event_type] = event_types.get(event_type, 0) + 1
            if event_types:
                logging.info(f"   事件类型分布: {event_types}")
            
            for result in mem0_response['results']:
                event_type = result.get('event')
                memory_id = UUID(result['id'])
                
                logging.info(f"   📌 处理记忆 ID: {memory_id}, 事件类型: {event_type}")
                
                # 单个查询（使用主键索引）
                existing_memory = db.query(Memory).filter(Memory.id == memory_id).first()
                logging.info(f"      🔍 数据库查询结果: {'已存在' if existing_memory else '不存在'}")
                
                handler = EVENT_HANDLERS.get(event_type)
                logging.info(f"      🔍 Handler 查找: event_type='{event_type}', handler={handler is not None}")
                if handler:
                    logging.info(f"      🔍 Handler 函数: {handler.__name__}")
                
                if not handler:
                    logging.warning(f"   ⚠️  未知事件类型: {event_type}")
                    logging.warning(f"      🔍 可用的事件类型: {list(EVENT_HANDLERS.keys())}")
                    skipped_memories.append({'id': memory_id, 'event': event_type, 'reason': 'UNKNOWN_EVENT_TYPE'})
                    continue
                
                logging.info(f"      🔍 调用 handler: {handler.__name__}")
                try:
                    memory, action = handler(
                        result=result,
                        memory_id=memory_id,
                        existing_memory=existing_memory,
                        user=user,
                        app_obj=app_obj,
                        request=request,
                        db=db
                    )
                    logging.info(f"      ✅ Handler 返回: memory={memory is not None}, action='{action}'")
                    logging.info(f"      🔍 Memory 对象: id={memory.id if memory else None}, content={memory.content[:50] if memory and memory.content else None}...")
                except Exception as handler_error:
                    logging.error(f"      ❌ Handler 执行失败: {handler_error}")
                    import traceback
                    logging.error(f"      ❌ 错误堆栈:\n{traceback.format_exc()}")
                    skipped_memories.append({'id': memory_id, 'event': event_type, 'reason': f'HANDLER_ERROR: {str(handler_error)}'})
                    continue
                
                action_memory_map = {
                    "created": created_memories,
                    "updated": updated_memories,
                    "deleted": deleted_memories,
                }
                
                logging.info(f"      🔍 Action 映射检查: action='{action}', 可用 actions: {list(action_memory_map.keys())}")
                target_list = action_memory_map.get(action)
                logging.info(f"      🔍 Target list 查找结果: {target_list is not None}, 类型: {type(target_list)}")
                
                if target_list is not None:
                    logging.info(f"      ✅ 找到目标列表，准备添加 memory")
                    target_list.append(memory)
                    logging.info(f"      ✅ Memory 已添加到 {action} 列表，当前列表长度: {len(target_list)}")
                else:
                    reason = 'NOOP' if event_type == MEMORY_EVENT_NONE else 'UNKNOWN_EVENT_TYPE'
                    logging.warning(f"      ⚠️  Action '{action}' 不在 action_memory_map 中，将被跳过")
                    logging.warning(f"      🔍 原因: {reason}")
                    skipped_memories.append({'id': memory_id, 'event': event_type, 'reason': reason})
            
            # Commit all changes at once
            total_changes = len(created_memories) + len(updated_memories) + len(deleted_memories)
            total_processed = total_changes + len(skipped_memories)
            
            logging.info(f"   📊 处理统计:")
            logging.info(f"      - 创建: {len(created_memories)} 条")
            logging.info(f"      - 更新: {len(updated_memories)} 条")
            logging.info(f"      - 删除: {len(deleted_memories)} 条")
            logging.info(f"      - 跳过: {len(skipped_memories)} 条 (NONE/未知事件)")
            logging.info(f"      - 总计: {total_processed} 条")
            
            if skipped_memories:
                logging.info(f"   ⚠️  跳过的记录详情:")
                for skipped in skipped_memories:
                    logging.info(f"      - ID: {skipped['id']}, 事件: {skipped['event']}, 原因: {skipped['reason']}")
            
            if total_changes > 0:
                logging.info(f"   💾 提交 {total_changes} 条记录到 MySQL...")
                db.commit()
                for memory in created_memories + updated_memories + deleted_memories:
                    db.refresh(memory)
                
                logging.info(f"✅ [MYSQL DB] MySQL 数据库写入完成")
                logging.info(f"   ✅ 成功处理 {total_changes} 条记忆记录")
                
                # 验证图形存储数据（如果启用）
                # mem0 master 分支返回格式: {"deleted_entities": [...], "added_entities": [...]}
                if enable_graph and has_graph and 'relations' in mem0_response:
                    relations = mem0_response.get('relations')
                    if relations:
                        added_entities = relations.get('added_entities', [])
                        deleted_entities = relations.get('deleted_entities', [])
                        total_relations = len(added_entities) + len(deleted_entities)
                        
                        if total_relations > 0:
                            logging.info(f"✅ [GRAPH STORE] 图形存储写入成功，提取到 {total_relations} 个关系")
                            logging.info(f"   添加的实体: {len(added_entities)} 个，删除的实体: {len(deleted_entities)} 个")
                            logging.info("   💡 提示: 关系数据已保存到 Neo4j，可通过 Neo4j Browser 查询验证")
                        else:
                            logging.warning("⚠️  [GRAPH STORE] 图形存储响应为空（可能 LLM 未提取到关系）")
                    else:
                        logging.warning("⚠️  [GRAPH STORE] 图形存储响应为空（可能 LLM 未提取到关系）")
                elif enable_graph and has_graph:
                    logging.warning("⚠️  [GRAPH STORE] 图形存储未返回 relations（可能写入失败或超时）")
                else:
                    logging.info("ℹ️  [GRAPH STORE] 图形存储未启用")
                
                logging.info("=" * 80)
                logging.info(f"✅ [MEMORY CREATE] 记忆创建完成")
                logging.info("=" * 80)
                
                # Return the first memory (for API compatibility)
                # Priority: created > updated > deleted
                memory_lists = [
                    ("created", created_memories),
                    ("updated", updated_memories),
                    ("deleted", deleted_memories),
                ]
                
                for action, memory_list in memory_lists:
                    if memory_list:
                        return memory_list[0]
                
                logging.warning("⚠️  [MYSQL DB] 没有需要返回的记忆记录")
                return None
            else:
                logging.warning("⚠️  [MYSQL DB] 没有需要保存的记忆记录")
        else:
            logging.error(f"❌ [MEM0 RESPONSE] 响应格式异常或缺少 'results' 字段")
            logging.error(f"   响应内容: {mem0_response}")

    except Exception as mem0_error:
        import traceback
        error_trace = traceback.format_exc()
        logging.error("=" * 80)
        logging.error(f"❌ [ERROR] Mem0 操作失败")
        logging.error(f"   错误类型: {type(mem0_error).__name__}")
        logging.error(f"   错误信息: {str(mem0_error)}")
        logging.error(f"   错误堆栈:\n{error_trace}")
        logging.error("=" * 80)
        # Return a json response with the error
        return {
            "error": str(mem0_error)
        }




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