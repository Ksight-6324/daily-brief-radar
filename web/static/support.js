"use strict";

// Payment codes are uploaded to the server separately. A missing image hides itself instead of showing a broken icon.
function watchCode(id) {
  const image = document.getElementById(id);
  return new Promise((resolve) => {
    const done = (ok) => {
      if (!ok) image.closest("figure").hidden = true;
      resolve(ok);
    };
    if (image.complete) done(image.naturalWidth > 0);
    else {
      image.addEventListener("load", () => done(true), { once: true });
      image.addEventListener("error", () => done(false), { once: true });
    }
  });
}

Promise.all([watchCode("tip-alipay"), watchCode("tip-wechat")]).then((shown) => {
  document.getElementById("tip-codes-missing").hidden = shown.some(Boolean);
});

function yuan(fen) {
  return `¥${(fen / 100).toFixed(fen % 100 ? 2 : 0)}`;
}

(async () => {
  const list = document.getElementById("leaderboard");
  const message = (text) => {
    const item = document.createElement("li");
    item.className = "leaderboard-empty";
    item.textContent = text;
    list.replaceChildren(item);
  };
  try {
    const response = await fetch("/api/leaderboard");
    if (!response.ok) throw new Error("leaderboard");
    const { tippers } = await response.json();
    if (!tippers.length) return message("榜首还空着，等你来坐。");
    list.replaceChildren(
      ...tippers.map((tipper) => {
        const item = document.createElement("li");
        item.className = tipper.rank <= 3 ? "leader top" : "leader";
        const rank = document.createElement("span");
        rank.className = "leader-rank";
        rank.textContent = String(tipper.rank).padStart(2, "0");
        const name = document.createElement("span");
        name.className = "leader-name";
        name.textContent = tipper.name;
        const total = document.createElement("span");
        total.className = "leader-total";
        total.textContent = tipper.times > 1 ? `${yuan(tipper.total)} · ${tipper.times} 次` : yuan(tipper.total);
        item.append(rank, name, total);
        return item;
      }),
    );
  } catch {
    message("榜单暂时无法读取，请刷新页面。");
  }
})();
