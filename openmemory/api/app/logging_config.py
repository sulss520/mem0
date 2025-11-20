"""
日志配置模块
支持通过环境变量配置日志级别、输出位置、文件路径等
与 graphs_proxy 保持一致的日志管理方式
"""
import logging
import os
from logging.handlers import RotatingFileHandler

# ============================================================================
# 日志配置（服务级别）
# ============================================================================
# 支持通过环境变量配置：
# - LOG_LEVEL: 日志级别（DEBUG, INFO, WARNING, ERROR），默认 INFO
# - LOG_OUTPUT: 日志输出位置（file, console, both），默认 file
# - LOG_FILE: 日志文件名，默认 api.log
# - LOG_DIR: 日志目录，默认 logs（相对于当前工作目录）
# - LOG_MAX_BYTES: 单个日志文件最大大小，支持单位（M/MB, K/KB, G/GB），默认 10M
# - LOG_BACKUP_COUNT: 保留的备份文件数量，默认 5
# ============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_OUTPUT = os.getenv("LOG_OUTPUT", "file").lower()  # file, console, both
LOG_FILE = os.getenv("LOG_FILE", "api.log")  # 日志文件名
LOG_DIR = os.getenv("LOG_DIR", "logs")  # 日志目录（相对于当前工作目录）

# 解析 LOG_MAX_BYTES，支持 M/MB 单位（例如：10M, 10MB）
def parse_size(size_str: str) -> int:
    """解析大小字符串，支持 M/MB/K/KB/G/GB 单位
    
    Args:
        size_str: 大小字符串，例如 "10M", "10MB", "10485760"
    
    Returns:
        字节数
    """
    size_str = size_str.strip().upper()
    
    # 单位到字节的乘数映射（按长度降序排列，先匹配长单位）
    units = [
        ("GB", 1024 ** 3),
        ("MB", 1024 ** 2),
        ("KB", 1024 ** 1),
        ("G", 1024 ** 3),
        ("M", 1024 ** 2),
        ("K", 1024 ** 1),
    ]
    
    for unit, multiplier in units:
        if size_str.endswith(unit):
            return int(size_str[:-len(unit)]) * multiplier
    
    # 纯数字，直接返回
    return int(size_str)

LOG_MAX_BYTES = parse_size(os.getenv("LOG_MAX_BYTES", "10M"))  # 单个日志文件最大大小（默认 10M）
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))  # 保留的备份文件数量

# 配置日志格式
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
log_level = getattr(logging, LOG_LEVEL, logging.INFO)

# 创建日志目录（如果输出到文件）
handlers = []
if LOG_OUTPUT in ["file", "both"]:
    # 确保日志目录存在
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file_path = os.path.join(LOG_DIR, LOG_FILE)
    # 使用 RotatingFileHandler 实现日志轮转
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    handlers.append(file_handler)

if LOG_OUTPUT in ["console", "both"]:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    handlers.append(console_handler)

# 如果未指定输出方式，默认只输出到文件
if not handlers:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file_path = os.path.join(LOG_DIR, LOG_FILE)
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(log_format))
    handlers.append(file_handler)

# 配置根日志记录器
root_logger = logging.getLogger()
root_logger.setLevel(log_level)
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
for handler in handlers:
    root_logger.addHandler(handler)

# 配置 uvicorn 日志
uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_logger.setLevel(log_level)
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.setLevel(log_level)

logger = logging.getLogger(__name__)

# 记录日志配置信息
if LOG_OUTPUT in ["file", "both"]:
    log_file_path = os.path.join(LOG_DIR, LOG_FILE)
    logger.info(f"日志文件: {os.path.abspath(log_file_path)}")
if LOG_OUTPUT in ["console", "both"]:
    logger.info("日志输出: 控制台")
logger.info(f"日志级别: {LOG_LEVEL}")

