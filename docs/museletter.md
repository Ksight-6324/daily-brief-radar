# Museletter 轻量模式

`BRIEF_MODE=museletter` 是默认模式。内容采集、AI 摘要、板块编排继续由本仓库负责，订阅者、双重确认、退订、退信与投诉抑制、发送账本交给 Museletter 1.5.0（版本固定在 `requirements-museletter.txt`）。原付费订阅系统（`BRIEF_MODE=subscription`，含个人主页、支付宝、删除账号）代码保留，不在轻量模式中运行。

## 分工

| 事项 | 负责方 |
| --- | --- |
| 订阅者存储、双重确认、退订链接、退信与投诉抑制、发送与投递记录 | Museletter（自己的 SQLite 文件） |
| 采集、摘要、板块与卡片渲染、早晚报冻结、重试时沿用冻结内容 | 本仓库 `newsletter.py` |
| 发件服务 | Amazon SES 或 Cloudflare Email Service，由 Museletter 调用 |

本地发布库 `BRIEF_PUBLISH_DB_PATH` 只保存期号、冻结正文、目标列表或标签、campaign ID，不含任何订阅者信息。

## 与付费模式的差别

- 所有人收到同一份内容，按列表或标签分组，不再按个人偏好排序。
- 没有个人主页、内容回看、删除账号页和支付。订阅入口使用引擎的 `/subscribe`，退订使用引擎的链接。要做按主题分发，为每个主题建标签，并用 `MUSELETTER_TAG` 分别发布。
- 付费、试读次数不由引擎管理，需要时在导入订阅者时打标签区分。

## 本地启动

1. `pip install -r requirements-museletter.txt`，复制 `.env.example` 为 `.env`，填写 `MUSELETTER_*` 与 AWS 或 Cloudflare 凭据、`MUSELETTER_API_KEY`、`MUSELETTER_FROM_EMAIL`、`MUSELETTER_POSTAL_ADDRESS`。
2. 启动引擎：`museletter serve --env-file .env`，用 `museletter doctor` 检查 DNS 与服务商状态。
3. 导入测试订阅者：`museletter subs add 你的邮箱`。
4. 先预览：`python -m newsletter generate` 之后 `python -m newsletter publish --draft`，在引擎里检查草稿并手工发一次测试邮件。
5. 常驻调度：`python -m newsletter schedule --watch`。07:30、19:30 提前生成并冻结，08:00、20:00 之后发布。也可以用 `docker compose -f compose.museletter.yml up -d --build` 同时启动引擎与调度器。

引擎默认要求发送前有一次测试发送。确认流程稳定后，才用 `MUSELETTER_SKIP_TEST=true` 开启全自动发送。

## 公网与合规边界

- 管理 API 只在本机或私网使用，Compose 把端口绑定在 `127.0.0.1`。
- 读者点击的 `/confirm`、`/unsubscribe` 必须能从公网访问，所以公开推广前要把引擎放到有稳定 HTTPS 域名的服务器上，`MUSELETTER_BASE_URL` 填该地址。仅本机运行时，只能手动导入订阅者，邮件里的确认和退订链接指向本机，读者点不开。
- 上游投递是至少一次语义，供应商已接受但账本未提交时崩溃，极端情况下可能重复发送一封。
- 本仓库的隐私政策、服务条款页面只在付费模式的网站中提供。轻量模式公开前需要另外提供。
- 邮件正文由 AI 辅助生成，请以每条的原始来源为准。

## 测试

`python -m unittest discover -s tests` 包含对真实 Museletter 应用的集成测试，使用假的邮件服务商，不会真的发信。真实 SES 或 Cloudflare 发信、真实邮箱到达需要凭据后另行验收。
