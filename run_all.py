"""
一键运行：依次执行抓取 → AI摘要 → 发送简报邮件

订阅版由调度器在北京时间 08:00 和 20:00 调用。

用法：python run_all.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env")

# 每一步最多重试几次、每次重试前等多久（秒）
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 60

FETCH_STEPS = [
    ("抓取 GitHub Trending", "fetchers/fetch_github_trending.py"),
    ("抓取 Hacker News", "fetchers/fetch_hacker_news.py"),
    ("抓取科技/AI媒体RSS", "fetchers/fetch_rss_sources.py"),
    ("抓取经济/政治/科学突破新闻", "fetchers/fetch_category_news.py"),
    ("抓取 YouTube 频道更新", "fetchers/fetch_youtube.py"),
]

steps = FETCH_STEPS + [
    ("生成AI摘要", "core/summarize.py"),
    ("发送简报邮件", "core/send_email.py"),
]

if os.environ.get("BRIEF_MODE") == "subscription":
    from subscription.store import BEIJING, EDITIONS, Store, edition_for, utcnow

    now = utcnow()
    edition = os.environ.get("BRIEF_EDITION") or edition_for(now)
    if edition not in EDITIONS:
        raise ValueError("BRIEF_EDITION 必须为 morning 或 evening")
    os.environ["BRIEF_EDITION"] = edition
    frozen = Store().issue(now.astimezone(BEIJING).date().isoformat(), edition)
    steps = [] if frozen else [
        ("抓取订阅版新闻", "fetchers/fetch_category_news.py"),
        ("生成本期主题摘要", "-m", "subscription.cli", "generate"),
    ]
    steps.append(("冻结快照并创建发送任务", "-m", "subscription.cli", "publish"))

def run_step_with_retry(step_name, script, extra_args):
    """执行一步，失败就等一会儿重试，重试次数用完才算真正失败"""
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n{'='*40}")
        print(f"▶ 正在执行：{step_name}（第{attempt}次尝试）")
        print('='*40)
        result = subprocess.run([sys.executable, script] + extra_args)
        if result.returncode == 0:
            return True
        print(f"⚠️ {step_name} 第{attempt}次失败（退出码 {result.returncode}）")
        if attempt < MAX_RETRIES:
            print(f"   等待 {RETRY_WAIT_SECONDS} 秒后重试...")
            time.sleep(RETRY_WAIT_SECONDS)
    print(f"❌ {step_name} 重试{MAX_RETRIES}次仍然失败，停止后续步骤")
    return False


all_ok = True
for step in steps:
    step_name, script = step[0], step[1]
    extra_args = list(step[2:])
    if not run_step_with_retry(step_name, script, extra_args):
        all_ok = False
        break

if all_ok:
    print("\n✅ 简报任务已完成" if os.environ.get("BRIEF_MODE") == "subscription" else "\n✅ 全部完成！简报已生成并发送")
else:
      sys.exit(1)

