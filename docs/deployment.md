# 部署、守护与数据保存

本文覆盖：三个常驻进程怎么守护、怎么监控、怎么升级，以及用户数据和各类日志怎么保存。发信域名见 `docs/email-domain-setup.md`，账号配置见 `docs/subscription-operations.md`。

以下配置文件在 Windows 开发机上无法完整运行 Docker 或 systemd，所以只做了语法检查，没有在真实服务器上启动过。首次部署请先在一台测试服务器上走一遍。

## 1. 选哪种方式

| | Docker Compose | systemd |
| --- | --- | --- |
| 适合 | 一台 Linux 云服务器，想一条命令启动并自带 HTTPS | 不想装 Docker，习惯直接管理进程 |
| 包含 | 网页、worker、scheduler、每日维护、Caddy HTTPS | 同样的服务，HTTPS 另装 Caddy 或 nginx |
| 文件 | `Dockerfile`、`docker-compose.yml`、`deploy/Caddyfile` | `deploy/systemd/*`、`deploy/install-systemd.sh` |

两种方式都要求：网页、worker、scheduler 在同一台机器，共用一个 SQLite 文件，不要放网络盘。

## 2. Docker Compose

1. 服务器装好 Docker，把仓库拉到服务器，例如 `/opt/brief/app`。
2. 复制 `.env.example` 为 `.env`，填写：`PUBLIC_BASE_URL=https://你的域名`、`DOMAIN=你的域名`、`BRIEF_TOKEN_SECRET`、`RESEND_API_KEY`、`RESEND_FROM`、`DEEPSEEK_API_KEY`、支付宝四项、`CONTACT_EMAIL`、`OPERATOR_NAME`。数据库路径和备份目录由 compose 指定，不用写。
3. 域名解析到服务器，开放 80 和 443 端口。
4. 启动：`docker compose up -d --build`。
5. 查看：`docker compose ps`、`docker compose logs -f worker`。

行为：所有容器 `restart: unless-stopped`，崩溃或服务器重启后自动拉起。数据库、去重记录在卷 `brief-data`，备份在卷 `brief-backups`，证书在卷 `caddy-data`。升级重建镜像不会丢数据。

## 3. systemd

1. 服务器上把仓库放到 `/opt/brief/app`，执行 `sudo /opt/brief/app/deploy/install-systemd.sh`。脚本会建系统用户 `brief`、数据目录、虚拟环境并安装单元文件。
2. 编辑 `/etc/brief/brief.env`（模板是 `deploy/brief.env.example`）。
3. 启动：`sudo systemctl enable --now brief-api brief-worker brief-scheduler brief-maintenance.timer brief-check.timer`。
4. HTTPS：安装 Caddy，用 `deploy/Caddyfile`，设置环境变量 `DOMAIN=你的域名`。上游默认是 `127.0.0.1:8000`。
5. 查看：`systemctl status 'brief-*'`、`journalctl -u brief-worker -f`。

三个服务都是 `Restart=always`、重启间隔 5 到 10 秒，且不限制重启次数。服务以无权限用户运行，系统目录只读，只有数据目录可写。

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
- 最近一次备份超过 36 小时（设置了 `BRIEF_BACKUP_DIR` 时）。
- 警告级：重试 6 次仍失败的邮件、已验签但无法匹配的付款。

systemd 的 `brief-check.timer` 每 5 分钟运行一次。在环境文件里填 `HEALTHCHECK_PING_URL`（例如 Healthchecks.io 的地址），检查通过时才会访问该地址，服务商在你设定的时间内收不到访问就给你发通知，整台服务器宕机也能发现。Compose 版的 worker 容器带同样的健康检查，状态显示在 `docker compose ps`，但不会自动重启，也不会通知，建议同样接入外部的存活监控。

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

- [ ] `PUBLIC_BASE_URL` 是 `https://` 开头的正式域名，证书已签发。
- [ ] `BRIEF_TOKEN_SECRET` 已设置并另存一份。
- [ ] `python -m subscription.cli check-dns` 没有 `fail`，Gmail 实测 SPF、DKIM、DMARC 都是 PASS。
- [ ] 手动执行一次 `backup` 和 `check`，确认备份文件生成、`ok` 为 true。
- [ ] 外部存活监控已接入，故意停掉 worker，确认收到通知。
- [ ] 异地备份已配置并验证过恢复。
- [ ] `CONTACT_EMAIL`、`OPERATOR_NAME` 已填写，隐私政策和服务条款已审阅。
- [ ] 支付宝沙箱走通，再切换生产。
