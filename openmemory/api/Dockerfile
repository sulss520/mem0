FROM python:3.12-slim

LABEL org.opencontainers.image.name="mem0/openmemory-api"

WORKDIR /usr/src/openmemory

COPY requirements.txt .
# 使用腾讯云镜像源加速 pip 安装
RUN pip install -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple --trusted-host mirrors.cloud.tencent.com

COPY config.json .
COPY . .

EXPOSE 8765
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
