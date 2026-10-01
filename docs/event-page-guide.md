# 活动页接入指南

给活动页作者的说明。完整示例见 `docs/examples/sample-event-page.html`，可直接打包投放。

## 两步接入

```html
<!-- 1. 不需要引入 SDK —— 平台会在返回你的 HTML 时自动插入这一行：
     <script src="/sdk/v1/cea.js" id="cea-sdk"></script> -->

<script>
  // 2. 等宿主握手完成，拿到身份描述符
  const identity = await CEA.ready;
  console.log(identity.loggedIn, identity.displayName);

  // 3. 提交
  await CEA.submit({ payload: { name: '张三' }, kind: 'signup' });
</script>
```

**SDK 是自动注入的。** 托管层返回 HTML 前检查页面里有没有 `id="cea-sdk"` 的
`<script>`：**没有就注入，有就原样返回**。所以"忘了引入 SDK"不是可能犯的错误。

### 如果你想自己写那一行

完全可以，但 **`id="cea-sdk"` 不能省**：

```html
<script src="/sdk/v1/cea.js" id="cea-sdk"></script>   <!-- 正确 -->
<script src="/sdk/v1/cea.js"></script>                <!-- 错误：会被判定为"没有引用" -->
```

**判断只看 `id`，不看 `src`。** 理由是路径会变（`/sdk/v1/` → `/sdk/v2/`），按 `src`
判断的话 SDK 一换路径，所有老页面都会被判成"没有引用"。

上面第二种写法**不会被特殊照顾**（平台不做迁移），结果是平台再插一个标签，**SDK
加载两次**。目前这不影响功能，但没必要。

### 注入标签的识别

注入的那一行带 `id="cea-sdk"`，你可以据此判断 SDK 是否就位：

```js
const sdkReady = document.getElementById('cea-sdk') !== null
```

> 需要关掉注入时设 `CONTENT_SDK_INJECT=false`，即回到"必须自己写"的行为。

## 可用方法

| 方法 | 说明 |
|---|---|
| `await CEA.ready` | 握手完成，返回身份描述符；10 秒未握手则 reject |
| `CEA.identity()` | 同步读取当前身份（未就绪时为 `null`） |
| `await CEA.event()` | 当前活动的公开信息（标题、配额、是否需登录、开放/截止时间） |
| `await CEA.submit({...})` | 提交信息与文件 |
| `await CEA.me()` | 当前身份描述符（走一次 RPC） |
| `await CEA.mySubmissions()` | 自己的提交历史（**未登录会失败**） |
| `CEA.toast(message, level?)` | 请求宿主弹提示 |
| `CEA.navigate(path)` | 请求宿主导航（只接受站内路径） |
| `CEA.setTitle(title)` | 更新**浏览器标签标题** |
| `CEA.resize()` | 空操作，保留兼容（见下方"版面"） |
| `CEA.draft.save/load/clear(value?, formKey?)` | 草稿存取 |

### `CEA.submit` 的参数

```js
await CEA.submit({
  payload: { /* 任意 JSON 对象 */ },
  files: [File, File],        // 可选
  kind: 'signup',             // 可选分类标签，管理端据此分组
  idempotencyKey: crypto.randomUUID(),  // 强烈建议带上
  onProgress: (loaded, total) => {},    // 可选，真实上传进度
});
```

**带不带文件都是同一次调用**：SDK 会自动选择合适的端点。作者不需要知道后端有两个
端点，也不需要为"既有字段又有文件"分两次提交（那会让管理端看到两条记录）。

**建议始终带 `idempotencyKey`。** 网络抖动或用户连点导致重试时，服务端会返回原提交
而不是新建一条，返回值的 `deduplicated` 为 `true`。

**`onProgress` 是真实进度。** 请求由宿主发出，所以它能拿到上传进度并回推给活动页；
`total` 在服务端未提供总长时为 `null`，此时只能显示"已上传 N 字节"。

## 版面：活动页自己负责滚动

活动页**占满整个视口**，宿主不在上面叠任何自己的界面（没有顶部栏、没有标题栏）。
因此：

- **页面自己滚动** —— 正常写你的 HTML 即可，内容超长时是页面内部滚动
- **不要依赖 `CEA.resize()`** —— 它现在什么都不做。它的存在是为了兼容早期调用；
  在"iframe 按内容高度撑开"的旧版式下才有意义，而那种版式会让长页面产生双层滚动条
- 想改浏览器标签标题用 `CEA.setTitle()`，活动标题默认已经写进去了

## 能力边界：这些事做不到

沙箱 iframe 处于**不透明源**，因此：

| 做不到 | 原因 | 替代 |
|---|---|---|
| `localStorage` / `sessionStorage` | 不透明源没有存储 | `CEA.draft.*`（宿主按活动代存） |
| `document.cookie` | 同上 | 不需要，活动页本就不持有凭据 |
| `history.pushState` | 抛 SecurityError | 页内多步流程用 hash（`#step2`），或 `CEA.navigate()` |
| `fetch('/api/v1/...')` | **被浏览器拦死** | 一切数据经 `CEA.*` |
| `fetch('./data.json')` | 对同主机也算跨源 | 需要 CORS 头；`/content/**` 已配 `ACAO: *`，所以**这个可以** |
| 访问 `parent.document` | 沙箱隔离 | 用 `CEA.*` 请求宿主代办 |
| `alert` 默认被拦 | 沙箱 | `CEA.toast()`（主题统一） |

`fetch('./data.json')` 之所以可行：`/content/**` 刻意返回
`Access-Control-Allow-Origin: *`，而 `/api/**` 不返回任何 CORS 头 —— 这个不对称正是
"活动页能取自己的数据，但绕不过宿主调后端"的机制。

## 不要在这个文件里放秘密

`/content/**` 是**公开只读**的。任何能访问活动页的人都能读到页面里的全部内容，
包括注释与内联脚本。

不要把 API 密钥、内部链接、口令写进活动页。需要保密的数据一律放在后端，经
`CEA.*` 按身份取回。

## 设计令牌

宿主在 `CEA.ready` 之前会下发一套 CSS 变量（`hub:init` 的 `theme` 字段）。活动页
可以直接用，观感就与宿主一致：

```css
:root {
  --bg:#0b0b0d; --panel:#101014; --bone:#cfcac4;
  --mute:#8b8e97; --dim:#5f626b; --red:#d0202f; --red-hi:#ff4a55;
  --line:rgba(255,255,255,.09);
}
body { background: var(--bg); color: var(--bone); }
```

约定：**无渐变**、圆角只有 7px 与 10px 两档、过渡不超过 200ms。

## 错误处理

活动页**不需要解析 HTTP 状态码**，只需判断 `error.code`：

```js
try {
  await CEA.submit({ payload });
} catch (error) {
  switch (error.code) {
    case 'login_required':   CEA.navigate('/login'); break;
    case 'quota_exhausted':  showFull();             break;  // 终态，停止
    case 'event_closed':     showClosed();           break;  // 终态，停止
    case 'rate_limited':     showRetryLater();       break;  // 唯一值得重试的
    case 'validation_failed': markFields(error.fields); break;
    default:                 showGeneric(error.message);
  }
}
```

完整错误码表见 `docs/bridge-protocol.md`。**`quota_exhausted` 与 `rate_limited` 必须
区别对待**：前者是终态，后者可以重试。

## 打包与投放

1. 把活动页与它的资源（CSS、图片、数据文件）放进一个目录，入口文件命名为
   `index.html`
2. 打成 **zip**（不要包含外层目录，条目直接是 `index.html`、`assets/...`）
3. 管理台 → 活动详情 → 网页内容 → 上传

投放是**整体替换**：新包里没有的文件会消失。投放成功后内容版本递增，活动页会立刻
加载新版本。

zip 中**不能包含符号链接条目**，也不能包含 `..` 或绝对路径 —— 校验不通过时整个
投放被拒绝，且线上内容完全不受影响。

## 本地调试

```bash
# 后端
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload
# 前端
cd frontend && npm run dev
```

把活动页放进 `content/{event_id}/index.html`，然后访问
`http://localhost:5173/{event_id}`。

Vite 会把 `/api` 与 `/content` 代理到后端，因此开发环境与生产**同源** —— 这一点
很重要，跨源开发会掩盖真实的沙箱与 CORS 行为。
