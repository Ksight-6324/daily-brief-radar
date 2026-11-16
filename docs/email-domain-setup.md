# 发信域名配置：SPF、DKIM、DMARC

目的：让 Gmail、QQ 邮箱、网易邮箱等相信“这封邮件确实来自你的域名”。三项缺一项，试读邮件都容易进垃圾箱，而试读转化全靠这几封邮件。

这些记录必须在你的域名 DNS 管理后台和 Resend 控制台里操作，我无法代你完成。仓库提供了检查命令，配完后用它验证。

## 1. 选发信地址

建议用专门的子域名发信，例如 `mail.你的域名.com`，把简报邮件的信誉和主域名隔开。

```
RESEND_FROM=Daily Brief Radar <brief@mail.你的域名.com>
```

## 2. 在 Resend 添加域名并抄记录

1. 登录 Resend，进入 Domains，添加上面的发信域名。
2. 控制台会列出需要添加的 DNS 记录。**逐条照抄控制台给出的值，不要自己拼写。**不同时期创建的域名记录形式不同：
   - 常见形式：发送子域名 `send` 上的一条 TXT（SPF）和一条 MX（退信反馈），加 `resend._domainkey` 上的一条 TXT（DKIM 公钥）。
   - 较新的域名可能显示 CNAME 记录。
3. 到你的域名服务商（阿里云、腾讯云 DNSPod、Cloudflare 等）的解析页面逐条添加。
4. 回到 Resend 点验证。通常十几分钟内完成，最长可能需要 72 小时。超时后点 “Restart verification”。

### 填写时最常见的错误

来自 Resend 的排错说明：

- 主机记录只填子域名部分，例如 `send`、`resend._domainkey`，不要带完整域名。多数国内云解析和 Hetzner、Squarespace 都是这样。
- DKIM 的值必须与控制台完全一致，不要加引号、空格，也不要被截断。
- MX 记录有的服务商会自动补上你的域名，此时需要在值末尾加一个英文句点。
- 使用 Cloudflare 时，CNAME 记录要关掉代理（灰色云朵），否则验证不通过。

## 3. 添加 DMARC

等 Resend 显示域名已验证，再添加。DMARC 告诉收件方“SPF 和 DKIM 都对不上时怎么处理”，并把报告发给你。

| 项目 | 值 |
| --- | --- |
| 类型 | TXT |
| 主机记录 | `_dmarc.mail`（发信域名是 `mail.你的域名.com` 时）。发信域名就是主域名时填 `_dmarc` |
| 值（起步） | `v=DMARC1; p=none; rua=mailto:dmarc@你的域名.com` |

`rua` 邮箱需要真的能收信，每天会收到各大邮箱服务商发来的汇总报告（压缩的 XML）。

推荐的收紧节奏：

1. `p=none` 运行 2 到 4 周，在报告里确认自己的邮件全部通过、没有被误判。
2. 改为 `p=quarantine`，不合格的邮件进垃圾箱。
3. 稳定后改为 `p=reject`。

## 4. 用命令检查

配置好 `.env`（或服务器的环境文件）里的 `RESEND_FROM` 后：

```bash
python -m subscription.cli check-dns
```

也可以指定域名和记录名：

```bash
python -m subscription.cli check-dns mail.你的域名.com --selector resend --send-label send
```

输出每一项为 `ok`、`warn` 或 `fail`，有 `fail` 时命令返回非零。它只检查记录是否存在于正确位置，不比对值，所以通过后仍需做下面的实测。检查通过 DNS-over-HTTPS 向 Google 公共 DNS 查询，只发送域名，不发送任何读者数据。

## 5. 实测一封真实邮件

1. 在订阅页用自己的 Gmail 邮箱申请订阅，收到确认邮件。
2. 在 Gmail 打开这封邮件，右上角菜单选“显示原邮件”。
3. 顶部应显示 `SPF: PASS`、`DKIM: PASS`、`DMARC: PASS`。
4. 如果进了垃圾箱，先看这三项哪一项不是 PASS。

## 参考

- [What if my domain is not verifying? - Resend](https://resend.com/docs/knowledge-base/what-if-my-domain-is-not-verifying)
- [Verified Domains - Resend](https://resend.com/docs/dashboard/domains/introduction)
- [Email Authentication: A Developer's Guide - Resend](https://resend.com/blog/email-authentication-a-developers-guide)
