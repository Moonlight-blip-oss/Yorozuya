# Yorozuya 服务器镜像：只装后端（FastAPI）+ 前端静态文件
#
# 构建上下文是**项目根**（docker-compose.yml 里写的是 context: ..）：
#   docker build -f deploy/Dockerfile -t yorozuya .
#
# 为什么不用 requirements.txt：见 requirements-server.txt 顶部注释
# （pywebview / pyinstaller 是桌面壳与打包用的，服务器上不需要也装不上）。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata：容器默认 UTC，agent 的「今天」和提醒会差 8 小时；装了好用 TZ 指定时区
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/yorozuya

COPY requirements-server.txt ./
RUN pip install -r requirements-server.txt

# ★ 目录结构必须保持：app/ 里是 server.py + db.py，yorozuya/ 是内核，renderer/ 是前端。
#   server.py 用 parents[1] 认项目根，靠它找 renderer/ 和 yorozuya/ —— 少一层就起不来。
COPY app ./app
COPY yorozuya ./yorozuya
COPY renderer ./renderer
COPY icon ./icon

# 非 root 跑：agent 内核能在容器里执行命令，别给它 root
RUN useradd -m -u 10001 yorozuya \
 && mkdir -p /srv/yorozuya/appdata \
 && chown -R yorozuya:yorozuya /srv/yorozuya
USER yorozuya

# 上传附件 / agent 快照统一落这里（见 yorozuya/paths.py）；compose 里挂了卷
ENV YOROZUYA_DATA_DIR=/srv/yorozuya/appdata \
    TZ=Asia/Shanghai

EXPOSE 8902

# --app-dir app：让 `import db` 能找到 app/db.py（server.py 开头就 import 它）
# --proxy-headers：让 Caddy/Nginx 转发时 FastAPI 拿到真实的 https/base_url
CMD ["uvicorn", "server:app", "--app-dir", "app", \
     "--host", "0.0.0.0", "--port", "8902", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
