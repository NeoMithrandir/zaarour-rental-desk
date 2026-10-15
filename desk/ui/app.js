// Stefan's morning review screen.
(function () {
  let tab = "review", data = null, cursor = 0;
  const list = document.getElementById("list");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n ? "$" + Number(n).toLocaleString("en-CA") : "price n/a");
  const day = (s) => (s || "").slice(0, 10);
  // Landlords get discouraged after 2-3 weeks of few calls: an older ad is a warmer lead, not a stale one.
  const daysUp = (s) => {
    const d = Math.floor((Date.now() - Date.parse(s)) / 86400000);
    return isNaN(d) || d < 1 ? "" : ` <b class="${d >= 14 ? "warm" : ""}">(${d} days up)</b>`;
  };

  async function load() {
    const r = await fetch("/api/leads?tab=" + tab + "&phase=" + document.getElementById("phase").value);
    data = await r.json();
    document.querySelectorAll("#tabs button").forEach((b) => {
      b.classList.toggle("on", b.dataset.tab === tab);
      b.querySelector("span").textContent = data.counts[b.dataset.tab] || "";
    });
    render();
  }

  function centrisLink(l) {
    const q = encodeURIComponent('"' + (l.address || l.title) + '" centris OR realtor.ca');
    return "https://www.google.com/search?q=" + q;
  }

  function card(l, i) {
    const lang = l.lang || "fr";
    const followup = tab === "sent";
    const msg = followup ? l["followup_" + lang] : l.message || l["message_" + lang];
    const flags = l.flags.map((f) => `<span class="flag ${f.level}" title="${esc(f.text)}">${esc(f.text)}</span>`).join("");
    const approved = l.status === "approved";
    let actions;
    if (tab === "sent") {
      actions = `<span class="muted">Sent ${day(l.sent_at)}${l.outcome ? " · " + esc(l.outcome) : ""}</span>
        <button data-act="copy">Copy follow-up</button>
        <button data-act="replied">Replied</button><button data-act="presentation">Presentation booked</button>
        <button data-act="listing" class="primary">Listing signed</button><button data-act="no_reply">No reply</button>`;
    } else if (approved) {
      actions = `<button data-act="open">Open ad again</button><button data-act="sent" class="primary">Mark sent</button><button data-act="undo">Undo</button>`;
    } else if (["rejected", "later", "hidden"].includes(tab)) {
      actions = `<button data-act="undo">Back to review</button>` + (l.reject_reason ? ` <span class="muted">${esc(l.reject_reason)}</span>` : "");
    } else {
      actions = `<button data-act="approve" class="primary">Approve: copy message and open ad</button>
        <select data-act="reject"><option value="">Reject…</option>${data.reasons.map((r) => `<option>${esc(r)}</option>`).join("")}</select>
        <button data-act="later">Later</button>`;
    }
    return `<article class="lead ${i === cursor ? "cur" : ""} ${approved ? "approved" : ""}" data-i="${i}">
      ${l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy">` : `<div class="noimg"></div>`}
      <div class="body">
        <div class="top"><a href="${esc(l.url)}" target="_blank" rel="noopener" class="title">${esc(l.title)}</a>
          <span class="price">${money(l.price)}</span></div>
        <div class="meta">${esc(l.area || "?")}${l.phase === 2 ? " (phase 2)" : ""} · ${esc(l.address)} · ${esc(l.source)} · posted ${day(l.posted_at)}${daysUp(l.posted_at)}
          · ${l.poster_type === "owner" ? "owner" : esc(l.poster_type || "poster ?")}
          · <a href="${centrisLink(l)}" target="_blank" rel="noopener">check Centris / Realtor</a></div>
        <div class="flags">${flags}</div>
        <p class="snippet">${esc(l.snippet)}</p>
        ${l.others && l.others.length ? `<details class="others"><summary>+${l.others.length} more ad${l.others.length > 1 ? "s" : ""} from this poster (one message covers them)</summary>
          <ul>${l.others.map((o) => `<li><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a> · ${esc(o.address)} · ${money(o.price)}</li>`).join("")}</ul></details>` : ""}
        ${["review", "broker"].includes(tab) || followup ? `
        <div class="msg">
          <div class="msgbar"><label><input type="radio" name="lang${i}" value="fr" ${lang === "fr" ? "checked" : ""}> FR</label>
          <label><input type="radio" name="lang${i}" value="en" ${lang === "en" ? "checked" : ""}> EN</label>
          <span class="count"></span></div>
          <textarea rows="7">${esc(msg)}</textarea></div>` : ""}
        <div class="actions">${actions}</div>
      </div></article>`;
  }

  function render() {
    if (!data.leads.length) {
      list.innerHTML = `<p class="empty">Nothing here. ${tab === "review" ? "Run the morning fetch to get today's ads." : ""}</p>`;
      return;
    }
    cursor = Math.min(cursor, data.leads.length - 1);
    list.innerHTML = data.leads.map(card).join("");
    list.querySelectorAll("article").forEach(wire);
  }

  function count(el) {
    const ta = el.querySelector("textarea"), c = el.querySelector(".count");
    if (!ta || !c) return;
    const n = ta.value.length;
    c.textContent = n + " / " + data.limit + " characters";
    c.classList.toggle("over", n > data.limit);
  }

  async function decide(l, body) {
    await fetch("/api/lead/" + encodeURIComponent(l.id) + "/decide", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { return false; }
  }

  function wire(el) {
    const l = data.leads[+el.dataset.i];
    const ta = el.querySelector("textarea");
    count(el);
    if (ta) ta.addEventListener("input", () => count(el));
    el.querySelectorAll('input[type=radio]').forEach((r) => r.addEventListener("change", () => {
      ta.value = l[(tab === "sent" ? "followup_" : "message_") + r.value];
      count(el);
    }));
    el.addEventListener("click", () => { cursor = +el.dataset.i; mark(); });
    el.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const act = b.dataset.act;
      if (act === "approve") {
        if (ta.value.length > data.limit && !confirm("The message is longer than the platform limit. Approve anyway?")) return;
        await copy(ta.value);
        window.open(l.url, "_blank", "noopener");
        await decide(l, { action: "approve", message: ta.value });
      } else if (act === "open") {
        await copy(l.message || (ta && ta.value) || "");
        window.open(l.url, "_blank", "noopener");
        return;
      } else if (act === "copy") {
        await copy(ta.value); b.textContent = "Copied"; window.open(l.url, "_blank", "noopener"); return;
      } else {
        await decide(l, { action: act });
      }
      load();
    }));
    const sel = el.querySelector("select[data-act=reject]");
    if (sel) sel.addEventListener("change", async () => { if (sel.value) { await decide(l, { action: "reject", reason: sel.value }); load(); } });
  }

  function mark() {
    list.querySelectorAll("article").forEach((a) => a.classList.toggle("cur", +a.dataset.i === cursor));
    const cur = list.querySelector("article.cur");
    if (cur) cur.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  document.addEventListener("keydown", (e) => {
    if (["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName) || !data || !data.leads.length) return;
    const cur = list.querySelector("article.cur");
    if (e.key === "j") { cursor = Math.min(cursor + 1, data.leads.length - 1); mark(); }
    else if (e.key === "k") { cursor = Math.max(cursor - 1, 0); mark(); }
    else if (e.key === "a" && cur) { const b = cur.querySelector('[data-act=approve]') || cur.querySelector('[data-act=sent]'); if (b) b.click(); }
    else if (e.key === "l" && cur) { const b = cur.querySelector('[data-act=later]'); if (b) b.click(); }
    else if (e.key === "r" && cur) { const s = cur.querySelector('select[data-act=reject]'); if (s) s.focus(); }
  });

  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => { tab = b.dataset.tab; cursor = 0; load(); }));
  document.getElementById("phase").addEventListener("change", () => { cursor = 0; load(); });

  document.getElementById("manual").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    await fetch("/api/manual", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    e.target.reset();
    tab = "review"; load();
  });

  load();
})();
