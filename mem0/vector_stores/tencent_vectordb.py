import logging
import time
from typing import Dict, List, Optional

from pydantic import BaseModel

from mem0.vector_stores.base import VectorStoreBase

try:
    from tcvectordb import VectorDBClient
    from tcvectordb.model.enum import FieldType, IndexType, MetricType, ReadConsistency
    from tcvectordb.model.index import Index, VectorIndex, FilterIndex, HNSWParams
    from tcvectordb.model.document import Document, Filter, SearchParams
    from tcvectordb.model.collection import Collection
except ImportError:
    raise ImportError(
        "The 'tcvectordb' library is required. Please install it using 'pip install tcvectordb'."
    )

logger = logging.getLogger(__name__)


class OutputData(BaseModel):
    id: Optional[str]  # memory id
    score: Optional[float]  # distance
    payload: Optional[Dict]  # metadata


class TencentVectorDB(VectorStoreBase):
    def __init__(
        self,
        url: str,
        username: str,
        key: str,
        database_name: str,
        collection_name: str,
        embedding_model_dims: int,
        metric_type: str = "cosine",
        timeout: int = 30,
    ) -> None:
        """Initialize the Tencent Cloud VectorDB database.

        Args:
            url (str): 腾讯云向量数据库连接地址
            username (str): 用户名
            key (str): API密钥
            database_name (str): 数据库名称
            collection_name (str): 集合名称
            embedding_model_dims (int): 向量维度
            metric_type (str): 距离度量类型，可选值: cosine, l2, ip
            timeout (int): 连接超时时间（秒）
        """
        self.url = url
        self.username = username
        self.key = key
        self.database_name = database_name
        self.collection_name = collection_name
        self.embedding_model_dims = embedding_model_dims
        self.timeout = timeout

        # 转换距离度量类型
        metric_map = {
            "cosine": MetricType.COSINE,
            "l2": MetricType.L2,
            "ip": MetricType.IP,
        }
        if metric_type.lower() not in metric_map:
            raise ValueError(f"Unsupported metric_type: {metric_type}. Supported: cosine, l2, ip")
        self.metric_type = metric_map[metric_type.lower()]

        # 初始化客户端
        self.client = VectorDBClient(
            url=url,
            username=username,
            key=key,
            timeout=timeout,
        )

        # 确保数据库和集合存在
        self._create_database_if_not_exists()
        self.create_col(
            name=self.collection_name,
            vector_size=self.embedding_model_dims,
            distance=metric_type,
        )

    def _create_database_if_not_exists(self):
        """创建数据库（如果不存在）"""
        try:
            # 使用 create_database_if_not_exists 方法创建数据库（如果不存在）
            self._database = self.client.create_database_if_not_exists(self.database_name)
            logger.info(f"Database '{self.database_name}' is ready (created if not exists)")
        except Exception as e:
            logger.error(f"Error creating/accessing database: {e}")
            raise

    def create_col(self, name: str, vector_size: int, distance: str):
        """创建新的集合

        Args:
            name (str): 集合名称
            vector_size (int): 向量维度
            distance (str): 距离度量类型
        """
        try:
            # 检查集合是否已存在
            try:
                collection_info = self._database.describe_collection(name)
                self._collection = self._database.collection(name)
                logger.info(f"Collection {name} already exists. Skipping creation.")
                return
            except Exception:
                # 集合不存在，需要创建
                pass

            # 转换距离度量类型
            metric_map = {
                "cosine": MetricType.COSINE,
                "l2": MetricType.L2,
                "ip": MetricType.IP,
            }
            metric_type = metric_map.get(distance.lower(), MetricType.COSINE)

            # 定义集合结构
            # 创建索引 - 根据腾讯云向量数据库SDK的实际API调整
            # 注意：由于 insert 方法中 metadata 是通过 setattr 直接设置为 Document 的顶级字段
            # 所以需要为常用的 metadata 字段创建 FilterIndex，以便用于过滤查询
            index = Index(
                FilterIndex(
                    name="id",
                    field_type=FieldType.String,
                    index_type=IndexType.PRIMARY_KEY,
                ),
                VectorIndex(
                    name="vector",
                    dimension=vector_size,
                    index_type=IndexType.HNSW,
                    metric_type=metric_type,
                    params=HNSWParams(m=16, efconstruction=200),
                ),
                # 为常用的 metadata 字段创建 FilterIndex，以便用于过滤查询
                FilterIndex(
                    name="user_id",
                    field_type=FieldType.String,
                    index_type=IndexType.FILTER,
                ),
                FilterIndex(
                    name="agent_id",
                    field_type=FieldType.String,
                    index_type=IndexType.FILTER,
                ),
                FilterIndex(
                    name="run_id",
                    field_type=FieldType.String,
                    index_type=IndexType.FILTER,
                ),
                FilterIndex(
                    name="actor_id",
                    field_type=FieldType.String,
                    index_type=IndexType.FILTER,
                ),
                FilterIndex(
                    name="role",
                    field_type=FieldType.String,
                    index_type=IndexType.FILTER,
                ),
                # 保留 metadata 字段用于其他自定义字段（可选）
                FilterIndex(
                    name="metadata",
                    field_type=FieldType.Json,
                    index_type=IndexType.FILTER,
                ),
            )

            # 创建集合
            self._collection = self._database.create_collection(
                name=name,
                shard=1,
                replicas=0,
                description="mem0 collection",
                index=index,
            )
            logger.info(f"Created collection: {name}")

            # 等待集合就绪
            max_wait_time = 60  # 最大等待时间（秒）
            wait_interval = 2  # 等待间隔（秒）
            elapsed_time = 0
            while elapsed_time < max_wait_time:
                try:
                    collection_info = self._database.describe_collection(name)
                    # 根据实际API调整状态检查
                    if hasattr(collection_info, "status") and collection_info.status == "NORMAL":
                        logger.info(f"Collection {name} is ready.")
                        break
                    elif not hasattr(collection_info, "status"):
                        # 如果describe_collection成功，说明集合已就绪
                        logger.info(f"Collection {name} is ready.")
                        break
                    logger.info(f"Waiting for collection {name} to be ready...")
                except Exception as e:
                    logger.debug(f"Error checking collection status: {e}")
                time.sleep(wait_interval)
                elapsed_time += wait_interval

            # 获取集合对象
            self._collection = self._database.collection(name)
        except Exception as e:
            logger.error(f"Error creating collection: {e}")
            raise

    def insert(self, vectors: List[List[float]], payloads: List[Dict] = None, ids: List[str] = None):
        """插入向量到集合中

        Args:
            vectors (List[List[float]]): 要插入的向量列表
            payloads (List[Dict], optional): 对应的元数据列表
            ids (List[str], optional): 对应的ID列表
        """
        if not vectors:
            return

        if ids is None:
            ids = [str(i) for i in range(len(vectors))]
        if payloads is None:
            payloads = [{}] * len(vectors)

        # 准备文档数据
        documents = []
        for idx, vector, metadata in zip(ids, vectors, payloads):
            # 根据腾讯云向量数据库SDK的实际API调整Document创建方式
            doc = Document(
                id=idx,
                vector=vector,
            )
            # 将metadata作为文档的其他字段添加
            if metadata:
                for key, value in metadata.items():
                    setattr(doc, key, value)
            documents.append(doc)

        # 批量插入
        try:
            self._collection.upsert(documents)
            logger.info(f"Inserted {len(documents)} vectors into collection {self.collection_name}")
        except Exception as e:
            logger.error(f"Error inserting vectors: {e}")
            raise

    def search(
        self, query: str, vectors: List[float], limit: int = 5, filters: Dict = None
    ) -> List[OutputData]:
        """搜索相似向量

        Args:
            query (str): 查询字符串
            vectors (List[float]): 查询向量
            limit (int, optional): 返回结果数量. 默认 5
            filters (Dict, optional): 过滤条件. 默认 None

        Returns:
            List[OutputData]: 搜索结果
        """
        # 构建过滤条件
        search_filter = None
        if filters:
            search_filter = self._create_filter(filters)

        # 执行向量搜索
        try:
            search_params = SearchParams(ef=200)
            results = self._collection.search(
                vectors=[vectors],
                filter=search_filter,
                params=search_params,
                limit=limit,
                retrieve_vector=False,
            )

            # 解析结果
            output = []
            if results and len(results) > 0:
                # 根据实际API调整结果解析方式
                result_list = results[0] if isinstance(results, list) else results
                for doc in result_list:
                    # 根据实际返回的数据结构调整
                    doc_id = doc.get("id") if isinstance(doc, dict) else getattr(doc, "id", None)
                    doc_score = doc.get("score", 0.0) if isinstance(doc, dict) else getattr(doc, "score", 0.0)
                    # 提取metadata，排除id和vector字段
                    doc_dict = doc if isinstance(doc, dict) else doc.__dict__
                    metadata = {k: v for k, v in doc_dict.items() if k not in ["id", "vector", "score"]}
                    
                    output_data = OutputData(
                        id=doc_id,
                        score=doc_score,
                        payload=metadata,
                    )
                    output.append(output_data)

            return output
        except Exception as e:
            logger.error(f"Error searching vectors: {e}")
            raise

    def delete(self, vector_id: str):
        """根据ID删除向量

        Args:
            vector_id (str): 要删除的向量ID
        """
        try:
            self._collection.delete_by_ids([vector_id])
            logger.debug(f"Deleted vector with id: {vector_id}")
        except Exception as e:
            logger.error(f"Error deleting vector: {e}")
            raise

    def update(self, vector_id: str = None, vector: List[float] = None, payload: Dict = None):
        """更新向量及其元数据

        Args:
            vector_id (str): 要更新的向量ID
            vector (List[float], optional): 更新的向量
            payload (Dict, optional): 更新的元数据
        """
        if vector_id is None:
            raise ValueError("vector_id is required for update")

        doc = Document(
            id=vector_id,
            vector=vector,
            metadata=payload,
        )

        try:
            self._collection.upsert([doc])
            logger.debug(f"Updated vector with id: {vector_id}")
        except Exception as e:
            logger.error(f"Error updating vector: {e}")
            raise

    def get(self, vector_id: str) -> OutputData:
        """根据ID获取向量

        Args:
            vector_id (str): 要获取的向量ID

        Returns:
            OutputData: 获取到的向量数据
        """
        try:
            # 使用 searchById 方法根据ID查询向量
            # searchById 返回格式: List[List[Dict]]，外层列表对应每个查询ID，内层列表是结果
            results = self._collection.searchById([vector_id], retrieve_vector=False)
            if results and len(results) > 0 and len(results[0]) > 0:
                # results[0] 是第一个查询ID的结果列表
                doc = results[0][0]  # 取第一个结果
                # 处理返回的数据（可能是 dict）
                if isinstance(doc, dict):
                    doc_id = doc.get("id")
                    # 提取metadata，排除id和vector字段
                    metadata = {k: v for k, v in doc.items() if k not in ["id", "vector"]}
                else:
                    # 如果是对象，尝试获取属性
                    doc_id = getattr(doc, "id", None)
                    doc_dict = doc.__dict__ if hasattr(doc, "__dict__") else {}
                    metadata = {k: v for k, v in doc_dict.items() if k not in ["id", "vector"]}
                
                return OutputData(
                    id=doc_id,
                    score=None,
                    payload=metadata,
                )
            return OutputData(id=None, score=None, payload=None)
        except Exception as e:
            logger.error(f"Error getting vector: {e}")
            raise

    def list_cols(self) -> List[str]:
        """列出所有集合

        Returns:
            List[str]: 集合名称列表
        """
        try:
            collections = self._database.list_collections()
            return [col.name for col in collections]
        except Exception as e:
            logger.error(f"Error listing collections: {e}")
            raise

    def delete_col(self):
        """删除集合"""
        try:
            self._database.drop_collection(self.collection_name)
            logger.info(f"Deleted collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Error deleting collection: {e}")
            raise

    def col_info(self) -> Dict:
        """获取集合信息

        Returns:
            Dict: 集合信息
        """
        try:
            collection_info = self._database.describe_collection(self.collection_name)
            return {
                "name": collection_info.name,
                "status": collection_info.status,
                "shard": collection_info.shard,
                "replicas": collection_info.replicas,
                "description": collection_info.description,
            }
        except Exception as e:
            logger.error(f"Error getting collection info: {e}")
            raise

    def list(self, filters: Dict = None, limit: int = 100) -> List[List[OutputData]]:
        """列出集合中的所有向量

        Args:
            filters (Dict, optional): 过滤条件
            limit (int, optional): 返回数量. 默认 100

        Returns:
            List[List[OutputData]]: 向量列表
        """
        try:
            search_filter = self._create_filter(filters) if filters else None

            # 使用query方法获取文档
            # 注意：根据腾讯云向量数据库的实际API调整
            try:
                results = self._collection.query(
                    filter=search_filter,
                    limit=limit,
                    retrieve_vector=False,
                )
            except AttributeError:
                # 如果query方法不存在，使用其他方法
                # 这里可能需要根据实际API调整
                results = []

            memories = []
            if results:
                for doc in results:
                    # 根据实际返回的数据结构调整
                    doc_dict = doc if isinstance(doc, dict) else doc.__dict__
                    doc_id = doc_dict.get("id") if isinstance(doc, dict) else getattr(doc, "id", None)
                    # 提取metadata，排除id和vector字段
                    metadata = {k: v for k, v in doc_dict.items() if k not in ["id", "vector"]}
                    
                    obj = OutputData(
                        id=doc_id,
                        score=None,
                        payload=metadata,
                    )
                    memories.append(obj)

            return [memories]
        except Exception as e:
            logger.error(f"Error listing vectors: {e}")
            raise

    def reset(self):
        """重置集合（删除并重新创建）"""
        logger.warning(f"Resetting collection {self.collection_name}...")
        try:
            self.delete_col()
            # 等待删除完成
            time.sleep(2)
            self.create_col(
                name=self.collection_name,
                vector_size=self.embedding_model_dims,
                distance=self.metric_type.name.lower() if hasattr(self.metric_type, "name") else "cosine",
            )
        except Exception as e:
            logger.warning(f"Error resetting collection: {e}")
            raise

    def _create_filter(self, filters: Dict) -> Filter:
        """创建过滤条件

        Args:
            filters (Dict): 过滤条件字典

        Returns:
            Filter: 过滤条件对象
        """
        if not filters:
            return None

        # 构建过滤表达式
        # 注意：由于 insert 方法中 metadata 是通过 setattr 直接设置为 Document 的顶级字段
        # 所以过滤语法应该直接使用字段名，而不是 metadata["key"]
        conditions = []
        for key, value in filters.items():
            if isinstance(value, str):
                # 字符串值需要用引号包裹
                conditions.append(f'{key} = "{value}"')
            elif isinstance(value, (int, float)):
                # 数值直接使用
                conditions.append(f'{key} = {value}')
            elif isinstance(value, dict):
                # 支持范围查询等复杂条件
                if "gte" in value and "lte" in value:
                    conditions.append(
                        f'{key} >= {value["gte"]} AND {key} <= {value["lte"]}'
                    )
                elif "gte" in value:
                    conditions.append(f'{key} >= {value["gte"]}')
                elif "lte" in value:
                    conditions.append(f'{key} <= {value["lte"]}')
            else:
                # 其他类型转换为字符串
                conditions.append(f'{key} = "{value}"')

        filter_expr = " AND ".join(conditions)
        # Filter 类初始化方法签名: Filter(cond: str)，直接传入过滤表达式字符串
        return Filter(filter_expr)

