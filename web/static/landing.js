"use strict";

// Landing page motion and the interactive sample email. Nothing here talks to the server.
(() => {
  const landing = document.getElementById("landing");
  if (!landing) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
  const onLanding = () => !landing.hidden;

  // ---- Smooth wheel scrolling --------------------------------------------------------------
  // Mouse-wheel steps are eased toward their target instead of jumping. Keyboard, scrollbar,
  // touch, zoom (Ctrl+wheel) and inner scrollable areas keep the browser's own behaviour.
  if (!reduceMotion && finePointer) {
    let target = window.scrollY;
    let current = window.scrollY;
    let running = false;
    const maxScroll = () => document.documentElement.scrollHeight - window.innerHeight;
    const scrollsInside = (node, deltaY) => {
      for (let el = node; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
        const style = getComputedStyle(el);
        if (!/(auto|scroll)/.test(style.overflowY) || el.scrollHeight <= el.clientHeight) continue;
        if (deltaY < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight) return true;
      }
      return false;
    };
    const step = () => {
      current += (target - current) * 0.11;
      if (Math.abs(target - current) < 0.5) current = target;
      window.scrollTo({ top: current, behavior: "instant" });
      if (current !== target) requestAnimationFrame(step);
      else running = false;
    };
    window.addEventListener(
      "wheel",
      (event) => {
        if (!onLanding() || event.ctrlKey || event.defaultPrevented) return;
        if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
        if (scrollsInside(event.target, event.deltaY)) return;
        event.preventDefault();
        if (!running) target = current = window.scrollY;
        const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? window.innerHeight : 1;
        target = clamp(target + event.deltaY * unit, 0, maxScroll());
        if (!running) {
          running = true;
          requestAnimationFrame(step);
        }
      },
      { passive: false },
    );
    // Any other scroll (keys, scrollbar, anchor links) resets the wheel target.
    window.addEventListener("scroll", () => {
      if (!running) target = current = window.scrollY;
    }, { passive: true });
  }

  // ---- Scroll-linked effects, computed once per frame ----------------------------------------
  const bar = document.createElement("div");
  bar.className = "scroll-bar";
  bar.setAttribute("aria-hidden", "true");
  document.body.prepend(bar);

  const hero = $(".hero");
  const ticker = $(".ticker-track");
  const tickerAnimation = () => (ticker && ticker.getAnimations ? ticker.getAnimations()[0] : null);
  let lastY = window.scrollY;
  let velocity = 0;
  let queued = false;
  const frameHandlers = [];

  function onFrame() {
    queued = false;
    const y = window.scrollY;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    bar.hidden = !onLanding();
    bar.style.setProperty("--p", max > 0 ? clamp(y / max).toFixed(4) : "0");
    if (hero && !reduceMotion) hero.style.setProperty("--sy", String(Math.min(y, 900)));
    velocity = y - lastY;
    lastY = y;
    const marquee = tickerAnimation();
    if (marquee && !reduceMotion) marquee.playbackRate = 1 + Math.min(Math.abs(velocity) / 6, 8);
    for (const handler of frameHandlers) handler(y);
  }
  const requestFrame = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(onFrame);
  };
  window.addEventListener("scroll", requestFrame, { passive: true });
  window.addEventListener("resize", requestFrame);
  // Let the ticker drift back to its normal speed after the wheel stops.
  if (!reduceMotion)
    setInterval(() => {
      const marquee = tickerAnimation();
      if (marquee && marquee.playbackRate > 1) marquee.playbackRate = Math.max(1, marquee.playbackRate * 0.85);
    }, 120);

  // ---- Reveal blocks as they enter the viewport ----------------------------------------------
  if (!reduceMotion && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const node = entry.target;
          observer.unobserve(node);
          node.classList.add("in");
          const delay = 1150 + Number(node.style.getPropertyValue("--i") || 0) * 110;
          setTimeout(() => node.classList.remove("reveal", "in"), delay);
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -6% 0px" },
    );
    const selectors = [
      ".why .eyebrow, .why h2",
      ".why-card, .why-arrow",
      ".features .eyebrow, .features h2, .features .section-lede",
      ".feature",
      ".sample-head",
      ".sample-tools",
      ".mail-frame",
      ".steps .eyebrow, .steps h2",
      ".steps-grid li",
      ".steps-cta",
      ".subscribe-section",
      ".plans-section",
    ];
    for (const node of $$(selectors.join(","), landing)) {
      const box = node.getBoundingClientRect();
      if (box.height > 0 && box.top < window.innerHeight * 0.9) continue;
      node.classList.add("reveal");
      node.style.setProperty("--i", String(Array.from(node.parentElement.children).indexOf(node) % 4));
      observer.observe(node);
    }
  }

  // ---- Hero: a live email whose topic, headline, subtitle and summary fly in -----------------
  const stories = [
    ["08:00 早报", "股票经济", "利率按兵不动，市场在读措辞", "声明对通胀的措辞微调，年内降息节奏被重新评估。",
      "利率预期会传导到房贷、理财收益和企业融资成本，值得留意下一次数据公布。"],
    ["08:00 早报", "人工智能", "新模型发布，先看实际能做什么", "官方强调长文本与工具调用，第三方评测尚未全部公开。",
      "分清已发布的功能和待验证的说法，才知道哪些真的会影响你的工作。"],
    ["08:00 早报", "就业机会", "招聘旺季，哪些岗位在回暖", "部分技术类岗位需求回升，传统行业岗位保持平稳。",
      "了解市场冷热，有助于判断求职或转岗的时机。"],
    ["20:00 晚报", "石油黄金", "油价窄幅震荡，黄金走势分化", "油价全天在窄幅区间波动，黄金在避险与美元之间拉扯。",
      "能源价格影响通胀预期，也影响各国央行的决策空间。"],
    ["20:00 晚报", "体育健康", "久坐一族的“碎片运动”，研究怎么说", "每小时起身活动几分钟，对代谢指标有积极影响。",
      "不必专门腾出整块时间，也能找到可行的起点。效果因人而异，不构成医疗建议。"],
    ["20:00 晚报", "设计美术", "百年之后，极简设计为什么还在流行", "功能优先与几何形态，仍在塑造今天的界面与产品。",
      "理解设计语言的来路，更容易看懂眼前的产品为什么长这样。"],
  ];
  const mail = $(".live-mail");
  if (mail && !reduceMotion) {
    const dwell = 5200;
    const fields = {
      edition: $(".lm-edition", mail),
      topic: $(".lm-topic .fly", mail),
      title: $(".lm-title .fly", mail),
      sub: $(".lm-sub .fly", mail),
      sum: $(".lm-sum .fly", mail),
      count: $(".lm-count", mail),
    };
    mail.style.setProperty("--dwell", `${dwell}ms`);
    let index = 0;
    let timer = 0;
    const show = (next) => {
      index = next;
      const [edition, topic, title, sub, sum] = stories[index];
      fields.edition.textContent = edition;
      fields.topic.textContent = topic;
      fields.title.textContent = title;
      fields.sub.textContent = sub;
      fields.sum.textContent = sum;
      fields.count.textContent = `${String(index + 1).padStart(2, "0")} / ${String(stories.length).padStart(2, "0")}`;
      mail.classList.remove("is-out", "is-in");
      void mail.offsetWidth; // restart the CSS animations
      mail.classList.add("is-in");
    };
    const cycle = () => {
      mail.classList.add("is-out");
      timer = setTimeout(() => {
        show((index + 1) % stories.length);
        timer = setTimeout(cycle, dwell);
      }, 480);
    };
    // Only animate while the hero is on screen and the tab is visible.
    let visible = true;
    const sync = () => {
      clearTimeout(timer);
      if (visible && !document.hidden && onLanding()) timer = setTimeout(cycle, dwell);
    };
    if ("IntersectionObserver" in window)
      new IntersectionObserver((entries) => {
        visible = entries[0].isIntersecting;
        sync();
      }).observe(mail);
    document.addEventListener("visibilitychange", sync);
    show(0);
    sync();
  }

  // ---- Unfold: the pinned email assembles itself as you scroll ------------------------------
  const unfold = $(".unfold");
  if (unfold && !reduceMotion) {
    const pieces = $$(".u-piece", unfold);
    const steps = $$(".unfold-steps li", unfold);
    const clock = $("#unfold-time", unfold);
    const mailCard = $(".unfold-mail", unfold);
    const pinQuery = window.matchMedia("(min-width: 1001px)");
    // Each line gets its own slot along the scroll: [start, start + span].
    const span = 0.09;
    const slots = pieces.map((_, i) => 0.04 + (i / Math.max(1, pieces.length - 1)) * (0.86 - span));
    let pinned = false;

    const paint = (progress) => {
      pieces.forEach((piece, i) => piece.style.setProperty("--t", clamp((progress - slots[i]) / span).toFixed(3)));
      const reached = pieces.filter((_, i) => progress >= slots[i] + span * 0.5);
      const step = reached.length ? Math.max(...reached.map((piece) => Number(piece.dataset.step))) : -1;
      steps.forEach((item, i) => item.classList.toggle("is-on", i <= step));
      const seconds = Math.round(clamp(progress / 0.95) * 180);
      clock.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    };

    const layout = () => {
      // Pin only when the whole email fits on screen; otherwise lines fly in as they appear.
      unfold.classList.remove("is-pinned");
      const fits = mailCard.offsetHeight + 80 < window.innerHeight;
      pinned = pinQuery.matches && fits;
      unfold.classList.toggle("is-pinned", pinned);
      unfold.classList.add("is-animated");
      if (!pinned) paint(0);
      requestFrame();
    };

    frameHandlers.push(() => {
      if (!pinned) return;
      const box = unfold.getBoundingClientRect();
      paint(clamp(-box.top / (box.height - window.innerHeight)));
    });

    if ("IntersectionObserver" in window) {
      const lineObserver = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (pinned || !entry.isIntersecting) continue;
            entry.target.style.setProperty("--t", "1");
            const step = Number(entry.target.dataset.step);
            steps.forEach((item, i) => item.classList.toggle("is-on", i <= step || item.classList.contains("is-on")));
          }
        },
        { threshold: 0.6 },
      );
      pieces.forEach((piece) => lineObserver.observe(piece));
    }
    window.addEventListener("resize", layout);
    layout();
  }

  // ---- Sample email: switch edition, and follow topics to see them move to the top and expand.
  const sample = document.getElementById("preview");
  if (!sample) return;
  const tabs = $$('[role="tab"]', sample);
  const panels = $$(".mail-panel", sample);
  const chipBox = document.getElementById("demo-topics");
  const note = document.getElementById("demo-note");
  const followed = new Set();
  const activePanel = () => panels.find((panel) => !panel.hidden);

  for (const panel of panels)
    $$(".m-story", panel).forEach((story, index) => {
      story.dataset.order = String(index);
    });

  function renderChips() {
    chipBox.replaceChildren(
      ...$$(".m-story", activePanel()).map((story) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "demo-chip";
        chip.dataset.topic = story.dataset.topic;
        chip.textContent = story.dataset.topic;
        chip.setAttribute("aria-pressed", String(followed.has(story.dataset.topic)));
        return chip;
      }),
    );
  }

  function arrange(animate) {
    const list = $(".m-list", activePanel());
    const items = $$(".m-story", list);
    const before = new Map(items.map((story) => [story, story.getBoundingClientRect().top]));
    const ordered = items.slice().sort(
      (a, b) =>
        Number(followed.has(b.dataset.topic)) - Number(followed.has(a.dataset.topic)) ||
        Number(a.dataset.order) - Number(b.dataset.order),
    );
    for (const story of ordered) {
      const on = followed.has(story.dataset.topic);
      story.classList.toggle("is-followed", on);
      story.classList.toggle("is-folded", followed.size > 0 && !on);
      list.append(story);
    }
    if (animate && !reduceMotion) {
      for (const story of ordered) {
        const shift = before.get(story) - story.getBoundingClientRect().top;
        if (Math.abs(shift) < 1) continue;
        story.animate([{ transform: `translateY(${shift}px)` }, { transform: "none" }], {
          duration: 700,
          easing: "cubic-bezier(0.16, 1, 0.3, 1)",
        });
      }
    }
    note.textContent = followed.size
      ? `已关注 ${followed.size} 个主题：它们完整展开并排在最前，其余收起。`
      : "未选择主题时显示通用版，全部展开。";
  }

  function selectTab(tab) {
    for (const item of tabs) {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
    }
    followed.clear();
    renderChips();
    arrange(false);
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const next = tabs[(tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      selectTab(next);
      next.focus();
    });
  }

  chipBox.addEventListener("click", (event) => {
    const chip = event.target.closest(".demo-chip");
    if (!chip) return;
    const topic = chip.dataset.topic;
    if (followed.has(topic)) followed.delete(topic);
    else followed.add(topic);
    chip.setAttribute("aria-pressed", String(followed.has(topic)));
    arrange(true);
  });

  renderChips();
  onFrame();
})();
