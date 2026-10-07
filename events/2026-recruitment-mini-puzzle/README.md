# 2026 招新 · 迷你解谜（活动页）

一个伪装成 Linux 终端的活动页：玩家以 `bobby@ellia` 进入，探索用户目录、找到口令、
`su` 切换到 `self@ellia`，通过后留下邮箱作为联系方式。**只对已登录成员开放。**

> 本文件是给维护者看的，**不在投放包里**。答案就写在下面，所以别把它一起压缩上传。

## 玩法与答案

```
bobby@ellia:~$ ls                      documents/  pictures/
bobby@ellia:~$ ls pictures             （空 —— 什么都没有）
bobby@ellia:~$ cat documents/password  username: self / password: facb…
bobby@ellia:~$ su self                 Password: ********
已切换为 self@ellia
恭喜通过 2026 招新的迷你解谜，填写邮箱以便后续联系。
email> someone@example.com             → 提交成功，回到 shell
self@ellia:~$ ls documents             data
self@ellia:~$ cat documents/data       意外？抛弃？许多可能……
self@ellia:~$ ls pictures              illusion.png
```

**每个用户一份家目录**，所以"换成 self 之后才看得到"的东西才有分量：

| | documents/ | pictures/ |
|---|---|---|
| `bobby` | `文本文档.txt`、`password` | **空** |
| `self` | `data` | `illusion.png` |

口令文件是 **bobby 的**（谜题就是在他的目录里找到它）；图片与那句独白是 **self 的**。
两边都不含解题线索 —— 答案只有那份口令，`download` 因此不在解题关键路径上。

> **邮箱提示只接邮箱**：那一段唯一要做的事就是留下联系方式，所以敲命令会得到
> "邮箱格式看起来不对"。提交成功后会回到 self 的 shell，那时命令恢复 —— self 家目录里
> 的东西是**那之后**看的。界面上不再为此额外写一行提示，玩家看到的是提示符从 `email>`
> 变回 `self@ellia:~$`。走查里两条断言分别钉住这两半。

## 目录里各是什么

| 路径 | 是什么 | 进投放包吗 |
|---|---|---|
| `index.html` | **源码**，可读。调试台直接加载它 | 不 —— 进包的是构建产物 |
| `documents/` | **内容真相**（`password`、`文本文档.txt`、`data` 都在这儿） | 不 —— 见下 |
| `pictures/illusion.png` | `download` 的目标 | 是 |
| `tools/` | 构建与走查脚本 | 不 |
| `tests/` | 单测与产物测试 | 不 |
| `dist/` | 构建产物（不入库） | **上传 `dist/2026-recruitment-mini-puzzle.zip`** |

**为什么 `documents/` 不进包**：`ls` 会把 `documents/password` 这个路径告诉玩家，而
`/content/**` 是公开只读的 —— 只要文件真在包里，手打一次 URL 就绕过整个谜题。所以它的
内容在构建时被编码内联进产物，运行时才还原成终端里的"文件"。

**往里加文件时**：构建与产物测试都是**遍历整个 `documents/`** 取明文的（不是写死几个
文件名），所以新增文件会自动进入"不许出现在产物里"那条断言 —— 这正是它该守住的地方。

## 常用命令

```bash
cd events/2026-recruitment-mini-puzzle
npm install          # 只装 javascript-obfuscator（构建用）
npm test             # 单测 + 产物测试（会顺带构建一次）
npm run build        # 产出 dist/ 与投放 zip
npm run smoke        # 真浏览器走查：源码与产物各玩一遍
```

`npm run smoke` 需要后端与前端开发服务器都在跑（见 `docs/dev-harness.md`），
浏览器默认找本机 Chrome，可用 `CHROME_PATH` 指定。

**改动之后至少跑一遍 `npm test && npm run smoke`。** 单测只看得见纯逻辑，产物测试只看
静态内容 —— "混淆之后的代码在浏览器里跑不跑得起来"只有走查能回答。

## 投放

1. `npm run build`
2. 把 `dist/2026-recruitment-mini-puzzle.zip` 上传到管理台 → 活动详情 → 网页内容
   （zip 里只有 `index.html` 与 `pictures/illusion.png` 两个条目，都在根下）
3. 活动配置：**必须勾选「需登录」**，入口 `index.html`，建议 `max_per_submitter = 1`

第 3 步不是可选项：页面里的"拒绝访问"只是体验层（不让人填完邮箱才发现交不上去），
**真正的门在活动配置上** —— 未登录时宿主会在发出请求之前就把提交短路成 `login_required`，
后端也会拒绝。不配的话，懂技术的玩家可以自己往宿主发一条提交消息。

## 这一页刻意不做什么

- **不做 `pwd` / `mkdir` / `rm` / Tab 补全**：文件系统只有两层的四个条目，加上去只是
  增加表面积。可用的七条命令就是 `ls` / `cd` / `cat` / `echo` / `su` / `download` / `help`。
  这些在单测里都是断言（`help` 必须逐条列出、未提供的命令必须报 `command not found`）。
- **不做服务端谜题校验**：平台没有这种接口，能记录下来的只有邮箱这一条提交。
- **不做邮箱真实性校验**：只做格式检查。
- **不改沙箱令牌集**：见下。

## 路径与 `cd` 的规则

用户目录就是这个世界（提示符里的 `~`）。脚本外的东西一律够不着：

| 写法 | 结果 |
|---|---|
| `cd documents`、`cat password`（在 documents 里） | 按当前目录解析，正常 |
| `cd ..`（在子目录里） | 回到上一层 |
| `cd ..`（已经站在用户目录上） | `cd: ..: Permission denied` —— 那是世界之外 |
| `/etc/passwd`、`cd /etc` | `Permission denied`（没有 `/` 这个东西） |
| `cd nope`、`cd documents/password` | `No such file or directory` / `Not a directory` |

越界判定是**边走边夹**：解析路径时一遇到"已经在用户目录之上还要往上"就立刻拒绝，
而不是"先规范化再比较"。规范化正是这类越界绕过的经典来源。

`su` 成功后当前目录回到 `~` —— 真 `su` 起的是新会话，落点在该用户的家目录。

## 两个必须知道的边界

**`download` 不是真下载。** 沙箱 iframe 没有 `allow-downloads` 令牌，浏览器会直接拒绝
（控制台原文：`Download is disallowed. The frame initiating or instantiating the download
is sandboxed…`）。所以 `download` 的行为是**在本页查看器里打开**，并额外给一个
「在新标签页打开」（`allow-popups` 是给了令牌的）。`download` 不在解题关键路径上，
所以这个降级不影响谜题完整性。

**查看器里的图片按容器缩放（contain），不出滚动条。** 这一点靠两处配合：看图时给盒子
一个**确定的高度**（`.viewer__box--image`），图片的 `max-height:100%` 才有意义 ——
只写百分比而盒子高度又"由内容决定"的话，百分比会解析成 `none`，图照样溢出。
走查里有一条断言直接量 `scrollHeight <= clientHeight` 与图是否落在容器内。

**滚动条是自己画的**（10px、透明轨道、`--dim` 滑块）。Chromium 里只要出现
`::-webkit-scrollbar` 规则，标准的 `scrollbar-width`/`scrollbar-color` 就会被忽略，
所以两套写法都留着且取值一致。注意：**滚动条的绘制在 headless 截图里看不到**（连浏览器
默认那条也不画，已实测），所以别拿截图为它背书 —— 走查量的是 gutter 宽度（默认 15px，
这一页 10px）。

**混淆不是安全边界。** `cat documents/password` 必须把明文打印出来，所以口令在运行时
一定存在于页面内存里；构建期那几条断言只保证**静态文件里**没有明文。这一层挡的是
"直接读源码抄答案"，挡不住有心反混淆的人。

## 改这一页时容易踩的坑

- **`var FS_KEY =` 这个锚点在源码里只能出现一次。** 注释里再抄一遍原文，构建的字符串
  替换就会命中注释那一处，密钥注不进去 —— 页面报"内容读取失败：JSON 解析出错"，而产物
  每一项看起来都对。构建里有断言拦着，单测里也有一条。
- **core 与 app 拼成一段时必须带分号。** 在源码里它们是两个 `<script>`，少个分号没事；
  拼成一段后 `})()` 紧跟 `(function(){…})()` 会被 ASI 当成一次调用。
- **合成的那一束必须插在 SDK 之后**：启动时要读 `window.CEA`。
- **不要按 `src` 判断 SDK 是否已引入**，只看 `id="cea-sdk"`（路径会从 `/sdk/v1/` 走到
  `/sdk/v2/`）。这一页显式写了那一行，所以也不依赖 `CONTENT_SDK_INJECT` 开关。
