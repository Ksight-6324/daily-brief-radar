"use strict";

const byId = (id) => document.getElementById(id);
const route = window.location.pathname;
const fragmentToken = window.location.hash.slice(1);
let manageToken = sessionStorage.getItem("brief_manage_token") || "";
let pollingTimer;
let qrObjectUrl;

if (route !== "/" && fragmentToken) {
  history.replaceState(null, "", route);
  if (route !== "/confirm") {
    manageToken = fragmentToken;
    sessionStorage.setItem("brief_manage_token", manageToken);
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
    throw new Error(detail);
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
      return label;
    });
    form.querySelector("[data-topics]").replaceChildren(...labels);
    form.addEventListener("change", () => updateTopicCount(form));
    for (const button of form.querySelectorAll("[data-topic-action]")) {
      button.addEventListener("click", () => {
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
  for (const plan of options.plans) {
    const amount = `¥${plan.amount / 100}`;
    const card = document.createElement("article");
    card.className = "plan";
    const title = document.createElement("h3");
    title.textContent = plan.label;
    const price = document.createElement("p");
    price.className = "price";
    price.textContent = amount;
    const duration = document.createElement("p");
    duration.textContent = `${plan.days} 天 · 手动续费`;
    const button = document.createElement("button");
    button.className = "text-button";
    button.dataset.plan = plan.id;
    button.textContent = `选择${plan.label}`;
    card.append(title, price, duration, button);
    byId("plan-options").append(card);
    const renewal = button.cloneNode(true);
    renewal.className = "button secondary";
    renewal.textContent = `${plan.label} ${amount}`;
    byId("renewal-plan-options").append(renewal);
  }
}

function showPanel(name) {
  for (const id of [
    "landing",
    "confirmation-panel",
    "management-panel",
    "unsubscribe-panel",
  ]) {
    byId(id).hidden = id !== name;
  }
}

async function loadAccount() {
  try {
    const account = await api("/api/account");
    byId("account-email").textContent = account.email;
    let summary = `免费试读剩余 ${account.trial_remaining} 期。`;
    if (account.expires_at) {
      const expiry = new Date(account.expires_at);
      summary = `订阅有效期至 ${expiry.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short" })}（北京时间）。`;
    }
    if (account.status === "unsubscribed")
      summary += " 当前已停止接收，请回到首页重新申请并确认邮箱以恢复。";
    if (account.status === "expired")
      summary += " 订阅已暂停，完成续费后恢复。";
    if (!account.confirmed) summary = "请先在确认邮件中验证邮箱。";
    byId("account-summary").textContent = summary;
    for (const input of byId("preferences-form").querySelectorAll(
      'input[name="preferences"]',
    )) {
      input.checked = account.preferences.includes(input.value);
    }
    updateTopicCount(byId("preferences-form"));
    return account;
  } catch (error) {
    feedback("management-feedback", error.message, true);
    byId("account-summary").textContent = "请使用最新邮件中的管理链接进入。";
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
    sessionStorage.setItem("brief_manage_token", manageToken);
    history.replaceState(null, "", "/manage");
    showPanel("management-panel");
    feedback("management-feedback", "邮箱已确认，三期免费试读已开启。");
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
  } catch (error) {
    feedback("management-feedback", error.message, true);
  } finally {
    button.disabled = false;
  }
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
    byId("payment-amount").textContent =
      `订单金额 ¥${(order.amount / 100).toFixed(2)}，收款码 30 分钟内有效。`;
    byId("payment-link").href = order.payment_url;
    byId("payment-panel").hidden = false;
    byId("payment-status").textContent =
      "请用支付宝扫码支付，完成后在这里自动确认。";
    feedback("management-feedback", "订单已创建。");
    byId("payment-panel").scrollIntoView({ block: "center" });
    clearTimeout(pollingTimer);
    pollPayment(order.order_token, Date.now() + 30 * 60 * 1000);
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
} else if (route === "/manage") {
  showPanel("management-panel");
} else if (route === "/unsubscribe") {
  showPanel("unsubscribe-panel");
}

const optionsReady = loadOptions();
optionsReady
  .then(() => {
    if (route === "/manage") loadAccount();
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
