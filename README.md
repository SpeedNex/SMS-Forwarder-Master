# 短信主控 (Master)

集中控制/监控多个 Agent，并汇总短信。适合部署在公网或可访问的服务器上，通过鉴权登录后下发任务、查看短信和日志。

## 运行环境
- Python 3.10+（推荐 3.10/3.11/3.12）
- 依赖：`fastapi`、`uvicorn`、`requests`

## 快速启动

```bash
cd SMS-Platform/Master
python -m venv .venv
source .venv/bin/activate          # Windows 用 .venv\Scripts\activate
pip install --upgrade pip
pip install fastapi uvicorn requests

uvicorn app:app --host 127.0.0.1 --port 8000
```

可选环境变量：

- `MASTER_DB_PATH`：数据库路径，默认 `master.db`
- `MASTER_LOG_FILE`：日志路径，默认 `master.log`
- `MASTER_SECRET`：Token 签名密钥，默认 `change-me-secret`
- `MASTER_ADMIN_USER` / `MASTER_ADMIN_PASSWORD`：初始登录账号密码，默认 `admin` / `admin123`
- `MASTER_TOKEN_EXPIRE_HOURS`：登录 Token 过期时间（小时），默认 48
- `MASTER_AGENT_OFFLINE_SECONDS`：Agent 超过该秒数未心跳则视为离线，默认 60
- `MASTER_LOG_LEVEL`：日志级别，默认 INFO

启动后访问 `/`，登录后即可管理 Agent。

## Agent 对接

1. 在「Agent 管理」中新建 Agent，获取 `API Key`。
2. Agent 端请求头携带 `X-Agent-Key: <API Key>`。
3. 主要接口：

- `POST /api/agent/heartbeat`  
  轮询心跳，Body（可选）：`{"name": "...", "status": "online", "meta": {...}}`。返回 pending 任务列表并自动标记为 `delivered`。

- `GET /api/agent/tasks`  
  轮询待执行任务（与 heartbeat 类似，不更新元数据）。

- `POST /api/agent/tasks/{task_id}/ack`  
  Body: `{"status": "done"|"error", "result": "..."}`，上报执行结果。

- `POST /api/agent/messages`  
  Body: `{"messages": [{"modem_port": "...", "sender": "...", "timestamp": "...", "text": "..."}]}`，上传收到的短信。

Agent 在内网/NAT 后无需暴露端口，只要主动向 Master 轮询并上传数据即可。

### Agent 侧对接参数（由 Agent 自身配置）

- `MASTER_URL`：Master 的公网地址，例如 `https://example.com`
- `MASTER_AGENT_KEY`：在 Master 后台生成的 API Key
- `MASTER_AGENT_NAME`：上报给 Master 的名称（可选）
- `MASTER_POLL_INTERVAL`：Agent 心跳/任务轮询间隔，默认 5 秒

> 以上参数由 Agent 实例读取其本地 `.env` 或在 Agent 前端页面配置存入自身数据库，Master 不存储这些值。

配置后 Agent 会：
1) 每隔 `MASTER_POLL_INTERVAL` 秒向 `/api/agent/heartbeat` 上报状态并拉取待发任务；  
2) 依次调用本地串口发送短信，完成后回执 `/api/agent/tasks/{id}/ack`；  
3) 收到的新短信在本地入库后也会推送到 Master 的 `/api/agent/messages`。

## Web 功能

- 登录鉴权后查看 Agent 状态、API Key。
- 指定 Agent 下发短信任务（待 Agent 拉取执行）。
- 汇总所有 Agent 的短信收件箱。
- 查看任务流转状态（pending/delivered/done/error）。
- 读取 Master 运行日志。

## Agent 在线判定规则

Master 每次返回 Agent 列表时，会根据 `last_seen` 与当前时间的差值判断是否在线，超出 `MASTER_AGENT_OFFLINE_SECONDS`（默认 60 秒）视为离线，并在前端标记。

## Agent 端失败重试与队列

Agent 侧若推送短信或任务回执到 Master 失败，会将请求入库到 `master_queue`，后台线程周期重试，避免暂时的网络中断导致消息丢失。

## 安全注意事项（公网部署必看）

- **不要将 `.env`、数据库、日志暴露为静态文件**。默认 FastAPI 仅返回 `index.html`，如经 Nginx/Apache 反代，务必禁止目录浏览并屏蔽 `.env`/`.db`/`.log` 等路径。可将 `.env` 放到非 Web 根目录，或使用进程环境变量。
- 启动前修改默认 `MASTER_SECRET`、`MASTER_ADMIN_USER`、`MASTER_ADMIN_PASSWORD`，并定期更换。
- 全站启用 HTTPS，限制管理端口访问源（防火墙/安全组/IP 白名单）。
- Agent API Key 丢失时立即新建并替换。
- 建议 Master 进程只监听 `127.0.0.1`（例如 `uvicorn app:app --host 127.0.0.1 --port 8000`），由 Nginx 等反代到公网，并在反代层做路径/头部过滤、访问控制。

## 目录结构
- `app.py`：后端服务（API、鉴权、Agent 对接）
- `public/`：前端页面与静态资源，`/assets/css`、`/assets/js`
- 默认数据库、日志、`.env` 均在应用根目录，不在 `public/`
