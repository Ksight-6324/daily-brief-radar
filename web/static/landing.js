"use strict";

// Landing page motion. Nothing here talks to the server. Without JS, or with reduced motion,
// the page is a sequence of still, readable screens.
(() => {
  const landing = document.getElementById("landing");
  if (!landing) return;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
  const onLanding = () => !landing.hidden;
  // Progress of a tall pinned section: 0 when its top reaches the viewport top, 1 at its end.
  const pinProgress = (section) => {
    const box = section.getBoundingClientRect();
    return clamp(-box.top / Math.max(1, box.height - window.innerHeight));
  };

  // ---- Today's date in the dateline, Beijing time ---------------------------------------------
  const today = document.getElementById("today");
  if (today) {
    try {
      today.textContent = new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "long",
        day: "numeric",
        weekday: "long",
      }).format(new Date());
    } catch {
      /* keep the static text */
    }
  }

  // ---- Smooth wheel scrolling ------------------------------------------------------------------
  // Mouse-wheel steps are eased toward their target. Keyboard, scrollbar, touch, Ctrl+wheel zoom
  // and inner scrollable areas keep the browser's own behaviour.
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
      current += (target - current) * 0.1;
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
    window.addEventListener(
      "scroll",
      () => {
        if (!running) target = current = window.scrollY;
      },
      { passive: true },
    );
  }

  // ---- One scroll frame drives every scroll-linked effect --------------------------------------
  const bar = document.createElement("div");
  bar.className = "scroll-bar";
  bar.setAttribute("aria-hidden", "true");
  document.body.prepend(bar);
  const frameHandlers = [];
  let queued = false;
  function onFrame() {
    queued = false;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    bar.hidden = !onLanding();
    bar.style.setProperty("--p", max > 0 ? clamp(window.scrollY / max).toFixed(4) : "0");
    for (const handler of frameHandlers) handler();
  }
  const requestFrame = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(onFrame);
  };
  window.addEventListener("scroll", requestFrame, { passive: true });
  window.addEventListener("resize", requestFrame);

  // ---- Opening: the headline is forgotten as you scroll, the answer takes its place -------------
  const opening = $(".opening");
  const title = $(".opening-title");
  const chars = [];
  if (title) {
    title.setAttribute("aria-label", title.textContent.replace(/\s+/g, ""));
    for (const line of $$("[data-split]", title)) {
      const text = Array.from(line.textContent.trim());
      line.textContent = "";
      line.setAttribute("aria-hidden", "true");
      text.forEach((character, n) => {
        const outer = document.createElement("span");
        outer.className = "ch";
        const inner = document.createElement("i");
        inner.textContent = character;
        inner.style.setProperty("--n", String(n));
        outer.append(inner);
        line.append(outer);
        chars.push({ outer, inner, character });
      });
    }
  }
  if (opening && !reduceMotion && chars.length) {
    opening.classList.add("is-live");
    const random = (low, high) => low + Math.random() * (high - low);
    // Each character drifts off in its own direction; "忘" goes first.
    const drift = chars.map(({ character }) => ({
      dx: random(-0.32, 0.32),
      dy: random(-0.42, -0.08),
      rot: random(-50, 50),
      start: character === "忘" ? 0 : random(0.02, 0.22),
    }));
    frameHandlers.push(() => {
      const p = pinProgress(opening);
      const w = window.innerWidth;
      const h = window.innerHeight;
      chars.forEach(({ outer }, i) => {
        const t = clamp((p - 0.03 - drift[i].start) / 0.3);
        const eased = t * t * (3 - 2 * t);
        outer.style.transform = t
          ? `translate(${(drift[i].dx * w * eased).toFixed(1)}px, ${(drift[i].dy * h * eased).toFixed(1)}px) rotate(${(drift[i].rot * eased).toFixed(1)}deg)`
          : "";
        outer.style.opacity = t ? String(1 - eased) : "";
        outer.style.filter = t ? `blur(${(eased * 14).toFixed(1)}px)` : "";
      });
      const answer = clamp((p - 0.42) / 0.3);
      opening.style.setProperty("--a", answer.toFixed(3));
      opening.style.setProperty("--f", (1 - clamp(p / 0.2)).toFixed(3));
      opening.classList.toggle("answer-on", answer > 0.6);
    });

    // Pointer: characters near the cursor shy away and blur, as if half-remembered.
    if (finePointer) {
      const stage = $(".opening-stage", opening);
      let pointer = null;
      let pending = false;
      const push = () => {
        pending = false;
        for (const { inner } of chars) {
          if (!pointer) {
            inner.style.removeProperty("--hx");
            inner.style.removeProperty("--hy");
            inner.style.removeProperty("--hb");
            continue;
          }
          const box = inner.getBoundingClientRect();
          const dx = box.left + box.width / 2 - pointer.x;
          const dy = box.top + box.height / 2 - pointer.y;
          const distance = Math.hypot(dx, dy) || 1;
          const force = clamp(1 - distance / 170);
          inner.style.setProperty("--hx", `${((dx / distance) * force * 34).toFixed(1)}px`);
          inner.style.setProperty("--hy", `${((dy / distance) * force * 34).toFixed(1)}px`);
          inner.style.setProperty("--hb", `${(force * 5).toFixed(1)}px`);
        }
      };
      const schedule = () => {
        if (pending) return;
        pending = true;
        requestAnimationFrame(push);
      };
      stage.addEventListener("pointermove", (event) => {
        pointer = { x: event.clientX, y: event.clientY };
        schedule();
      });
      stage.addEventListener("pointerleave", () => {
        pointer = null;
        schedule();
      });
    }
  }

  // ---- Pages: large words rise out of a mask, small text follows -------------------------------
  if (!reduceMotion && "IntersectionObserver" in window) {
    const show = (node) => {
      node.classList.add("in");
      node.classList.remove("will-fade");
    };
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          observer.unobserve(entry.target);
          show(entry.target);
        }
      },
      { threshold: 0.35 },
    );
    for (const big of $$(".page-big, .split-right", landing)) {
      $$(":scope > span", big).forEach((span, n) => {
        const inner = document.createElement("i");
        inner.append(...span.childNodes);
        inner.style.setProperty("--n", String(n));
        span.append(inner);
      });
      big.classList.add("will-rise");
      observer.observe(big);
    }
    for (const small of $$(".page .page-small, .page .page-no, .page-topics, #landing section > .page-small", landing)) {
      small.classList.add("fade-late", "will-fade");
      observer.observe(small);
    }
  }

  // ---- Three must-knows: the title settles, the three items fly in one by one, then slide away --
  const three = $(".three");
  if (three && !reduceMotion) {
    three.classList.add("is-live");
    const items = $$(".three-list li", three);
    frameHandlers.push(() => {
      const p = pinProgress(three);
      three.style.setProperty("--ti", clamp((p + 0.02) / 0.1).toFixed(3));
      three.style.setProperty("--to", clamp((p - 0.88) / 0.1).toFixed(3));
      items.forEach((item, i) => {
        item.style.setProperty("--in", clamp((p - 0.1 - i * 0.13) / 0.14).toFixed(3));
        item.style.setProperty("--out", clamp((p - 0.66 - i * 0.07) / 0.14).toFixed(3));
      });
    });
  }

  // ---- Anatomy: the email builds itself part by part as you scroll -----------------------------
  const anatomy = $(".anatomy");
  if (anatomy && !reduceMotion) {
    anatomy.classList.add("is-live");
    const parts = $$(".em-part", anatomy);
    const mail = $(".anatomy-mail", anatomy);
    const view = $(".anatomy-window", anatomy);
    const capBig = $(".cap-big", anatomy);
    const capSmall = $(".cap-small", anatomy);
    const count = $(".cap-count b", anatomy);
    const intro = { big: capBig.innerHTML, small: capSmall.textContent };
    const total = parts.length;
    $(".cap-count", anatomy).lastChild.textContent = ` / ${String(total).padStart(2, "0")}`;
    let shown = -2;
    let centers = [];

    const measure = () => {
      centers = parts.map((part) => {
        let top = 0;
        for (let el = part; el && el !== mail; el = el.offsetParent) top += el.offsetTop;
        return top + part.offsetHeight / 2;
      });
    };
    const caption = (index) => {
      if (index === shown) return;
      shown = index;
      if (index < 0) {
        capBig.innerHTML = intro.big;
        capSmall.textContent = intro.small;
      } else {
        capBig.textContent = parts[index].dataset.cap;
        capSmall.textContent = parts[index].dataset.note;
      }
      for (const node of [capBig, capSmall]) {
        node.classList.remove("swap");
        void node.offsetWidth; // restart the swap animation
        node.classList.add("swap");
      }
      count.textContent = String(Math.max(0, index + 1)).padStart(2, "0");
      parts.forEach((part, i) => part.classList.toggle("is-active", i === index));
    };

    frameHandlers.push(() => {
      const p = pinProgress(anatomy);
      // Leave a short lead-in before the first part and a pause after the last one.
      const units = clamp((p - 0.04) / 0.86) * total;
      parts.forEach((part, i) => part.style.setProperty("--t", clamp((units - i) / 0.65).toFixed(3)));
      const active = units < 0.15 ? -1 : Math.min(total - 1, Math.floor(units));
      caption(active);
      // Camera: keep the part being built in the middle of the window.
      const index = Math.min(total - 1, Math.max(0, Math.floor(units)));
      const next = Math.min(total - 1, index + 1);
      const focus = centers[index] + (centers[next] - centers[index]) * clamp(units - index);
      const room = view.clientHeight;
      const cam = clamp(focus - room * 0.5, 0, Math.max(0, mail.offsetHeight - room));
      mail.style.setProperty("--cam", `${(-cam).toFixed(1)}px`);
    });

    window.addEventListener("resize", () => {
      measure();
      requestFrame();
    });
    measure();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => {
      measure();
      requestFrame();
    });
  }

  onFrame();
})();
