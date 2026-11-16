# 部署、守护与数据保存

本文覆盖腾讯云中国大陆部署、进程守护、监控、升级和数据保存。发信域名见 `docs/email-domain-setup.md`，账号配置见 `docs/subscription-operations.md`。

截至 2026 年 11 月 16 日，已使用官方 Docker Compose CLI 解析全部服务配置，使用官方 Caddy 可执行程序完成配置转换，并测试共享数据库路径、登录、支付、备份、健康检查和代理后的限流行为。本机尚未实际启动 Linux 容器或 systemd 服务，服务器启动、证书签发和真实服务商验收需在云端完成。

## 腾讯云大陆接入前置条件

- 已确认中国大陆地域，当前交接采用 Ubuntu 24.04 LTS 与 Docker Compose，数据库使用本机持久卷。
- 准备实名域名，并通过腾讯云完成适用于该服务器的网站备案，再开放正式网站。取得备案号后在首页及政策页底部展示真实备案号和查询入口，按腾讯云备案指引核对后续手续。依据为腾讯云《ICP备案备案流程》，官方文档编号 `243/39038`。
- 项目包含政治、军事及经济新闻摘要，公开运营前向属地网信部门确认互联网新闻信息服务许可范围及申请主体资格。网信办《互联网新闻信息服务管理规定》第二、五、六条及《答记者问》涉及社会公共事务新闻、公众信息服务与法人主体条件，具体以主管部门对本项目服务形态的认定为准。将内容资质核验作为公开宣传收费的前置条件。
- 腾讯云安全组和主机防火墙开放 80、443，SSH 管理入口按实际连接方式设置。API 的 8000 端口保持应用内网可达，公网由 Caddy 接入。
- 在支付宝商户平台确认当面付产品、应用及经营类目已获准，准备应用 ID、收款方 ID、应用私钥和支付宝公钥，使用正式域名下的 `/api/payments/alipay/notify` 接收通知。
- 在目标服务器验证 Resend、模型接口和各主题采集源的实际连通性。境内环境需单独测试 Google News、境外 RSS 和 DNS-over-HTTPS 检查所用的服务。
- 新站默认初始化生产数据库，预览库中的测试邮箱与模拟订单保留在开发环境。已有真实用户或付款数据时，先执行下文备份恢复流程并核验数据再迁移。

## 1. 选哪种方式

| | Docker Compose | systemd |
| --- | --- | --- |
| 适合 | 一台 Linux 云服务器，想一条命令启动并自带 HTTPS | 不想装 Docker，习惯直接管理进程 |
| 包含 | 网页、worker、scheduler、每日维护、Caddy HTTPS | 同样的服务，HTTPS 另装 Caddy 或 nginx |
| 文件 | `Dockerfile`、`docker-compose.yml`、`deploy/Caddyfile` | `deploy/systemd/*`、`deploy/install-systemd.sh` |

两种方式都要求：网页、worker、scheduler 在同一台机器，共用一个 SQLite 文件，不要放网络盘。

## 2. Docker Compose

1. 服务器装好 Docker Engine 和 Compose 插件，确认 `docker version`、`docker compose version` 可用，再将 `Ksight-6324/daily-brief-radar` 的已验收提交放到 `/opt/brief/app`。
2. 复制 `.env.example` 为 `.env`，填写：`PUBLIC_BASE_URL=https://你的域名`、`DOMAIN=你的域名`、`BRIEF_TOKEN_SECRET`、`RESEND_API_KEY`、`RESEND_FROM`、`DEEPSEEK_API_KEY`、支付宝四项、`CONTACT_EMAIL`、`OPERATOR_NAME`。数据库路径和备份目录由 compose 指定，不用写。
3. 域名解析到服务器，开放 80 和 443 端口。
4. 在仓库目录执行 `docker compose config --quiet`，随后执行 `docker compose up -d --build`。配置解析通过后再启动服务。
5. 用 `docker compose ps` 查看状态，使用 `docker compose logs --tail 100 api worker scheduler maintenance` 检查启动结果。通过正式域名访问 `/health`，确认 HTTPS 和数据库可用。
6. 执行 `docker compose exec maintenance python -m subscription.cli backup --to /backups`，再执行 `docker compose exec worker python -m subscription.cli check --ping`。健康检查返回 `ok=true` 后继续真实业务验收。

行为：所有容器 `restart: unless-stopped`，崩溃或服务器重启后自动拉起。数据库、去重记录在卷 `brief-data`，备份在卷 `brief-backups`，证书在卷 `caddy-data`。升级重建镜像不会丢数据。

共享环境显式固定 `BRIEF_DB_PATH=/data/brief.db`、`BRIEF_SEEN_FILE=/data/seen_items.json`、`BRIEF_BACKUP_DIR=/backups`，覆盖本地 `.env` 中的相对路径。API 单独信任应用内网代理传来的客户端地址，依赖于当前仅 Caddy 暴露公网端口的边界。

## 3. systemd

1. 安装 `python3`、`python3-venv` 和 `git`，将仓库放到 `/opt/brief/app`，执行 `sudo /opt/brief/app/deploy/install-systemd.sh`。脚本会建系统用户 `brief`、数据目录、虚拟环境并安装单元文件。
2. 编辑 `/etc/brief/brief.env`（模板是 `deploy/brief.env.example`）。
3. 启动：`sudo systemctl enable --now brief-api brief-worker brief-scheduler brief-maintenance.timer brief-check.timer`。
4. HTTPS：安装 Caddy，用 `deploy/Caddyfile`，设置环境变量 `DOMAIN=你的域名`。上游默认是 `127.0.0.1:8000`。
5. 查看：`systemctl status 'brief-*'`、`journalctl -u brief-worker -f`。

三个服务都是 `Restart=always`、重启间隔 5 到 10 秒，且不限制重启次数。服务以无权限用户运行，系统目录只读，只有数据目录可写。

systemd 环境文件中的支付宝 PEM 必须整行置于单引号中，换行使用字面量 `\n`。沿用模板的 `ALIPAY_PRIVATE_KEY='...'` 与 `ALIPAY_PUBLIC_KEY='...'` 格式，避免 EnvironmentFile 解释反斜杠后导致验签或签名失败。

## 4. 守护能保证什么，不能保证什么

进程崩溃或被杀：自动重启，重启后继续。

| 场景 | 结果 |
| --- | --- |
| worker 在发邮件途中被杀 | 任务租约 10 分钟后被重新领取，沿用原来的幂等键，服务商不会重复发 |
| scheduler 在 08:00 当时停着 | 恢复后补扫当前窗口，当期快照缺失会生成 |
| 三个进程同时启动 | 数据库升级在事务里串行执行，已有并发启动测试 |
| 服务器重启 | 服务随开机启动，维护定时器 `Persistent=true` 会补跑错过的备份 |

守护做不到的，需要监控来发现：进程还活着但一直失败、邮件服务商拒信、磁盘写满、整台服务器宕机。

## 5. 监控与告警

命令 `python -m subscription.cli check` 返回 JSON，有 `error` 时退出码为 1。它检查：

- 邮件超过 15 分钟仍未发出（worker 没运行或服务商持续失败）。
- 邮件卡在发送中（worker 在发送途中退出）。
- 有邮件待人工核对。
- 到 08:30 或 20:30 仍没有当期快照（scheduler 没运行或生成失败）。
- 最近一次备份超过 36 小时、文件损坏或文件模式令牌密钥缺失（设置了 `BRIEF_BACKUP_DIR` 时）。
- 重试 6 次仍失败的邮件、已验签但无法匹配的付款。

systemd 的 `brief-check.timer` 和 Compose worker 健康检查均每 5 分钟执行 `check --ping`。在私密环境文件中填写 `HEALTHCHECK_PING_URL`，全部检查通过才发送外部心跳。外部平台需配置接收人、5 分钟周期和至少 10 分钟宽限时间，心跳停止后由平台通知运营者。监控请求失败会让命令返回非零，输出包含通用错误信息。

Compose 的 unhealthy 状态用于查看和告警，容器退出由重启策略处理。验收时使用测试任务制造超过 15 分钟的发送积压，或临时停止监控执行，确认外部通知确实到达。单独停止一个空闲 worker 可能没有积压，不能据此验证队列告警。

## 6. 升级

1. 先备份：`python -m subscription.cli backup --to 备份目录`（Compose：`docker compose exec maintenance python -m subscription.cli backup --to /backups`）。
2. 拉取新代码，Compose 执行 `docker compose up -d --build`，systemd 执行 `sudo systemctl restart brief-api brief-worker brief-scheduler`。
3. 数据库升级自动完成。新版本数据库不能用旧版本程序打开，程序会拒绝启动。回滚需要同时回滚代码并恢复升级前的备份。

## 7. 用户数据与日志怎么保存

原则：个人信息只放在一个地方，保留够用的最短时间，日志里不出现邮箱、令牌和邮件正文。

| 类别 | 内容 | 放在哪里 | 保留多久 | 备注 |
| --- | --- | --- | --- | --- |
| 用户数据库 | 邮箱、主题、订阅状态、订单、发送状态 | 服务器本机磁盘 `/var/lib/brief`（Compose 为卷 `brief-data`），目录权限 700 | 账号存续期间，删除账号即清除 | 不要放网络盘，不要放重启就清空的磁盘 |
| 令牌密钥 | `BRIEF_TOKEN_SECRET` | 环境文件（权限 640）加密码管理器里的一份副本 | 长期 | 明确设置它，数据库备份里就不会带密钥文件。密钥丢失意味着所有已发出的链接失效 |
| SQLite 日志文件 | `brief.db-wal`、`brief.db-shm` | 与数据库同目录 | 自动管理 | 不要手动删除。每日维护会执行一次检查点合并，用在线备份而不是直接复制文件 |
| 邮件发送记录 | `delivery_log` 表 | 数据库内 | 成功和跳过的记录，正文 30 天后清除，状态行保留用于防止重复发送。失败和待核对的保留到人工处理 | `prune` 命令每天执行 |
| 内容快照 | `daily_issue` 表 | 数据库内 | 长期，不含个人信息，个人主页回看用 | 禁止修改 |
| 订单与付款 | `orders` 表 | 数据库内 | 长期，作为财务记录 | 删除账号后只去掉与邮箱的关联。具体保存年限按所在地会计档案规定，请向会计确认 |
| 备份 | 带时间戳的 `.db` 文件 | 本机 `/var/backups/brief`，另加一份异地 | 30 天滚动 | 异地副本也要 30 天后删除并加密（对象存储服务端加密，或 `age`、`gpg`）。本机备份挡不住磁盘损坏 |
| 应用日志 | worker、scheduler、网页的标准输出 | systemd 的 journald，或 Docker json-file | systemd 在 `journald.conf` 设 `SystemMaxUse=500M`、`MaxRetentionSec=30day`。Compose 已配每个容器 10MB × 5 份 | 程序只输出状态词和计数，不含邮箱、令牌、正文 |
| 代理访问日志 | Caddy `access.log` | `/var/log/caddy`（Compose 为卷 `caddy-logs`） | 14 天，最多 5 份 × 10MB | 已删除查询里的 `token` 和 Authorization、Cookie 头。IP 地址和访问路径仍会记录，隐私政策已写明 |
| 密钥与配置 | `.env`、`/etc/brief/brief.env` | 仅服务器，权限 640 | 长期 | 不进 Git |

几条要点：

- **日志里不放个人信息**是靠设计保证的：程序不打印邮箱和令牌，网页服务关闭了访问日志（`--no-access-log`），代理日志过滤了令牌。新增代码时保持这一点。
- **备份是最容易泄露的地方**：它含有全部用户邮箱。备份目录权限 700，异地存储加密，访问权限只给你自己。
- **删除账号后的数据**仍会留在已有备份里最长 30 天，隐私政策已如实写明。恢复备份后，如果有读者在备份之后删过账号，需要重新执行删除。
- **磁盘**：给数据盘配空间告警。磁盘写满会让 SQLite 写入失败，发送和订阅都会停。
- **时区**：数据库时间都是 UTC，业务日期按北京时间。服务器本地时区不影响发送时间，但维护定时器按服务器本地时间 04:00 运行。

## 8. 上线检查清单

- [ ] 腾讯云大陆服务器、正式域名和备案已完成，页面展示真实备案信息。
- [ ] 政治、军事、经济等新闻内容的适用许可及主体资格已向属地主管部门确认，并完成适用手续。
- [ ] `PUBLIC_BASE_URL` 是 `https://` 开头的正式域名，证书已签发。
- [ ] `BRIEF_TOKEN_SECRET` 已设置并另存一份。
- [ ] `python -m subscription.cli check-dns` 没有 `fail`，Gmail 实测 SPF、DKIM、DMARC 都是 PASS。
- [ ] 手动执行一次 `backup` 和 `check`，确认备份文件生成、`ok` 为 true。
- [ ] 外部存活监控已接入，制造发送积压或停止心跳执行后确认收到通知。
- [ ] 异地备份已配置并验证过恢复。
- [ ] `CONTACT_EMAIL`、`OPERATOR_NAME` 已填写，隐私政策和服务条款已审阅。
- [ ] 支付宝沙箱走通，生产完成真实 3 元周付、回调验签、重复通知及续费核对，订阅仅延长一次。收款码与网页使用订单创建后 30 分钟的同一截止时间。
- [ ] 用 QQ、163 和 Gmail 等实际目标邮箱验证确认信、登录信和早晚报，检查服务商投递事件与垃圾箱情况。发送状态 success 表示 Resend 接受请求，实际到达需收件人验证。
- [ ] 连续观察至少两天四期真实快照与邮件，检查北京时间 08:00／20:00 入队、主题来源、摘要质量、试读耗尽和真实退订。
- [ ] 核对 Resend 当前额度与每日两封乘订阅人数的需求，并预留确认、登录和续费提醒用量。
- [ ] 重建容器并重启云主机后，用户、付款、订阅到期时间和发送幂等状态均保持一致。
- [ ] 退款规则、服务条款、个人信息及境外处理告知已按实际经营主体与服务商审核，相关经营类目和内容范围已确认。

全部人工验收项完成后开放小范围付费试运行，将真实服务结果记录到 `docs/implementation.md` 再扩大宣传。
