# cea-event-hub 后端

FastAPI + SQLAlchemy 2.x（同步）+ Alembic，当前 SQLite，预留迁移 MySQL。

分层：`api -> services -> repositories -> models`。两条硬规则：

1. **`services/` 不 import fastapi** —— 领域异常定义在 `core/exceptions.py`
   （该模块不依赖 Web 框架），由 `core/errors.py` 翻译成 HTTP。
2. **`repositories/` 不含业务判断** —— 只回答"存了什么"，不回答"能不能存"。

有测试自动检查第 1 条，避免它停留在口头约定。

## 环境准备

```bash
# 在本仓库根目录
.venv/Scripts/python.exe -m pip install -e "./backend[dev]"     # Windows
.venv/bin/python   -m pip install -e "./backend[dev]"           # Linux/macOS
```

依赖清单以 `backend/pyproject.toml` 为准，不要在别处重复维护。

## 配置

```bash
cp backend/.env.example backend/.env
```

`backend/.env` 已被 git 忽略。开发环境至少需要确认一项：

```ini
# 走 http://localhost 时必须关掉，否则浏览器不会回传会话 Cookie
SESSION_COOKIE_SECURE=false
```

生产环境另有两条硬性要求：必须设置 `IP_HASH_SALT`（否则应用拒绝启动——未加盐的
IPv4 哈希可在数秒内被暴力反查），以及保持 `SESSION_COOKIE_SECURE=true`。

## 建库与迁移

所有命令在 `backend/` 目录下执行。

```bash
cd backend

# 建出全部表
../.venv/Scripts/python.exe -m alembic upgrade head

# 回退全部迁移（验证迁移可逆）
../.venv/Scripts/python.exe -m alembic downgrade base

# 改完模型后生成新迁移
../.venv/Scripts/python.exe -m alembic revision --autogenerate -m "描述"
```

生成后**务必人工过一遍**自动生成的迁移：Alembic 无法识别所有语义变化（例如
重命名列会被当成"删一列 + 加一列"，直接跑会丢数据）。

数据库文件默认落在 `var/app.db`（已 git 忽略）。要换位置改 `DATABASE_URL`。

## 运行

```bash
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

开发模式下交互式文档在 `http://127.0.0.1:8000/api/v1/docs`。

**生产必须以单进程运行：**

```bash
uvicorn app.main:app --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

`--workers 1` 不是保守估计而是正确性要求：应用层限流是进程内内存实现，多进程会
让每个进程各持一份计数，实际阈值被放大到 N 倍。SQLite 的写入本来就串行化在单写者
锁上，多进程只会增加锁争用而不提升写入吞吐，因此单进程与存储模型也是一致的。

`--forwarded-allow-ips` 决定了是否采信 `X-Forwarded-For`。不配置的话，客户端自己
发一个该头就能把限流键算到伪造地址上，应用层限流形同虚设。

## 测试

```bash
cd backend
../.venv/Scripts/python.exe -m pytest -q
```

测试使用临时 SQLite 文件库，不会碰到 `var/app.db`。

## 首次启动

表建好后直接启动即可：用户表为空时平台会自动创建管理员。配置项与口令的输出规则
见 [docs/first-run-bootstrap.md](../docs/first-run-bootstrap.md)。

## 排错

**`pip install` 对所有包报 `from versions: none`**

本机全局 pip 配置指向的镜像已失效。本仓库在 `.venv/pip.ini` 里做了项目级覆盖
（阿里云为主，官方 PyPI 兜底），只影响这个虚拟环境。若该文件被删除，pip 会回落到
全局配置并再次失败。

注意 `.venv/pip.ini` 与 `backend/alembic.ini` **必须保持纯 ASCII**：pip 与 alembic
都用系统区域编码（本机为 gbk）读取这两个文件，非 ASCII 字节会让解析直接失败。

**启动报 `no such table: users`**

表还没建。执行 `alembic upgrade head`。
