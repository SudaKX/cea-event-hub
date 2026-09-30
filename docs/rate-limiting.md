# 限流

两层，因为**它们能拿到的键不同**——nginx 不知道请求属于谁，FastAPI 知道。

| 层 | 键 | 特点 |
|---|---|---|
| nginx `limit_req` | 客户端 IP | 最早、跨 worker 共享（共享内存）、零应用成本挡住洪流 |
| FastAPI 内存实现 | IP + 登录用户 | 能识别登录用户，可按端点细化 |

两层不是重复：只查用户维度，换个账号就能绕过；只查来源维度，同一出口后的所有人
会被连坐。

## nginx 层

```nginx
limit_req_zone $binary_remote_addr zone=cea_general:10m rate=20r/s;
limit_req_zone $binary_remote_addr zone=cea_submit:10m  rate=1r/s;
limit_req_zone $binary_remote_addr zone=cea_auth:10m    rate=0.5r/s;

limit_req_status 429;     # 默认是 503，语义不对
```

`limit_req_zone` 用共享内存，因此天然跨 worker——这是它比应用层内存限流更可靠的
地方。

## 应用层

配置项：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | 整体开关 |
| `RATE_LIMIT_SUBMIT_IP_MAX` / `..._WINDOW` | 30 / 60s | 提交端点，来源维度 |
| `RATE_LIMIT_SUBMIT_USER_MAX` / `..._WINDOW` | 20 / 60s | 提交端点，用户维度 |
| `RATE_LIMIT_AUTH_IP_MAX` / `..._WINDOW` | 10 / 60s | 登录、注册、改密、找回 |

实现是**滑动窗口日志**而非固定窗口：固定窗口在边界处允许 `2 × limit` 挤在极短的
时间跨度内（窗口末尾打满 + 新窗口开头再打满），而那恰恰是最容易被脚本利用的形态。

限流状态**不落数据库**——这是"单进程部署"能成立的前提之一，也让限流判定零写入开销。

## 三个会静默失效的坑

### 1. 转发头信任边界

**只信任受信代理写入的转发头。** 不设这条边界，客户端自己发一个
`X-Forwarded-For` 就能把限流键算到伪造地址上，应用层限流形同虚设。

```ini
TRUSTED_PROXY_IPS=["127.0.0.1", "::1"]
```

```bash
uvicorn app.main:app --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

取值时用转发头里**最右侧**那一项：nginx 用 `$proxy_add_x_forwarded_for` 会把直连
它的对端地址追加到末尾，因此最右项才是 nginx 实际看到的地址；左边全部是客户端
自己塞的。**取最左项是这里最常见的写错方式**——那等于让攻击者决定自己的限流键。

### 2. nginx 前面的 CDN

有 CDN 时 `$binary_remote_addr` 拿到的是 CDN 的地址，限流要么形同虚设，要么把整个
CDN 一起封掉。需要 `real_ip` 模块还原（配置样例见
`deploy/nginx/cea-event-hub.conf` 顶部注释）。

### 3. 多进程会让应用层限流按进程数放宽

应用层限流是**进程内**内存实现。`--workers N` 时每个进程各持一份计数，实际阈值
被放大到 N 倍。

本项目以 **`--workers 1`** 运行，因此语义完全正确。这不是保守估计：

- SQLite 的写入本来就串行化在单写者锁上，多进程只会增加锁争用而不提升写入吞吐
- nginx 那一层已经覆盖了 IP 维度

**如果将来改为多进程**，应用层阈值会被按进程数放宽。届时的选择：

1. 把 `RATE_LIMIT_*_MAX` 按进程数下调（粗糙但立刻可用）
2. 把限流职责整体交给 nginx（最省事，但失去用户维度）
3. 实现一个 Redis 版的 `RateLimiter` 端口实现（见 `app/core/ports.py`）

## 错误码

| 场景 | 状态 | `code` | 客户端该做什么 |
|---|---|---|---|
| 触发限流 | `429` | `rate_limited` | 提示稍后重试（**唯一值得重试的**） |
| 名额已满 | `409` | `quota_exhausted` | 渲染"名额已满"，停止 |
| 活动已关闭 | `403` | `event_closed` | 渲染"已截止"，停止 |
| 需要登录 | `401` | `login_required` | 引导登录 |

`429` 与 `409` 的区别是刻意的：前者是"稍后重试"，后者是终态。用 `429` 表示名额
已满会诱导客户端重试一个永远不会成功的请求。

`429` 响应携带 `Retry-After`，值由窗口剩余时间算出。

## 清理

限流键由 janitor 定时清扫。**清扫必须尊重窗口**——把还在窗口内的键清掉，限流就
被"清理"绕过了。
