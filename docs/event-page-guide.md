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
| `CEA.storage.save/load/remove/clear` | 本地储存（见下方"本地储存"） |

> 早期版本的 `CEA.resize()` **已移除**。它属于"iframe 按内容高度撑开"的旧版式，
> 而活动页现在是全屏的（见下方"版面"）。

### `CEA.submit` 的参数

```js
await CEA.submit({
  payload: { /* 任意 JSON 对象 */ },
  files: [File, File],        // 可选
  kind: 'signup',             // 可选分类标签，管理端据此分组
  idempotencyKey: intentKey,  // 强烈建议带上，见下方"防重复提交"
  onProgress: (loaded, total) => {},    // 可选，真实上传进度
});
```

**带不带文件都是同一次调用**：SDK 会自动选择合适的端点。作者不需要知道后端有两个
端点，也不需要为"既有字段又有文件"分两次提交（那会让管理端看到两条记录）。

**`onProgress` 是真实进度。** 请求由宿主发出，所以它能拿到上传进度并回推给活动页；
`total` 在服务端未提供总长时为 `null`，此时只能显示"已上传 N 字节"。

### 防重复提交（活动页的责任）

**平台不会按内容替你判重。** 两次内容相同的提交就是两条记录 —— 因为按内容判重不看
请求身份，一旦误判就会把你的提交连同附件一起**静默丢弃**，而用户看到的是"提交成功"。
多一条记录是可见且可恢复的，静默丢弃不是。

所以防重复要你自己做，两件事配合：

**1. 提交期间禁用按钮** —— 挡住连点。这是最直接的一道防线：

```js
submitButton.disabled = true;
try { await CEA.submit({...}); } finally { submitButton.disabled = false; }
```

**2. 按"意图"复用 `idempotencyKey`** —— 挡住网络重传。

关键在键的**生命周期**：它标识的是**一次提交意图**，不是一次点击。所以要在用户
**开始填写**时生成一次，成功后重新生成，而不是每次点击都新生成一个：

```js
let intentKey = crypto.randomUUID();   // 一次填写 = 一个意图

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  submitButton.disabled = true;
  try {
    const result = await CEA.submit({ payload, idempotencyKey: intentKey });
    if (result.deduplicated) {
      show('这次提交此前已经收到过了。');   // 重传，不是新提交
    } else {
      show('提交成功。');
    }
    intentKey = crypto.randomUUID();   // 下一次填写是新的意图
  } finally {
    submitButton.disabled = false;
  }
});
```

反过来说：**每次点击都生成新键，幂等键就挡不住连点** —— 每次点击都是一个"新意图"，
服务端无从判断它们其实是同一次。那种写法只能靠第 1 条兜住。

## 版面：活动页自己负责滚动

活动页**占满整个视口**，宿主不在上面叠任何自己的界面（没有顶部栏、没有标题栏）。
因此：

- **页面自己滚动** —— 正常写你的 HTML 即可，内容超长时是页面内部滚动
- 想改浏览器标签标题用 `CEA.setTitle()`，活动标题默认已经写进去了

## 本地储存

活动页处在**不透明源**，`localStorage` 与 `sessionStorage` 都会直接抛异常。所以
想存点本地数据只能请宿主代存：

```js
await CEA.storage.save('signup', { name: '张三', grade: '2' });
const saved = await CEA.storage.load('signup');   // -> { name: '张三', ... } 或 null
await CEA.storage.remove('signup');               // 删掉这一条
await CEA.storage.clear();                        // 清空**本活动**的全部本地数据
```

数据存在**宿主自己的 localStorage** 里，**不落后端**。

### key 的所有权在宿主

你传的 `key` 只是**本活动内**的一段命名空间。真正写进 localStorage 的键由宿主拼成：

```
cea.storage:{活动标识}:{你的 key}
             ^^^^^^^^^^ 来自宿主自身路由，你无法伪造
```

所以**你无法指到别的活动的数据，也碰不到宿主自己的键**。这是结构性的，不是靠约定。

`key` 只能是 1–64 位小写字母、数字、`-`、`_`，且以字母或数字开头。**不合规会抛
`storage_key_invalid`，不会回落** —— 回落会让两个本来不同的槽位撞在一起互相覆盖，
那是静默的数据损坏。

### 容量上限

**本活动所有本地数据加起来不能超过 4096 字符**（按 JSON 序列化后计算）。超出时抛
`storage_too_large`，宿主**不截断** —— 截断会让你读回一份与写下去的不一样的数据。

这个上限是按活动封的，因为 localStorage 是**同源共享**的资源：一个活动页把它写爆，
同源下所有活动页和管理台都会一起失败。

典型用法（表单草稿）远低于这个量级；真要存更多东西，应该走后端。

```js
// 草稿存不下不该挡住提交，所以吞掉错误是合理的
CEA.storage.save('signup', snapshot).catch(() => {});
```

## 能力边界：这些事做不到

沙箱 iframe 处于**不透明源**，因此：

| 做不到 | 原因 | 替代 |
|---|---|---|
| `localStorage` / `sessionStorage` | 不透明源没有存储 | `CEA.storage.*`（宿主按活动代存） |
| `document.cookie` | 同上 | 不需要，活动页本就不持有凭据 |
| `history.pushState` | 抛 SecurityError | 页内多步流程用 hash（`#step2`），或 `CEA.navigate()` |
| `fetch('/api/v1/...')` | **被浏览器拦死** | 一切数据经 `CEA.*` |
| `fetch('./data.json')` | 对同主机也算跨源 | 需要 CORS 头；`/content/**` 已配 `ACAO: *`，所以**这个可以** |
| 访问 `parent.document` | 沙箱隔离 | 用 `CEA.*` 请求宿主代办 |
| `alert` 默认被拦 | 沙箱 | `CEA.toast()`（主题统一） |

`fetch('./data.json')` 之所以可行：`/content/**` 刻意返回
`Access-Control-Allow-Origin: *`，而 `/api/**` 不返回任何 CORS 头 —— 这个不对称正是
"活动页能取自己的数据，但绕不过宿主调后端"的机制。

## 让管理端那一列更好读：`payload.$display`

提交内容不做字段级契约，管理端默认把整个 payload 当 JSON 显示。数据一多，那一列
就是一片挤在一起的键值对，管理员得逐个字段找。

在 payload 里放一个 **`$display`** 字符串，管理端列表就会显示它而不是 JSON：

```js
await CEA.submit({
  payload: {
    $display: '张三 · 2 年级 · 13800000001',   // 管理端列表里显示这一行
    name: '张三',
    grade: '2',
    contact: '13800000001',
    intro: '一段很长的自我介绍……',
  },
});
```

规则：

| 情形 | 管理端显示 |
|---|---|
| `$display` 是非空字符串 | 它 |
| 缺失、空串、纯空白 | 完整 JSON |
| 不是字符串（对象、数字…） | 完整 JSON |

**它只影响列表里的摘要那一行。** 点击整行弹出的详情对话框里仍然给出完整 JSON ——
摘要不该把原始数据挡在后面，管理员点开详情往往正是为了看摘要没覆盖到的字段。

用 `$` 前缀是刻意的：活动自己的字段很难叫这个名字，不会撞。

> **安全提示**：`$display` 会被管理端当**纯文本**渲染，不会当 HTML。所以它不能用来
> 塞标记或样式 —— 那既不会生效，也没有意义。同样地，别把秘密放进去：它和其他字段
> 一样存在提交记录里。

## 不要在这个文件里放秘密

`/content/**` 是**公开只读**的。任何能访问活动页的人都能读到页面里的全部内容，
包括注释与内联脚本。

不要把 API 密钥、内部链接、口令写进活动页。需要保密的数据一律放在后端，经
`CEA.*` 按身份取回。

## 设计令牌

**活动页的视觉语言由活动自己决定。** 平台不要求活动页长得像宿主 —— 你可以用圆角、
渐变、自己的字体，怎么合适怎么来。这一节只是把宿主**提供**的材料列清楚，想用就用，
不用也不影响任何功能。

宿主在握手时（`CEA.ready` 之后、`hub:init` 的 `theme` 字段）下发这些 CSS 变量：

| 令牌 | 取值 | 用途 |
|---|---|---|
| `--bg` | `#0b0b0d` | 页面底色 |
| `--panel` | `#101014` | 面板、卡片表面 |
| `--bone` | `#cfcac4` | 主文字 |
| `--edge` | `#b0aba5` | 卡片描边 |
| `--mute` | `#8b8e97` | 次级文字 |
| `--dim` | `#5f626b` | 占位与禁用 |
| `--red` | `#d0202f` | 主强调 |
| `--red-hi` | `#ff4a55` | 亮强调（悬停、链接） |
| `--line` | `rgba(255,255,255,.09)` | 分隔线 |
| `--shadow-ink` | `#26262e` | 硬阴影的实色 |
| `--mono` | 等宽字体栈 | 标题、标签、数字 |
| `--sans` | 无衬线字体栈 | 正文 |
| `--radius-control` | `0` | 按钮圆角（宿主当前一律直角） |
| `--radius-surface` | `0` | 输入框、卡片圆角（同上） |

最后两行现在都是 `0`：平台这一版走直角路线。列出来是因为宿主**确实**会下发它们 ——
哪天想跟宿主保持一致，读令牌比写死更稳。

想跟宿主的暗色基调对齐，最省事的做法是只取前几个颜色：

```css
:root {
  --bg:#0b0b0d; --panel:#101014; --bone:#cfcac4;
  --mute:#8b8e97; --dim:#5f626b; --red:#d0202f; --red-hi:#ff4a55;
  --line:rgba(255,255,255,.09);
}
body { background: var(--bg); color: var(--bone); }
```

令牌**只是初始值**，不是限制：活动页可以整套不用，也可以只借底色。写死颜色同样可以
—— 代价只是宿主将来调整配色时，这一页不会跟着变。

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
