# 订阅版运行与验收

## 当前实现

订阅版沿用 Python 采集与摘要，使用 FastAPI、原生网页、SQLite、支付宝当面付和 Resend。数据与发送任务保存在共享持久数据库，生成器按日期及早／晚版次冻结有可用来源的主题内容。

网页提供全部 15 个主题，支持零至全部选择、全选与清空，偏好影响排序。每天北京时间 08:00 发布早报、20:00 发布晚报，使用 Asia/Shanghai。免费试读三封，每封成功提交给服务商后计一次，宽限三天。周付 3 元对应 7 天，月付 9.9 元对应 30 天，季付 29 元对应 90 天，年付 99 元对应 365 天，按 UTC 时长计算。

## 本地准备

在仓库根目录执行，建议使用 Python 3.11 以上版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

编辑 `.env`。CLI、网页服务和 `run_all.py` 会读取当前工作目录下的文件，进程环境变量优先。

| 变量 | 配置内容 |
| --- | --- |
| `BRIEF_MODE` | `subscription` |
| `BRIEF_DB_PATH` | 本地可用 `var/brief.db`，线上使用持久磁盘绝对路径 |
| `PUBLIC_BASE_URL` | 本地可用 `http://127.0.0.1:8000`，线上填写公开 HTTPS 域名 |
| `BRIEF_TOKEN_SECRET` | 稳定随机值，至少 32 字符。留空时自动生成数据库旁的 `.db.key` |
| `RESEND_API_KEY` | Resend API 密钥 |
| `RESEND_FROM` | 已验证域名下的发件地址，例如 `Brief <brief@example.com>` |
| `DEEPSEEK_API_KEY` | 现有摘要程序使用的 API 密钥，也支持已有的 `LLM_*` 配置 |
| `ALIPAY_APP_ID` | 已开通当面付应用的 app ID |
| `ALIPAY_SELLER_ID` | 实际收款商户 seller ID |
| `ALIPAY_PRIVATE_KEY` | 应用 RSA 私钥 PEM，换行可以写为字面量 `\n` |
| `ALIPAY_PUBLIC_KEY` | 支付宝 RSA 公钥 PEM |
| `ALIPAY_SANDBOX` | `1` 使用沙箱网关，`0` 使用生产网关 |

确认 `.env`、数据库和密钥被 Git 忽略。SQLite、API、worker、scheduler 必须共用同一磁盘上的同一数据库文件。备份使用 SQLite 备份接口或停机后的一致性副本，同时备份 `.db.key`。恢复时保留相同令牌密钥。

## 启动三个进程

先初始化数据库。

```powershell
python -m subscription.cli init
```

已有 v1、v2 数据库会在启动时自动事务升级到 v3（v3 新增 `login_link` 邮件类型，重建发送表并保留全部记录）。升级前停止旧版进程并备份数据库及令牌密钥，再启动新版 API、worker 和 scheduler。升级保留订单、有效期、偏好及历史快照，将旧快照标记为 legacy。旧日报的 pending／failed 任务转 skipped，sending 转 needs_review，需核对服务商记录。新期号分别使用 `YYYY-MM-DD/morning` 和 `YYYY-MM-DD/evening`。

三个终端均在仓库根目录、相同虚拟环境运行。

```powershell
# 终端一，网页与 API
python -m uvicorn web.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log

# 终端二，发送 worker
python -m subscription.cli worker --watch

# 终端三，每分钟扫描，恢复当前早／晚报窗口的任务
python -m subscription.cli schedule --watch
```

打开 `http://127.0.0.1:8000`。正式上线时由反向代理提供 HTTPS，并通过进程管理器守护上述进程。one-click 退订请求的查询参数含管理凭证，代理访问日志应隐藏查询参数。

调度器在北京时间 08:00 至 20:00 前检查当天早报，在 20:00 至次日零点前检查当天晚报。缺少时调用现有 `run_all.py` 订阅入口，采集各主题候选并生成快照，已有快照直接扫描入队。失败五分钟后重试，在当前窗口恢复时补扫。跨入晚报窗口后生成晚报，早报已经入队的任务仍按原期号重试。生成和队列处理耗时会影响最终到达邮箱的时间。

## 人工执行命令

```powershell
# 申请确认邮件，需由收件人完成确认
python -m subscription.cli register you@example.com --preferences markets,ai

# 按北京时间当前窗口采集、生成、冻结和入队
python run_all.py

# 已有 daily_brief.md 时按其 SLOT 手动冻结与入队
python -m subscription.cli --edition morning publish
# 晚报使用 --edition evening

# 扫描已有当天快照及续费提醒
python -m subscription.cli scan

# 尝试处理一个发送任务
python -m subscription.cli worker --once

# 查看队列状态和未匹配支付数量
python -m subscription.cli status
```

`daily_brief.md` 必须有北京当天的 `DATE|||YYYY-MM-DD`，以及 `SLOT|||morning` 或 `SLOT|||evening`。`SECTION` 使用 `subscription/store.py` 中的主题键，至少有一个有效主题，每条采用现有六字段格式 `来源|||标题|||链接|||标签|||摘要|||为什么重要`。支持已有的 `ai_feeds`、`ai_tech` 和 `history_news` 别名，以及旧版 `noon` 输入。显式早晚版次冲突会拒绝发布。

快照冻结后禁止覆盖。后续所有用户及失败重试使用同一期快照，偏好仅调整板块排序。当前按收件人提交独立 outbox 任务，保留个人管理链接与幂等键。分组批量投递属于后续扩展。邮件前三条主展示，其余保留在“其他值得一扫”折叠区，纯文本包含全部可用主题。折叠显示效果需在目标邮件客户端验收。

## 支付验收

1. 配置支付宝当面付应用、公钥和商户信息，网站使用可访问的 HTTPS 域名。
2. 异步通知地址为 `https://你的域名/api/payments/alipay/notify`，由预下单请求携带。
3. 从确认邮件进入管理页，创建套餐订单。核验周付实付为 3.00 元，支付成功增加 7 天。系统把订单号写入 `out_trade_no`，保存支付宝收款 URL 并生成真实二维码。
4. 验签后的成功通知检查应用、收款方、金额与唯一交易流水，在同一事务更新订单和订阅。重复通知应得到 `success`，到期时间保持一致。
5. 无法匹配的已验签交易保存在 `orders.status='unmatched'`。每周核对处理并保留支付审计记录。

退款保留 `refunded` 状态。退款处理接口、退款确认邮件及运营后台仍待后续实现，当前如需退款须通过支付宝商户后台人工处理并审核记录。订阅到期时间必须由订单推导，禁止直接修改 `expires_at`。

## 邮件验收与队列处理

确认发件域名和所需 DNS 记录后，使用自己的真实邮箱完成申请、确认、发布、接收、管理及退订。测试重复扫描、重复 worker 与退订后的已入队任务。

队列 `success` 表示 Resend 接受请求。是否进入收件箱、退信和投诉需要结合 Resend 控制台检查，目前尚未接入服务商投递事件 webhook。

每个任务保存完整发送载荷和幂等键，最多尝试六次。首次尝试超过 23 小时仍未确认成功的任务转入 `needs_review`。人工核对服务商记录后再决定处置，保留原任务与幂等键以便追溯。达到六次的 `failed` 任务保留记录供排查。

worker 在最终资格检查及 HTTP 请求期间持有 SQLite 写锁，请求超时配置为 15 秒，用于串行化退订、试读计数与投递。当前实现面向早期规模，大规模发送需要重新评估锁持有时间与吞吐。

已实现 T-7、T-3、T-1、到期、宽限结束及试读结束引导，每个续费周期独立去重。退订同时使旧确认链接失效，恢复须重新申请并确认，保留已用试读期数。网页提供通用版排序选项。

## 测试与交付边界

```powershell
python -m unittest discover -s tests -v
python -m compileall -q subscription web tests fetchers/fetch_category_news.py
node --check web/static/app.js
git diff --check
```

自动化测试使用临时 SQLite、本地 HTTP 邮件替身及临时 RSA 密钥，覆盖真实 CLI/API、签名验证、周付金额与期限、交易幂等、并发续费、快照、重试、租约恢复、试读与退订、全部主题、北京时间早晚窗口及事务迁移回滚。

浏览器验收覆盖桌面 1440 像素、手机 390 像素、窄屏 320 像素，以及申请、显式确认、管理、偏好、通用版和退订。首页新闻卡片明确标记为结构预览。真实 AI 采集质量、支付宝沙箱与生产收款、Resend 实际收件箱送达和公网部署需要相应账号与基础设施后继续验收。

本地测试结果与执行记录见 `docs/implementation.md`。共享开发规则见仓库根目录 `AGENTS.md`。

## 个人主页与登录

- `/manage` 是读者的个人主页，显示订阅状态与下一封发送时间、所关注内容、推送板块编辑、续费和退订。`/login` 通过邮箱获取登录链接，无需密码。
- 登录链接来自邮件。每封简报、确认邮件和续费提醒中的“管理”链接同样可以进入。登录状态保存在读者自己的浏览器中，退出登录会清除。
- `POST /api/login` 对任何邮箱返回相同提示。只有已确认且仍订阅的账户会收到邮件，链接一小时内有效。该邮件走同一个 outbox，需要 worker 运行。
- `GET /api/feed` 读取已冻结的快照。试读、付费或宽限期内的读者可看近 14 期，其余读者只能看最新一期。
- 邮件中所选主题全部展开并排在最前，其余放入“其他值得一扫”。未选择主题时使用通用版三条必知。
- worker 先处理确认、登录和续费类邮件，再处理批量简报。
