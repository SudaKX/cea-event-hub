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

`.env` 里有**两项在生产环境必须设置**：

```ini
APP_ENV=production
# 未加盐的 IPv4 哈希可在数秒内被暴力反查，等于明文存储客户端地址。
# 不设置这一项应用会拒绝启动 —— 这是刻意的。
IP_HASH_SALT=<足够长的随机串>
# 生产必须保持 true
SESSION_COOKIE_SECURE=true
```

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

## 启动参数不是可选项

```bash
uvicorn app.main:app --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

| 参数 | 作用 | 不配置的后果 |
|---|---|---|
| `--workers 1` | 应用层限流是进程内实现 | 阈值被放大到 N 倍 |
| `--proxy-headers` | 让应用读取转发头 | 限流键永远是 nginx 的地址 |
| `--forwarded-allow-ips` | 限定只信任谁写的转发头 | 客户端可伪造 `X-Forwarded-For` 绕过限流 |

第三条尤其重要：**没有它，应用层限流等于没有**。它的取值必须与 nginx 的
`real_ip` 配置一致。

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
# 1. 健康检查
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
