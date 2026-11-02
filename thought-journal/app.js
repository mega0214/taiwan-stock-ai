"use strict";
const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { localStorage.setItem(k, JSON.stringify(v)); },
};

let thoughts = store.get("thoughts", []);
let settings = store.get("settings", { apiKey: "", model: "claude-sonnet-5-5" });
const save = () => store.set("thoughts", thoughts);

const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function render() {
  const q = $("search").value.trim().toLowerCase();
  const items = thoughts.filter((t) => !q || t.text.toLowerCase().includes(q) || t.tags.some((g) => g.toLowerCase().includes(q)));
  $("list").innerHTML = items.map((t) => `
    <article class="item" data-id="${t.id}">
      <div class="meta">${new Date(t.created).toLocaleString("zh-TW")}</div>
      <p>${esc(t.text)}</p>
      <div>${t.tags.map((g) => `<span class="tag">${esc(g)}</span>`).join("")}</div>
      <div class="actions">
        <button data-act="analyze">${t.analysis ? "重新分析" : "分析可能性"}</button>
        <button data-act="delete">刪除</button>
      </div>
      ${t.analysis ? `<div class="analysis">${esc(t.analysis)}</div>` : ""}
    </article>`).join("") || '<p class="meta">還沒有想法，先記一則吧。</p>';
}

$("addBtn").onclick = () => {
  const text = $("text").value.trim();
  if (!text) return;
  const tags = $("tags").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  thoughts.unshift({ id: crypto.randomUUID(), text, tags, created: Date.now(), analysis: "" });
  save();
  $("text").value = ""; $("tags").value = "";
  render();
};
$("search").oninput = render;

$("list").onclick = async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const t = thoughts.find((x) => x.id === btn.closest(".item").dataset.id);
  if (btn.dataset.act === "delete") {
    if (confirm("確定刪除這則想法？")) { thoughts = thoughts.filter((x) => x !== t); save(); render(); }
  } else {
    btn.disabled = true; btn.textContent = "分析中…";
    try { t.analysis = await analyze(t); } catch (err) { t.analysis = "分析失敗：" + err.message; }
    save(); render();
  }
};

const PROMPT = (text) => `以下是使用者的一則想法。請用繁體中文分析，格式如下：
1. 背後的假設（列出 2-4 點）
2. 3-5 種可能的發展路徑（每種寫：做法、好處、風險）
3. 最好與最壞的情況
4. 一個本週就能做的小實驗
請保持中立、不要只迎合使用者。

想法：
${text}`;

async function analyze(t) {
  if (!settings.apiKey) {
    return "【示範內容：尚未設定 API key】\n1. 假設：你認為這件事值得投入時間。\n2. 路徑 A：立刻小規模嘗試；路徑 B：先蒐集資訊；路徑 C：暫時擱置並設定回顧日。\n3. 最好：驗證成功；最壞：浪費少量時間。\n4. 小實驗：用 30 分鐘寫下三個最小可行步驟。";
  }
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": settings.apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({ model: settings.model, max_tokens: 1500, messages: [{ role: "user", content: PROMPT(t.text) }] }),
  });
  if (!res.ok) throw new Error(res.status + " " + (await res.text()).slice(0, 200));
  const data = await res.json();
  return data.content.map((b) => b.text || "").join("");
}

// 設定
$("settingsBtn").onclick = () => { $("apiKey").value = settings.apiKey; $("model").value = settings.model; $("settings").showModal(); };
$("saveSettings").onclick = () => { settings = { apiKey: $("apiKey").value.trim(), model: $("model").value.trim() || "claude-sonnet-5-5" }; store.set("settings", settings); };
$("exportBtn").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(thoughts, null, 2)], { type: "application/json" }));
  a.download = "thoughts.json"; a.click();
};
$("importFile").onchange = async (e) => {
  try {
    const list = JSON.parse(await e.target.files[0].text());
    const ids = new Set(thoughts.map((t) => t.id));
    thoughts = [...thoughts, ...list.filter((t) => t.id && !ids.has(t.id))].sort((a, b) => b.created - a.created);
    save(); render();
  } catch { alert("匯入失敗：檔案格式不正確"); }
};

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
render();
