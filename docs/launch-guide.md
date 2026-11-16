# 从买域名到上线运行（Museletter 轻量模式）

目标：读者在网页订阅，收到确认邮件，每天 08:00、20:00 收到简报，随时一键退订。全程约需 1 到 3 天，主要时间花在 AWS 审核。

价格和页面会变，下面不写具体金额，以各平台当时显示为准。

## 总览

| 步骤 | 做什么 | 谁来做 | 耗时 |
| --- | --- | --- | --- |
| 1 | 买域名 | 你 | 10 分钟 |
| 2 | 买一台海外小服务器 | 你 | 20 分钟 |
| 3 | 域名指向服务器 | 你 | 10 分钟 |
| 4 | 配置 Amazon SES 并申请出沙盒 | 你 | 30 分钟加审核 |
| 5 | 在服务器上安装并启动 | 你，我可以逐条陪你核对 | 40 分钟 |
| 6 | 加 HTTPS，只开放读者要用的路径 | 你 | 15 分钟 |
| 7 | 本地测试，再正式开启 | 你 | 30 分钟 |

选海外服务器和海外域名商，是因为发信用 AWS，且不需要在中国大陆备案。如果你想把服务器放在大陆，需要先做备案，流程长，本文不覆盖。

## 步骤 1：买域名

1. 选域名商。常见选择：Cloudflare Registrar、Namecheap、Porkbun。Cloudflare 的好处是后面 DNS 也在同一个地方，且按成本价收费。
2. 注册账号，搜索你想要的名字，优先 `.com`。名字尽量短、好念、不含连字符。
3. 购买时打开 WHOIS 隐私保护（多数商家默认免费），避免你的个人地址公开。
4. 付款后，记下登录邮箱，并开启两步验证。域名被盗是严重事故。

## 步骤 2：买一台服务器

1. 选云厂商，例如 Hetzner、DigitalOcean、AWS Lightsail、Vultr。要求：Ubuntu 22.04 或 24.04，1 核 1 到 2 GB 内存、20 GB 以上磁盘即可。
2. 创建时选 SSH 密钥登录（比密码安全）。Windows 上可在 PowerShell 执行 `ssh-keygen -t ed25519` 生成，把 `~/.ssh/id_ed25519.pub` 的内容粘贴到云厂商。
3. 创建完成后得到一个公网 IP，例如 `203.0.113.10`。
4. 防火墙只开放 22、80、443 三个端口。

## 步骤 3：域名指向服务器

1. 在域名商的 DNS 管理里添加一条记录：类型 A，名称 `news`，内容填服务器 IP。如果用 Cloudflare，把代理状态设为“仅 DNS”（灰色云朵）。
2. 之后订阅入口就是 `https://news.你的域名`。
3. 在自己电脑上用 `ping news.你的域名` 确认能解析到服务器 IP。生效通常几分钟。

## 步骤 4：配置 Amazon SES

1. 注册 AWS 账号，绑定银行卡。登录后右上角选区域，例如 `us-east-1`，之后一直用这个区域，并让 `.env` 的 `AWS_REGION` 与它一致。
2. 进入 SES，选 Verified identities，创建身份，类型选 Domain，填 `mail.你的域名`（发信用子域名）。勾选 Easy DKIM。
3. SES 会给出三条 CNAME 记录。逐条添加到域名的 DNS，注意主机名只填子域名部分，Cloudflare 里同样关闭代理。几分钟到几小时后状态变为 Verified。
4. 在身份的设置里启用自定义 MAIL FROM 域名（推荐，例如 `bounce.mail.你的域名`），按提示添加 MX 和 SPF 的 TXT 记录。
5. 添加 DMARC 记录，起步用 `v=DMARC1; p=none; rua=mailto:你的邮箱`。细节见 `docs/email-domain-setup.md`。
6. 申请移出沙盒：SES 的 Account dashboard 里点 Request production access。用途说明写清楚：用户在网站主动订阅、双重确认邮件、每封邮件有一键退订、会处理退信和投诉。通常一天内有答复，被追问就如实回答。
7. 创建 IAM 用户：IAM，Users，创建用户，只附加发信权限（`AmazonSESFullAccess` 最省事，更严格的做法是自己写只允许发信的策略）。给它创建访问密钥，保存 Access Key ID 和 Secret，这两个值只会显示一次，不要发到聊天或提交到 Git。
8. 退信和投诉回传（上线前必做）：SES 里创建 Configuration set，再创建 SNS 主题，让配置集把 Bounce 和 Complaint 事件发到该主题。等第 6 步服务器运行后，再把主题订阅到 `https://news.你的域名/webhooks/sns`。

## 步骤 5：在服务器上安装并启动

用 SSH 登录服务器：`ssh root@服务器IP`（建议另建普通用户，下面命令以有 sudo 权限的用户执行）。

1. 安装 Docker：`curl -fsSL https://get.docker.com | sh`，再 `sudo usermod -aG docker $USER`，重新登录。
2. 拉取代码：`git clone 你的仓库地址 /opt/brief/app && cd /opt/brief/app`。
3. 创建配置：`cp .env.example .env`，用 `nano .env` 编辑，至少填：
   - `MUSELETTER_API_KEY`：自己生成，`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
   - `MUSELETTER_BASE_URL=https://news.你的域名`
   - `MUSELETTER_FROM_EMAIL=brief@mail.你的域名`
   - `MUSELETTER_FROM_NAME=Daily Brief Radar`
   - `MUSELETTER_POSTAL_ADDRESS`：你的真实邮寄地址，会出现在每封邮件里
   - `MUSELETTER_EMAIL_PROVIDER=ses`、`AWS_REGION`、`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`
   - `MUSELETTER_SES_CONFIGURATION_SET`、`MUSELETTER_SNS_TOPIC_ARN`（第 4 步第 8 项）
   - `DEEPSEEK_API_KEY`（内容生成）
   - 保持 `MUSELETTER_OPT_IN=double`、`MUSELETTER_SKIP_TEST=false`
4. 权限收紧：`chmod 600 .env`。
5. 启动：`docker compose -f compose.museletter.yml up -d --build`。
6. 检查：`docker compose -f compose.museletter.yml ps`，两个服务都应是运行状态。`docker compose -f compose.museletter.yml logs -f` 看日志，Ctrl+C 退出。

## 步骤 6：HTTPS，并且只开放读者需要的路径

引擎的管理接口（`/v1/...`）只给你自己用，不能对公网开放。Compose 已把引擎端口绑在服务器本机 `127.0.0.1:8000`。用 Caddy 做公网入口，只转发公开路径：

1. 安装 Caddy：按 Caddy 官网的 Ubuntu 安装说明。
2. 编辑 `/etc/caddy/Caddyfile`：

```
news.你的域名 {
	@public path /subscribe/* /confirm/* /unsubscribe/* /webhooks/sns /health
	handle @public {
		reverse_proxy 127.0.0.1:8000
	}
	handle {
		respond 404
	}
}
```

3. `sudo systemctl reload caddy`。Caddy 会自动申请证书，域名必须已经解析到服务器。
4. 浏览器打开 `https://news.你的域名/health`，应返回正常。打开 `https://news.你的域名/v1/lists` 应是 404，说明管理接口没有暴露。
5. 回到 AWS，把 SNS 主题订阅到 `https://news.你的域名/webhooks/sns`，确认订阅状态为 Confirmed。

## 步骤 7：测试，再正式开启

1. 在服务器上设置客户端连接：`export MUSELETTER_URL=http://127.0.0.1:8000 MUSELETTER_API_KEY=你的密钥`，执行 `docker compose -f compose.museletter.yml exec museletter museletter doctor`（或在服务器安装客户端后直接执行 `museletter doctor`）。所有检查项应通过。
2. 用你自己的 Gmail 订阅：`curl -X POST https://news.你的域名/subscribe/default -H 'Content-Type: application/json' -d '{"email":"你的邮箱"}'`。应收到确认邮件，点击确认链接。
3. 在 Gmail 里“显示原邮件”，检查 SPF、DKIM、DMARC 都是 PASS。
4. 手动发一次：`docker compose -f compose.museletter.yml exec publisher python -m newsletter generate`，再 `python -m newsletter publish --draft`，在引擎里用 `museletter campaigns list` 看草稿，先做测试发送。
5. 确认邮件内容、退订链接、一键退订都正常后，正式发布：`python -m newsletter publish`。
6. 全部满意后，再考虑 `MUSELETTER_SKIP_TEST=true` 让调度器每天自动发送。自动运行的是 `publisher` 服务，它在 07:30、19:30 生成，08:00、20:00 之后发布。
7. 备份：引擎的数据在 Docker 卷 `museletter-data` 里，定期复制 `museletter.db`。订阅者数据都在这里，务必有异地备份。

## 常见问题

- **确认邮件进了垃圾箱**：看 DKIM、SPF、DMARC 哪项不是 PASS，见 `docs/email-domain-setup.md`。
- **SES 报 not verified 或 sandbox 错误**：身份还没验证，或还没有移出沙盒。
- **订阅请求返回 404 或连不上**：检查 DNS 是否解析、Caddy 是否运行、`MUSELETTER_BASE_URL` 是否与域名一致。
- **想让读者在你自己的网页上订阅**：网页表单向 `/subscribe/default` 发 POST 即可。本仓库原有的首页是付费模式的页面，轻量模式下需要另外做一个简单订阅页，可以再让我来做。
