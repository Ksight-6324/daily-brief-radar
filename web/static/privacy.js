"use strict";

// The operator's contact details come from the server configuration, so the policy never ships a made-up address.
(async () => {
  const line = document.getElementById("contact-line");
  try {
    const response = await fetch("/api/options");
    const { contact } = await response.json();
    if (contact && contact.operator)
      document.getElementById("contact-operator").textContent =
        `运营者：${contact.operator}。`;
    if (contact && contact.email) {
      const link = document.createElement("a");
      link.href = `mailto:${contact.email}`;
      link.textContent = contact.email;
      line.replaceChildren("隐私相关请求请发送邮件至 ", link, "。");
    } else {
      line.textContent = "运营者尚未配置联系邮箱。";
    }
  } catch {
    line.textContent = "联系方式暂时无法读取，请刷新页面。";
  }
})();
