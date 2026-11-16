"""Check the DNS records that decide whether mail from the sending domain is trusted.

Record values come from the mail provider's dashboard and differ per domain, so this only checks that the
right kinds of records exist where they should. Lookups use DNS over HTTPS and send only the domain names
to the resolver, never reader data."""

import re
from email.utils import parseaddr

import requests

DOH_URL = "https://dns.google/resolve"
TYPES = {"TXT": 16, "MX": 15, "CNAME": 5}


def doh_lookup(name, rtype):
    """Return [(type, data)] answers. NXDOMAIN and empty answers give an empty list."""
    response = requests.get(DOH_URL, params={"name": name, "type": rtype}, timeout=10,
                            headers={"Accept": "application/dns-json"})
    response.raise_for_status()
    answers = response.json().get("Answer") or []
    names = {value: key for key, value in TYPES.items()}
    found = []
    for answer in answers:
        kind = names.get(answer.get("type"))
        if kind:
            data = answer.get("data", "")
            if kind == "TXT":
                # Long TXT values arrive as several quoted strings that must be joined.
                data = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', data)) or data.strip('"')
            found.append((kind, data))
    return found


def domain_of(sender):
    address = parseaddr(sender)[1]
    if "@" not in address:
        raise ValueError("发件地址无效，需要形如 Brief <brief@example.com>")
    return address.rsplit("@", 1)[1].lower()


def parent_domains(domain):
    labels = domain.split(".")
    return [".".join(labels[index:]) for index in range(len(labels) - 1)]


def result(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def check_domain(domain, resolver=doh_lookup, selector="resend", send_label="send"):
    checks = []
    send_host = f"{send_label}.{domain}"

    # SPF: Resend publishes it on the send subdomain (TXT, or a CNAME on newer domains).
    spf = [data for kind, data in resolver(send_host, "TXT") if kind == "TXT" and data.lower().startswith("v=spf1")]
    cname = [data for kind, data in resolver(send_host, "CNAME") if kind == "CNAME"]
    root_spf = [data for kind, data in resolver(domain, "TXT") if kind == "TXT" and data.lower().startswith("v=spf1")]
    if spf:
        checks.append(result("SPF", "ok", f"{send_host} 已有 SPF 记录"))
    elif cname:
        checks.append(result("SPF", "ok", f"{send_host} 由 CNAME 托管，指向 {cname[0]}"))
    elif root_spf:
        checks.append(result("SPF", "warn", f"只在 {domain} 找到 SPF，没有 {send_host}。若服务商要求发送子域名，请按其控制台添加"))
    else:
        checks.append(result("SPF", "fail", f"没有找到 {send_host} 的 SPF 记录或 CNAME"))
    if spf and not any(kind == "MX" for kind, _ in resolver(send_host, "MX")):
        checks.append(result("退信 MX", "warn", f"{send_host} 没有 MX 记录，退信和投诉反馈可能收不到"))

    # DKIM: a TXT public key, or a CNAME to the provider's key.
    dkim_host = f"{selector}._domainkey.{domain}"
    dkim = resolver(dkim_host, "TXT")
    if any(kind == "TXT" and "p=" in data for kind, data in dkim):
        checks.append(result("DKIM", "ok", f"{dkim_host} 已发布公钥"))
    elif any(kind == "CNAME" for kind, _ in dkim) or resolver(dkim_host, "CNAME"):
        checks.append(result("DKIM", "ok", f"{dkim_host} 由 CNAME 托管"))
    else:
        checks.append(result("DKIM", "fail", f"没有找到 {dkim_host}。公钥必须与控制台给出的值完全一致，不要加引号或空格"))

    # DMARC: the policy lives at _dmarc of the sending domain or its organisational parent.
    policy = None
    for candidate in parent_domains(domain):
        records = [data for kind, data in resolver(f"_dmarc.{candidate}", "TXT") if kind == "TXT" and data.upper().startswith("V=DMARC1")]
        if records:
            policy = (candidate, records[0])
            break
    if policy is None:
        checks.append(result("DMARC", "fail", f"没有找到 _dmarc.{domain}。建议先添加 v=DMARC1; p=none; rua=mailto:你的邮箱"))
    else:
        where, record = policy
        match = re.search(r"(?:^|;)\s*p\s*=\s*(\w+)", record, re.I)
        level = match.group(1).lower() if match else ""
        reports = "rua=" in record.lower()
        if level in ("quarantine", "reject"):
            checks.append(result("DMARC", "ok", f"_dmarc.{where} 策略为 {level}" + ("" if reports else "，但没有 rua 报告地址")))
        elif level == "none":
            checks.append(result("DMARC", "warn", f"_dmarc.{where} 为监控模式 p=none。收到几周报告确认没有误伤后，建议升到 quarantine"
                                 + ("" if reports else "，并添加 rua 报告地址")))
        else:
            checks.append(result("DMARC", "fail", f"_dmarc.{where} 的策略无法识别：{record}"))
    return checks
