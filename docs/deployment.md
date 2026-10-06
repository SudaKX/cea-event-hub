# 部署

单机部署：nginx 反向代理 + systemd 托管后端，SQLite 存储。

## 首次部署顺序

顺序不能随意调换：迁移必须在服务启动前完成，否则启动会因为找不到表而失败。

```bash
# 0. 目录与用户
sudo useradd --system --home /srv/cea --shell /usr/sbin/nologin cea
sudo mkdir -p /srv/cea/{backend,content,data,var,spa}
sudo chown -R cea:cea /srv/cea

# 1. 代码与依赖
sudo -u cea git clone <repo> /srv/cea/backend
cd /srv/cea/backend
sudo -u cea python3 -m venv /srv/cea/.venv
sudo -u cea /srv/cea/.venv/bin/pip install -e ".[dev]"   # 生产可去掉 [dev]

# 2. 配置
sudo -u cea cp backend/.env.example backend/.env
sudo -u cea $EDITOR backend/.env
```

`.env` 里有**四项在生产环境必须逐字核对**。前三项写错会立刻暴露，第四项不会 —— 它只会
静静地把人引到错的地方：

```ini
APP_ENV=production

# 未加盐的 IPv4 哈希可在数秒内被暴力反查，等于明文存储客户端地址。
# 不设置这一项应用会拒绝启动 —— 这是刻意的。
IP_HASH_SALT=<足够长的随机串>

# 生产必须保持 true
SESSION_COOKIE_SECURE=true

# **邮件链接的基址**，必须是**前端站点**的 origin（不是 API 的）。
# 它错了不会报任何错：注册照常返回"请查收邮件"，而信里的链接指向 localhost。
# 用户点开是空白页，你会以为是邮件服务的问题。
PUBLIC_BASE_URL=https://<你的站点域名>
```

监听地址（`API_HOST` / `API_PORT`）默认就是 `127.0.0.1:8000`。改端口时
`deploy/nginx` 里**四处 `proxy_pass`** 要跟着改。

```bash
# 3. 建库（在服务启动之前）
cd /srv/cea/backend
sudo -u cea /srv/cea/.venv/bin/alembic upgrade head

# 4. 首个管理员由启动引导自动创建，无需手工步骤。
#    若想避免随机口令进入日志，先在 .env 里设置：
#      ADMIN_INITIAL_PASSWORD=<一个强口令>
#    这样启动输出中不会出现口令。

# 5. 前端构建
cd /srv/cea/frontend
sudo -u cea npm ci
sudo -u cea npm run build
sudo -u cea cp -r dist/. /srv/cea/spa/
# dist/ 里同时产出 sdk/v1/cea.js，nginx 会从 /sdk/ 直出

# 6. nginx
sudo cp deploy/nginx/cea-event-hub.conf /etc/nginx/conf.d/cea.conf
sudo nginx -t
sudo systemctl reload nginx

# 7. 后端服务
sudo cp deploy/systemd/cea-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cea-api

# 8. 验证
curl -fsS http://127.0.0.1:8000/api/v1/health
sudo journalctl -u cea-api -n 30
```

## 启动方式，以及为什么参数不是可选项

```bash
.venv/bin/python -m app        # 生产：读 .env 里的 API_HOST / API_PORT
uvicorn app.main:app --reload  # 开发：CLI 直起，可用 --reload
```

生产的启动命令是 **`python -m app`**（`backend/app/__main__.py`），不是直接调 uvicorn。
原因是监听地址要放在 `.env` 里，而 **uvicorn 读不到那份 `.env`**：它的 `--host`/`--port`
是 CLI 参数，它的 `UVICORN_*` 环境变量也只从**进程环境**取值。所以把 `UVICORN_PORT`
写进 `.env` 会被静默忽略 —— 服务照旧听 8000，没有任何报错。由应用读配置再显式传给
uvicorn，这个错位就不存在了。

三个参数**刻意写死在那个入口里**，不从 `.env` 取：

| 参数 | 作用 | 不配置的后果 |
|---|---|---|
| `workers=1` | 应用层限流是进程内实现 | 阈值被放大到 N 倍 |
| `proxy_headers=True` | 让应用读取转发头 | 限流键永远是 nginx 的地址 |
| `forwarded_allow_ips` | 限定只信任谁写的转发头 | 客户端可伪造 `X-Forwarded-For` 绕过限流 |

第三条尤其重要：**没有它，应用层限流等于没有**。它现在由 `TRUSTED_PROXY_IPS`
**推导**（`app/__main__.py`），而不是与 nginx 各写一份 —— 原先那种写法要求两处人工保持
一致，而"要求一致"的下一站通常是"某天不再一致"。它的取值必须与 nginx 的 `real_ip`
配置一致（若前面有 CDN）。

## 四个必须同时正确的配置面

这些约束分散在 nginx 与 FastAPI 两侧，任何一侧漏掉都会静默破坏某项隔离：

| 约束 | nginx 侧 | 应用侧 |
|---|---|---|
| `/content/**` 带 `ACAO: *` | 不覆盖该头 | `ContentStaticFiles` 设置 |
| `/api/**` **不带**任何 CORS 头 | 不添加 | 刻意不注册 `CORSMiddleware` |
| `/data/**` 不可达 | `return 404` | 不挂载该目录 |
| 体积上限 | `client_max_body_size` | `MAX_REQUEST_BYTES` 等 |

## 验收检查

部署完成后逐条确认：

```bash
# 1. 健康检查。它**真的会去连库**，因此这一条同时是"库通了"与"迁移跑了"的验收：
#    -f 让非 2xx 直接以非零退出，即 503 会让这条命令失败 —— 那正是我们要的
curl -fsS http://<host>/api/v1/health

# 2. /content 带通配 CORS 头
curl -sI http://<host>/content/<event_id>/index.html | grep -i access-control

# 3. /api 不带任何 CORS 头（这一条必须是空的）
curl -sI http://<host>/api/v1/health | grep -i access-control   # 期望无输出

# 4. /data 硬拒绝
curl -s -o /dev/null -w '%{http_code}\n' http://<host>/data/<event_id>/anything
# 期望 404

# 5. SPA history fallback
curl -s -o /dev/null -w '%{http_code}\n' http://<host>/admin/events
# 期望 200

# 6. SDK 可访问
curl -sI http://<host>/sdk/v1/cea.js | head -1
# 期望 200
```

第 2 与第 3 条是**一对**：只满足其中一条就说明隔离机制被破坏了一半。

## 健康检查的语义

`/api/v1/health` 每次被调用都会**真的连一次库**，因此它回答的是"服务现在能不能干活"，而不是"进程还在不在"。三种情形分开报，因为要采取的动作完全不同：

| 情形 | 状态码 | `database` 字段 | 该做什么 |
|---|---|---|---|
| 一切正常 | `200` | `ok` | 无事。响应里的 `revision` 与 `expected_revision` 应当一致 |
| 连不上数据存储 | `503` | `unreachable` | 查数据库进程、磁盘、连接串 |
| 连得上，但迁移从未跑过 | `503` | `unmigrated` | 跑 `alembic upgrade head` |
| 迁移过，但版本落后于代码 | `200` | `ok` | 计划一次迁移；服务当前还能干活 |

第三行与第四行的区别值得记住：**"没迁移"是故障，"迁移落后"不是**。落后的库通常仍能服务旧版本的接口，把它算成 503 会让人把一次正常的滚动发布当成线上事故。

`expected_revision` 取自代码里的迁移脚本，`revision` 取自库里的表 —— 两个来源彼此独立，因此二者不一致本身就是线索，不必登服务器查库。

响应**只含状态**（`app`、`env`、`database`、两个版本号）。它是匿名可访问的，因此不含凭据、连接串与绝对路径；往响应里加字段前请先读 `tests/test_health.py` 里的白名单 —— 那个白名单是它唯一的守门人。

**监控侧注意：不要把短暂的 503 当重启触发器。** 数据库抖动会让它返回 503，那是正确行为；但 systemd 的 `Restart=on-failure` 只看进程退出码、不读这个端点，因此不会形成重启风暴 —— 如果你的监控是自己写的，请照同样的方式处理。

轮询路径请用 `/api/v1/health`。根路径 `/health` 归前端路由（顶层路径一律解释为活动标识），后端不占用它。

## 回滚

无状态应用，回滚就是换回上一版代码并重启：

```bash
sudo -u cea git -C /srv/cea/backend checkout <上一个版本>
sudo -u cea /srv/cea/.venv/bin/alembic downgrade -1   # 仅当该版本含迁移
sudo systemctl restart cea-api
```

迁移一律走 Alembic 的降级路径，不手工改库。

## 备份

需要备份的是三样：

| 对象 | 说明 |
|---|---|
| `var/app.db` | 数据库（含账号、活动、提交元信息） |
| `data/` | 上传的附件字节 |
| `content/` | 活动网页内容 |

`content/` 与 `data/` 丢失后无法从数据库恢复 —— 库里只有元信息。

SQLite 建议用 `sqlite3 var/app.db ".backup /backup/app-$(date +%F).db"`，
而不是直接复制文件（运行中复制可能拿到不一致的快照）。

## 向 MySQL 迁移

代码层面已经为此做了准备（方言 SQL 全部关在 `repositories/`、约束命名统一、
写入前归一化、时间统一 UTC），迁移预期是替换配置而非重写代码。

需要复核的点：

1. 换 `DATABASE_URL` 与驱动（`pymysql`）
2. 仓储层的 upsert 写法：SQLite 的 `ON CONFLICT` 对应 MySQL 的
   `ON DUPLICATE KEY UPDATE`
3. 连接级 `PRAGMA foreign_keys` 钩子改为无操作（MySQL 恒为开启）
4. 唯一约束的行为：写入前已归一化，因此应当一致；用一份数据抽样验证
5. `alembic upgrade head` 在空库上跑一遍，确认约束名可读可删
6. 确认 `--workers 1` 的前提是否仍然需要（限流实现若换成 Redis 则可放宽）
7. **`users.id` 的 id 分配策略。** SQLite 侧靠 `AUTOINCREMENT` 保证删除账号后 id 不被复用（`submissions.submitter` 是派生字符串 `u:{id}`、没有外键，id 一旦复用，新账号会"继承"被删账号的提交与配额）。MySQL 的 `AUTO_INCREMENT` **计数器自 8.0 起持久化**，因此 8.0 及以上无需对应改动；更老的版本在服务重启后按 `max(id)+1` 重算，**可能复用 id**。迁到 MySQL 前请确认版本，并考虑在旧版本上改用别的办法（例如删除时改写 `submitter`）

## 已验证项与未验证项

`deploy/nginx/cea-event-hub.conf` **已用 `nginx -t` 校验并通过**（nginx 1.24.0）。校验过程抓到一个真错误：认证限流区原来写的是 `rate=0.5r/s`，而 nginx **只接受整数速率**（`invalid rate`），"两秒一次"必须写成 `30r/m`。这份配置此前从未被验证过 —— 这条正是 `nginx -t` 存在的意义。

在验证环境（WSL / Ubuntu 24.04，后端仍在 Windows 侧，经镜像网络以 `127.0.0.1:8000` 可达）里跑过、**已确认**的行为：

| 检查 | 结果 |
|---|---|
| `/admin/events`、`/2026spring` | 200（SPA history fallback 生效，活动标识也交前端） |
| `/data/<event>/...` | 404，响应体是 nginx 自己的页面，不泄露路径 |
| `/api/v1/health` | **零个** `access-control-*` 头 |
| `/content/<event>/index.html` | `access-control-allow-origin: *`；同目录下的静态数据文件同样可取 |
| `/sdk/v1/cea.js` | 200，带 `ACAO: *` 与 `immutable` 长缓存 |
| `/assets/<带哈希>` | 200；不存在的文件名 → 404（**不**回退到 index.html） |
| 入口（`/admin/events`） | `Cache-Control: no-cache`，发版后不会拿到旧壳 |

第二与第三行是设计决策 5 的那一对，**两侧必须在同一条链路上同时成立**，因此单独复核过：直连后端时 `/api` 同样零个 CORS 头，说明那个"没有"是后端本就不加，而不是 nginx 在剥离。

**仍未验证**，上生产前请补：

1. `client_max_body_size 64m` 的实际拦截 —— 只确认了指令能解析，没有真的推一个超限请求
2. 三个 `limit_req_zone` 在真实流量下的行为与 429 响应
3. 前置 CDN 时的 `set_real_ip_from` / `real_ip_recursive` 那一段 —— 本环境没有 CDN，**保持注释状态**
4. `--forwarded-allow-ips` 与 `real_ip` 的取值一致性（见上文"启动参数不是可选项"）—— 只有在真实代理链路上才有意义
