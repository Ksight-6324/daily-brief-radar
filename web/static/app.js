"use strict";

const byId = (id) => document.getElementById(id);
const route = window.location.pathname;
const fragmentToken = window.location.hash.slice(1);
const TOKEN_KEY = "brief_manage_token";

// The login link stays on this device until the reader signs out. Storage can be blocked, so never rely on it.
function readToken() {
  try {
    return (
      localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) || ""
    );
  } catch {
    return "";
  }
}

function saveToken(value) {
  try {
    localStorage.setItem(TOKEN_KEY, value);
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* the session simply ends with this tab */
  }
}

function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing stored */
  }
}

let manageToken = readToken();
let feedScope = "followed";
let pollingTimer;
let qrObjectUrl;

if (route !== "/" && fragmentToken) {
  history.replaceState(null, "", route);
  if (route !== "/confirm") {
    manageToken = fragmentToken;
    saveToken(manageToken);
  }
}

function feedback(id, message, error = false) {
  const node = byId(id);
  node.textContent = message;
  node.classList.toggle("error", error);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (manageToken) headers.Authorization = `Bearer ${manageToken}`;
  const response = await fetch(path, { ...options, headers });
  const result = await response.json();
  if (!response.ok) {
    const detail =
      typeof result.detail === "string"
        ? result.detail
        : "请检查填写内容后重试。";
    const failure = new Error(detail);
    failure.status = response.status;
    throw failure;
  }
  return result;
}

function selectedPreferences(form) {
  return Array.from(
    form.querySelectorAll('input[name="preferences"]:checked'),
    (input) => input.value,
  );
}

function updateTopicCount(form) {
  const total = form.querySelectorAll('input[name="preferences"]').length;
  form.querySelector("[data-topic-count]").textContent =
    `已选 ${selectedPreferences(form).length} / ${total}`;
}

// The signup form leads with a few popular topics so a new reader is not asked to do homework;
// the rest sit behind one tap, and any topic already chosen always stays visible.
const FEATURED_TOPICS = ["markets", "ai", "jobs", "politics_mil", "growth", "sports_health"];

function setTopicsExpanded(form, expanded) {
  const grid = form.querySelector("[data-topics]");
  grid.dataset.collapsed = String(!expanded);
  const toggle = form.querySelector('[data-topic-action="more"]');
  if (toggle) {
    toggle.setAttribute("aria-expanded", String(expanded));
    const extra = grid.querySelectorAll(".topic-extra").length;
    toggle.textContent = expanded ? "收起" : `更多主题 +${extra}`;
  }
}

async function loadOptions() {
  const options = await api("/api/options");
  for (const form of [byId("subscribe-form"), byId("preferences-form")]) {
    const labels = options.topics.map((topic) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.name = "preferences";
      checkbox.value = topic.id;
      label.append(checkbox, document.createTextNode(topic.label));
      if (form.querySelector('[data-topic-action="more"]') && !FEATURED_TOPICS.includes(topic.id))
        label.classList.add("topic-extra");
      return label;
    });
    form.querySelector("[data-topics]").replaceChildren(...labels);
    form.addEventListener("change", () => updateTopicCount(form));
    if (form.querySelector('[data-topic-action="more"]')) setTopicsExpanded(form, false);
    for (const button of form.querySelectorAll("[data-topic-action]")) {
      button.addEventListener("click", () => {
        if (button.dataset.topicAction === "more") {
          setTopicsExpanded(form, form.querySelector("[data-topics]").dataset.collapsed === "true");
          return;
        }
        for (const checkbox of form.querySelectorAll(
          'input[name="preferences"]',
        )) {
          checkbox.checked = button.dataset.topicAction === "all";
        }
        updateTopicCount(form);
      });
    }
    form.querySelector('button[type="submit"]').disabled = false;
    updateTopicCount(form);
  }
  // Reading is free and supported by tips. Paid renewal appears only if the server opens payments again.
  byId("renewal").hidden = !options.payments;
  if (!options.payments) return;
  for (const plan of options.plans) {
    const button = document.createElement("button");
    button.className = "button secondary";
    button.dataset.plan = plan.id;
    button.textContent = `${plan.label} ¥${plan.amount / 100}`;
    byId("renewal-plan-options").append(button);
  }
}

function showPanel(name) {
  for (const id of [
    "landing",
    "confirmation-panel",
    "login-panel",
    "management-panel",
    "delete-panel",
    "unsubscribe-panel",
  ]) {
    byId(id).hidden = id !== name;
  }
}

function nextDelivery() {
  const hour = Number(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Shanghai",
      hour: "numeric",
      hourCycle: "h23",
    }).format(new Date()),
  );
  if (hour < 8) return "今天 08:00 早报";
  if (hour < 20) return "今天 20:00 晚报";
  return "明天 08:00 早报";
}

function formatExpiry(value) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function describeAccount(account) {
  if (!account.confirmed) return "请先在确认邮件中验证邮箱。";
  if (account.status === "unsubscribed")
    return "当前已停止接收。想恢复，请回到首页重新申请并在邮件中确认邮箱。";
  const next = nextDelivery();
  if (account.delivery_mode === "paid")
    return `订阅有效期至 ${formatExpiry(account.expires_at)}（北京时间）。下一封：${next}。`;
  if (account.delivery_mode === "grace")
    return `订阅已于 ${formatExpiry(account.expires_at)} 到期，三天宽限期内仍会收到简报，请尽快续费。下一封：${next}。`;
  if (account.delivery_mode === "free")
    return `免费订阅中，每天早晚各一封。下一封：${next}。`;
  if (account.delivery_mode === "trial")
    return `免费试读还剩 ${account.trial_remaining} 封。下一封：${next}。`;
  if (account.expires_at) return "订阅已暂停，完成续费后恢复每日两封。";
  return "免费试读已用完，续费后继续每天收到两封简报。";
}

function showLogin(message, error = false) {
  clearToken();
  manageToken = "";
  history.replaceState(null, "", "/login");
  showPanel("login-panel");
  if (message) feedback("login-feedback", message, error);
}

async function loadAccount() {
  try {
    const account = await api("/api/account");
    byId("account-email").textContent = account.email;
    byId("account-summary").textContent = describeAccount(account);
    for (const input of byId("preferences-form").querySelectorAll(
      'input[name="preferences"]',
    )) {
      input.checked = account.preferences.includes(input.value);
    }
    updateTopicCount(byId("preferences-form"));
    byId("feed-section").hidden = !account.confirmed;
    if (account.confirmed) await loadFeed();
    return account;
  } catch (error) {
    if (error.status === 401) {
      showLogin("登录已失效，请重新获取登录链接。", true);
      return;
    }
    feedback("management-feedback", error.message, true);
    byId("account-summary").textContent = "暂时无法读取订阅信息，请稍后刷新。";
  }
}

function storyNode(story) {
  const card = document.createElement("article");
  card.className = story.followed ? "story followed" : "story";
  const label = document.createElement("span");
  label.className = "topic-label";
  label.textContent = story.label;
  const title = document.createElement("h4");
  title.textContent = story.title;
  card.append(label, title);
  if (story.summary) {
    const summary = document.createElement("p");
    summary.textContent = story.summary;
    card.append(summary);
  }
  if (story.importance) {
    const why = document.createElement("p");
    why.className = "story-why";
    const strong = document.createElement("strong");
    strong.textContent = "为什么重要 ";
    why.append(strong, document.createTextNode(story.importance));
    card.append(why);
  }
  const sourceName = story.source || "原始来源";
  if (story.url) {
    const link = document.createElement("a");
    link.href = story.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `阅读来源 · ${sourceName} ↗`;
    card.append(link);
  } else if (story.source) {
    const source = document.createElement("span");
    source.className = "source-label";
    source.textContent = story.source;
    card.append(source);
  }
  return card;
}

function emptyFeed(text) {
  const note = document.createElement("p");
  note.className = "form-note";
  note.textContent = text;
  return note;
}

async function loadFeed() {
  const container = byId("feed");
  for (const button of document.querySelectorAll("[data-scope]"))
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.scope === feedScope),
    );
  try {
    const feed = await api(`/api/feed?scope=${feedScope}`);
    const note = byId("feed-note");
    note.hidden = !feed.limited && !feed.general;
    note.textContent = feed.limited
      ? "当前只显示最新一期。续费后可回看近 7 天的全部期号，并继续每天收到两封邮件。"
      : "你还没有选择关注的主题，下面是完整的通用版。在下方“推送板块”里勾选后，这里和邮件都会按你的兴趣排列。";
    if (!feed.issues.length) {
      container.replaceChildren(emptyFeed("第一期简报发布后，会在这里显示。"));
      return;
    }
    const groups = feed.issues.map((issue) => {
      const group = document.createElement("section");
      group.className = "feed-issue";
      const heading = document.createElement("h3");
      heading.textContent = `${issue.date} · ${issue.edition_label}`;
      group.append(heading, ...issue.stories.map(storyNode));
      return group;
    });
    container.replaceChildren(...groups);
  } catch (error) {
    if (error.status === 401) {
      showLogin("登录已失效，请重新获取登录链接。", true);
      return;
    }
    container.replaceChildren(emptyFeed(error.message));
  }
}

byId("subscribe-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("subscribe-button");
  button.disabled = true;
  feedback("subscribe-feedback", "正在申请确认邮件…");
  try {
    const result = await api("/api/subscribe", {
      method: "POST",
      body: JSON.stringify({
        email: byId("email").value,
        preferences: selectedPreferences(event.currentTarget),
        consent: byId("consent").checked,
      }),
    });
    feedback("subscribe-feedback", result.message);
  } catch (error) {
    feedback("subscribe-feedback", error.message, true);
  } finally {
    button.disabled = false;
  }
});

byId("confirm-button").addEventListener("click", async () => {
  byId("confirm-button").disabled = true;
  try {
    await optionsReady;
    const result = await api("/api/confirm", {
      method: "POST",
      body: JSON.stringify({ token: fragmentToken }),
    });
    manageToken = result.manage_token;
    saveToken(manageToken);
    history.replaceState(null, "", "/manage");
    showPanel("management-panel");
    feedback("management-feedback", "邮箱已确认，每天 08:00 早报、20:00 晚报会送到你的邮箱。");
    await loadAccount();
  } catch (error) {
    feedback("confirm-feedback", error.message, true);
    byId("confirm-button").disabled = false;
  }
});

byId("preferences-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const result = await api("/api/preferences", {
      method: "POST",
      body: JSON.stringify({
        preferences: selectedPreferences(event.currentTarget),
      }),
    });
    feedback("management-feedback", result.message);
    await loadFeed();
  } catch (error) {
    feedback("management-feedback", error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-scope]");
  if (!tab || tab.dataset.scope === feedScope) return;
  feedScope = tab.dataset.scope;
  loadFeed();
});

byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("login-button");
  button.disabled = true;
  feedback("login-feedback", "正在发送登录链接…");
  try {
    const result = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ email: byId("login-email").value }),
    });
    feedback("login-feedback", result.message);
  } catch (error) {
    feedback("login-feedback", error.message, true);
  } finally {
    button.disabled = false;
  }
});

byId("logout-button").addEventListener("click", () => {
  showLogin("已退出登录。需要时可以再次获取登录链接。");
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-plan]");
  if (!button) return;
  if (!manageToken) {
    byId("email").scrollIntoView({ block: "center" });
    byId("email").focus({ preventScroll: true });
    feedback(
      "subscribe-feedback",
      "请先填写邮箱并完成确认，再从邮件中的管理链接选择套餐。",
    );
    return;
  }
  showPanel("management-panel");
  await loadAccount();
  button.disabled = true;
  feedback("management-feedback", "正在创建支付宝订单…");
  try {
    const order = await api("/api/orders", {
      method: "POST",
      body: JSON.stringify({ plan: button.dataset.plan }),
    });
    const qr = await fetch(
      `/api/orders/${encodeURIComponent(order.order_token)}/qr`,
      { headers: { Authorization: `Bearer ${manageToken}` } },
    );
    if (!qr.ok) throw new Error("收款码暂时无法显示，请重试。");
    if (qrObjectUrl) URL.revokeObjectURL(qrObjectUrl);
    qrObjectUrl = URL.createObjectURL(await qr.blob());
    byId("payment-qr").src = qrObjectUrl;
    const deadline = new Date(order.expires_at);
    const deadlineLabel = deadline.toLocaleTimeString("zh-CN", {
      timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit",
    });
    byId("payment-amount").textContent =
      `订单金额 ¥${(order.amount / 100).toFixed(2)}，收款码有效至北京时间 ${deadlineLabel}。`;
    byId("payment-link").href = order.payment_url;
    byId("payment-panel").hidden = false;
    byId("payment-status").textContent =
      "请用支付宝扫码支付，完成后在这里自动确认。";
    feedback("management-feedback", "订单已创建。");
    byId("payment-panel").scrollIntoView({ block: "center" });
    clearTimeout(pollingTimer);
    pollPayment(order.order_token, deadline.getTime());
  } catch (error) {
    feedback("management-feedback", error.message, true);
  } finally {
    button.disabled = false;
  }
});

function pollPayment(orderToken, until) {
  pollingTimer = setTimeout(async () => {
    try {
      const order = await api(`/api/orders/${encodeURIComponent(orderToken)}`);
      if (order.status === "paid") {
        byId("payment-status").textContent = "支付已确认，订阅有效期已更新。";
        await loadAccount();
        return;
      }
      if (Date.now() >= until) {
        byId("payment-status").textContent =
          "收款码已过有效期。若已付款，请稍后刷新订阅状态。";
        return;
      }
      pollPayment(orderToken, until);
    } catch (error) {
      byId("payment-status").textContent = error.message;
    }
  }, 5000);
}

byId("unsubscribe-link").addEventListener("click", (event) => {
  event.preventDefault();
  history.pushState(null, "", "/unsubscribe");
  showPanel("unsubscribe-panel");
});

byId("delete-link").addEventListener("click", (event) => {
  event.preventDefault();
  history.pushState(null, "", "/delete");
  showPanel("delete-panel");
});

byId("delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("delete-button");
  button.disabled = true;
  feedback("delete-feedback", "正在删除…");
  try {
    const result = await api("/api/account/delete", {
      method: "POST",
      body: JSON.stringify({ confirm_email: byId("delete-email").value }),
    });
    clearToken();
    manageToken = "";
    byId("delete-details").hidden = true;
    byId("delete-done").hidden = false;
    feedback("delete-feedback", result.message);
  } catch (error) {
    feedback("delete-feedback", error.message, true);
    button.disabled = false;
  }
});

byId("general-button").addEventListener("click", async () => {
  try {
    await api("/api/preferences", {
      method: "POST",
      body: JSON.stringify({ preferences: [] }),
    });
    feedback("unsubscribe-feedback", "已改为通用版排序，将继续接收简报。");
  } catch (error) {
    feedback("unsubscribe-feedback", error.message, true);
  }
});

byId("unsubscribe-button").addEventListener("click", async () => {
  byId("unsubscribe-button").disabled = true;
  try {
    const result = await api("/api/unsubscribe", { method: "POST" });
    feedback("unsubscribe-feedback", result.message);
    byId("general-button").hidden = true;
  } catch (error) {
    feedback("unsubscribe-feedback", error.message, true);
    byId("unsubscribe-button").disabled = false;
  }
});

if (route === "/confirm") {
  showPanel("confirmation-panel");
  if (!fragmentToken) {
    byId("confirm-button").disabled = true;
    feedback("confirm-feedback", "请打开确认邮件中的完整链接。", true);
  }
} else if (route === "/login") {
  showPanel("login-panel");
} else if (route === "/delete") {
  if (manageToken) showPanel("delete-panel");
  else showLogin("请登录后再删除账号。");
} else if (route === "/manage") {
  if (manageToken) showPanel("management-panel");
  else showLogin("请登录后查看你的主页。");
} else if (route === "/unsubscribe") {
  showPanel("unsubscribe-panel");
}

const optionsReady = loadOptions();
optionsReady
  .then(() => {
    if (route === "/manage" && manageToken) loadAccount();
  })
  .catch(() => {
    const message = "主题与套餐暂时无法读取，请刷新后重试。";
    feedback("subscribe-feedback", message, true);
    feedback("management-feedback", message, true);
  });

window.addEventListener("pagehide", () => {
  clearTimeout(pollingTimer);
  if (qrObjectUrl) URL.revokeObjectURL(qrObjectUrl);
});
