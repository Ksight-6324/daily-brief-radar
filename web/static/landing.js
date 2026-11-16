"use strict";

// Landing page motion and the interactive sample email. Nothing here talks to the server.
(() => {
  const landing = document.getElementById("landing");
  if (!landing) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  // Scroll progress, drawn in the three primary colours.
  const bar = document.createElement("div");
  bar.className = "scroll-bar";
  bar.setAttribute("aria-hidden", "true");
  document.body.prepend(bar);
  let progressQueued = false;
  function updateProgress() {
    progressQueued = false;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    bar.hidden = landing.hidden;
    bar.style.setProperty("--p", max > 0 ? Math.min(1, window.scrollY / max).toFixed(4) : "0");
  }
  window.addEventListener(
    "scroll",
    () => {
      if (progressQueued) return;
      progressQueued = true;
      requestAnimationFrame(updateProgress);
    },
    { passive: true },
  );
  updateProgress();

  // Reveal blocks as they enter the viewport. Blocks already on screen are left alone,
  // and the helper class is dropped afterwards so hover effects keep their own transitions.
  if (!reduceMotion && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const node = entry.target;
          observer.unobserve(node);
          node.classList.add("in");
          const delay = 750 + Number(node.style.getPropertyValue("--i") || 0) * 90;
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

  // Gentle pointer parallax on the hero composition.
  const hero = $(".hero");
  const stage = $(".hero-stage");
  if (hero && stage && !reduceMotion && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    let frame = 0;
    const move = (x, y) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        stage.style.setProperty("--mx", x.toFixed(3));
        stage.style.setProperty("--my", y.toFixed(3));
      });
    };
    hero.addEventListener("pointermove", (event) => {
      const box = stage.getBoundingClientRect();
      const clamp = (value) => Math.max(-1, Math.min(1, value));
      move(
        clamp((event.clientX - (box.left + box.width / 2)) / (window.innerWidth / 2)),
        clamp((event.clientY - (box.top + box.height / 2)) / (window.innerHeight / 2)),
      );
    });
    hero.addEventListener("pointerleave", () => move(0, 0));
  }

  // Sample email: switch edition, and follow topics to see them move to the top and expand.
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
    const stories = $$(".m-story", list);
    const before = new Map(stories.map((story) => [story, story.getBoundingClientRect().top]));
    const ordered = stories.slice().sort(
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
          duration: 520,
          easing: "cubic-bezier(0.2, 0.8, 0.2, 1)",
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
})();
