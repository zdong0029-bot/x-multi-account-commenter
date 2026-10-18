# AGENTS.md — 给 Codex / 代码助手的说明

## 项目
X 多账号评论助手。Windows 本机运行：`app.py` 是 127.0.0.1 上的面板与调度服务，`automation.py` 是每账号规则、筛选、限频和调度，`extension/` 是 Chrome/Edge MV3 扩展，在用户自己已登录 X 的浏览器配置里执行浏览、读主页、发评论。`index.html` 是面板。数据在 `data/`（SQLite、每日监控 CSV `data/reports/`），不要提交。

## 运行
- 启动：双击 `Start.cmd`，或 `python app.py --port 18865 --no-open`（Python 3.10+，仅标准库；旧版专用浏览器模式需 Playwright）。
- 测试：`python -m unittest discover tests`，改动后必须全部通过。
- 扩展改动后需在 chrome://extensions 重新加载并重新配对。

## 不可破坏的账号保护约束
1. 频率硬上限：`MIN_INTERVAL=600`，每账号每小时 ≤4、每天 ≤20（`automation.py` 顶部常量）。不要放宽，不要加“0 = 不限”。
2. 同一博主 7 天内只被所有账号中的一个评论一次；同一帖子全局只评论一次。
3. 检测到 X 风控提示（`content.js` 的 `xWarning`，错误前缀 `X_WARNING`）时暂停全部账号。
4. 发送结果不明时标记 uncertain 并暂停，绝不自动重发。
5. 只做浏览、核对、评论；不加点赞、关注、转发、私信。
6. 不加反检测功能：浏览器指纹伪装、代理轮换、伪造人类操作节奏、绕过验证码等一律不做。
7. 不读取密码、不导出 Cookie；AI 密钥只存内存。
8. 本机服务只监听 127.0.0.1，保留 Host/Origin/令牌校验和扩展配对校验。

## 约定
- 界面和日志用中文，时间用北京时间（`automation.TZ`）。
- 博主筛选只依据主页简介/公开分类，不依据帖子内容推断。
- 新的规则字段要同时加到 `DEFAULT_RULE`、`validate_rule`、`migrate_rule`（如需）和 `index.html` 的 `automationMarkup`（`data-rule="字段名"`）。
- 每个筛选/发布结果都要写入监控 CSV（`AutomationManager.report`）。
