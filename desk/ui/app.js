// Stefan's morning review screen: one ad at a time (or a list with bulk actions), undo, and a To send queue.
// Nothing is ever sent from here: Approve copies the message and opens the ad; Stefan sends it on the board,
// where the "Fill from desk" bookmark puts the message in the board's own message box.
(function () {
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n ? "$" + Number(n).toLocaleString("en-CA") : "Rent not shown");
  const store = {
    get(k, d) { try { const v = localStorage.getItem("desk." + k); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("desk." + k, v); } catch (e) { /* private window */ } },
  };
  const SOURCES = { kijiji: "Kijiji", craigslist: "Craigslist", logisquebec: "LogisQuébec", livrent: "liv.rent", facebook: "Facebook Marketplace",
    group: "Facebook group", lespac: "LesPAC", rentalsca: "Rentals.ca", padmapper: "PadMapper / Zumper", appartogo: "Appartogo", louer: "Louer.ca", duproprio: "DuProprio", other: "Other" };
  const OUTCOMES = [["replied", "Replied"], ["declined", "Said no"], ["presentation", "Presentation booked"], ["listing", "Listing signed"], ["no_reply", "No reply"]];
  const TRIAGE = ["review", "broker", "later"];           // tabs that can be shown one at a time
  const SELECTABLE = ["review", "broker", "later", "rejected", "hidden"];

  let tab = "review", phase = store.get("phase", ""), data = null, cursor = 0;
  // One at a time on a phone; the list with bulk actions is a desk thing.
  let view = matchMedia("(max-width: 640px)").matches ? "card" : store.get("view", "card");
  let rejecting = false, selected = new Set(), lastClicked = null, pendingSend = null;
  const undoStack = [], edits = {}, drafting = {}, draftErr = {};
  const stage = $("#stage");

  // Landlords get discouraged after 2-3 weeks of few calls: an older ad is a warmer lead, not a stale one.
  const daysUp = (s) => { const d = Math.floor((Date.now() - Date.parse(s)) / 86400000); return isNaN(d) || d < 0 ? null : d; };
  const upLabel = (s) => { const d = daysUp(s); return d == null ? "" : d < 1 ? "Posted today" : d === 1 ? "Up 1 day" : `Up ${d} days`; };
  const isTriage = () => TRIAGE.includes(tab) && view === "card";

  // ------------------------------------------------------------ data
  async function load(keepCursor) {
    const r = await fetch(`/api/leads?tab=${tab}&phase=${phase}`);
    data = await r.json();
    if (!keepCursor) cursor = 0;
    selected.clear(); rejecting = false;
    renderChrome(); render();
  }

  async function refreshCounts() {
    const r = await fetch("/api/counts");
    const c = await r.json();
    data.counts = c.counts; data.today = c.today;
    renderChrome();
  }

  async function post(id, body) {
    const r = await fetch("/api/lead/" + encodeURIComponent(id) + "/decide", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error("save failed");
  }

  // ------------------------------------------------------------ chrome: tabs, filters, progress
  function renderChrome() {
    $$("#tabs button").forEach((b) => {
      b.classList.toggle("on", b.dataset.tab === tab);
      const n = data.counts[b.dataset.tab] || 0;
      b.querySelector("b").textContent = n ? n.toLocaleString("en-CA") : "";
    });
    $$("#phase button").forEach((b) => b.classList.toggle("on", b.dataset.phase === phase));
    $("#view").hidden = !TRIAGE.includes(tab);
    $$("#view button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
    const p = $("#progress");
    p.hidden = !["review", "broker"].includes(tab);
    if (!p.hidden) {
      const t = data.today || {}, left = data.leads.length;
      const done = (t.approved || 0) + (t.rejected || 0) + (t.later || 0);
      $("i", p).style.width = (done + left ? Math.round((100 * done) / (done + left)) : 100) + "%";
      const bits = [[t.approved, "approved"], [t.sent, "sent"], [t.rejected, "rejected"], [t.later, "for later"]].filter((x) => x[0]).map((x) => `${x[0]} ${x[1]}`);
      $("span", p).innerHTML = `<b>${left.toLocaleString("en-CA")} left</b>${phase ? ` in phase ${phase}` : ""}` + (bits.length ? ` · today ${bits.join(", ")}` : "");
    }
  }

  // ------------------------------------------------------------ pieces
  function msgFor(l) {
    const followup = tab === "sent";
    const e = edits[l.id] || {};
    const lang = e.lang || l.lang || "fr";
    const custom = !followup && !e.template && l.drafts && l.drafts[lang];
    const base = followup ? l["followup_" + lang] : (tab === "approved" && l.message && !e.lang ? l.message
      : custom ? custom.text : l["message_" + lang]);
    return { lang, text: e[lang] != null ? e[lang] : base, custom: !!custom && !(tab === "approved" && l.message && !e.lang) };
  }

  function flagsHTML(l) {
    const banners = [], chips = [];
    for (const f of l.flags) {
      if (f.level === "broker") banners.push(`<div class="warn bad">⚠ ${esc(f.text)}</div>`);
      else if (f.level === "hide") banners.push(`<div class="warn quiet"><strong>Hidden:</strong> ${esc(f.text)}</div>`);
      else if (f.code === "centris_unchecked" || f.code === "phase2" || f.code === "property_manager") continue; // shown elsewhere
      else if (f.level === "check") chips.push(`<span class="tag amber">${esc(f.text)}</span>`);
      else chips.push(`<span class="tag">${esc(f.text)}</span>`);
    }
    return { banners: banners.join(""), chips: chips.join("") };
  }

  function keyFacts(l) {
    const mgr = l.poster_type === "professional" || l.flags.some((f) => f.code === "property_manager");
    const who = l.poster_type === "owner" && !mgr ? `<span class="kf good">Owner</span>` : mgr ? `<span class="kf">Property manager</span>` : `<span class="kf">Poster unknown</span>`;
    const d = daysUp(l.posted_at);
    const up = upLabel(l.posted_at);
    return who + (up ? `<span class="kf ${d >= 14 ? "good" : ""}" title="Posted ${esc((l.posted_at || "").slice(0, 10))}${d >= 14 ? ". Older ads are warmer leads: the landlord is getting few calls." : ""}">${up}</span>` : "")
      + `<span class="kf">${esc(SOURCES[l.source] || l.source)}</span>` + (l.phase === 2 ? `<span class="kf">Phase 2</span>` : "");
  }

  // The landlord's number, kept only on boards whose permission covers contact details.
  function phoneLine(l) {
    if (!l.phone) return "";
    const nums = l.phone.split(", ").map((p) => `<a href="tel:+1${p.replace(/-/g, "")}">${esc(p)}</a>`).join(" · ");
    return `<p class="phone">☎ ${nums} <small class="muted">${l.phone_via === "revealed" ? "shown on the board" : "from the ad text"}</small></p>`;
  }

  function centrisLink(l) {
    return "https://www.google.com/search?q=" + encodeURIComponent('"' + (l.address || l.title) + '" centris OR realtor.ca');
  }

  function others(l) {
    if (!l.others || !l.others.length) return "";
    return `<details class="others"><summary>${l.others.length} more ad${l.others.length > 1 ? "s" : ""} from this poster. One message covers them.</summary>
      <ul>${l.others.map((o) => `<li><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a> <span class="muted">${esc(o.address)} · ${money(o.price)}</span></li>`).join("")}</ul></details>`;
  }

  const canDraft = () => data.drafting && data.drafting.on && !["sent", "approved"].includes(tab);

  function draftLine(l, m) {
    if (tab === "sent" || tab === "approved") return "";
    const dr = data.drafting || {}, key = l.id + ":" + m.lang;
    if (!dr.on) return `<p class="draftline muted" title="${esc(dr.problem || "")}">Standard template. Custom messages are off.</p>`;
    if (drafting[key]) return `<p class="draftline busy">Writing a message for this ad in Stefan's style…</p>`;
    if (draftErr[key]) return `<p class="draftline bad">${esc(draftErr[key])} <button class="linkish" data-draft="fresh">Try again</button></p>`;
    const d = l.drafts && l.drafts[m.lang];
    if (m.custom) return `<p class="draftline good">Written for this ad from ${dr.samples ? dr.samples + " sample message" + (dr.samples > 1 ? "s" : "") : "no samples yet"} (${esc(d.model)}).${approachNote(d)}
      <button class="linkish" data-draft="fresh" title="Write another, same approach (W)">Rewrite</button>${d.approach && (data.learning || {}).active > 1 ? ` · <button class="linkish" data-draft="other" title="Write it with a different way of opening">Try another approach</button>` : ""} · <button class="linkish" data-draft="template">Use the template</button></p>`;
    return `<p class="draftline muted">Standard template. ${d ? `<button class="linkish" data-draft="custom">Use the custom message</button> · ` : ""}<button class="linkish" data-draft="${d ? "fresh" : "write"}" title="(W)">Write one for this ad</button>${dr.problem ? ` <span title="${esc(dr.problem)}">ⓘ</span>` : ""}</p>`;
  }

  // Which way of opening this message uses, and how much the desk is leaning on results yet (Scorecard has the numbers).
  function approachNote(d) {
    const lr = data.learning || {};
    if (!d.approach || !lr.names || !lr.names[d.approach]) return "";
    const why = d.picked_by === "stefan" ? "you asked for a different one"
      : lr.phase === "leaning" ? "chosen from what landlords answer most"
      : "still testing the approaches evenly";
    const tip = lr.phase === "leaning" ? "The desk favours the approaches that get the most answers, and keeps testing the others."
      : `Too early to favour one: the desk waits for about ${lr.min_sample} messages with a known result per approach.`;
    return ` Approach: <b>${esc(lr.names[d.approach])}</b> (<a href="/stats#approaches" target="_blank" rel="noopener" title="${esc(tip)}">${why}</a>).`;
  }

  function sendHint(l) {
    const r = l.send || {};
    const needs = (r.needs || []).length ? ` Needs: ${r.needs.join(", ")}.` : "";
    return `On ${esc(r.name || "the board")}: ${esc(r.where || "its message box")}. The message fills in by itself (desk extension) or with <b>Fill from desk</b>; read it, Send.${esc(needs)}`;
  }

  function composer(l) {
    const m = msgFor(l);
    const title = tab === "sent" ? "Follow-up message" : "Message";
    return `<section class="composer card">
      <header><h3>${title}</h3>
        <div class="seg small" role="radiogroup" aria-label="Language">
          <button data-lang="fr" class="${m.lang === "fr" ? "on" : ""}">Français</button><button data-lang="en" class="${m.lang === "en" ? "on" : ""}">English</button></div></header>
      ${draftLine(l, m)}
      <textarea spellcheck="true" aria-label="${title}">${esc(m.text)}</textarea>
      <footer><span class="hint">${tab === "sent" ? "Copy it, then send it on the board." : "Approve copies this and opens the ad. " + sendHint(l)} The broker disclaimer is always kept.</span><span class="count"></span></footer>
    </section>`;
  }

  // ------------------------------------------------------------ custom messages (drafts)
  async function requestDraft(l, lang, fresh, quiet, other) {
    const key = l.id + ":" + lang;
    if (drafting[key]) return;
    drafting[key] = true; delete draftErr[key];
    if (!quiet) refreshComposer(l);
    let res;
    try {
      res = await (await fetch("/api/lead/" + encodeURIComponent(l.id) + "/draft", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lang, fresh: !!fresh, other: !!other }) })).json();
    } catch (e) { res = { error: "The desk didn't answer. Check your connection (and that you're still signed in), then try again." }; }
    delete drafting[key];
    if (res.error) draftErr[key] = res.error;
    else {
      l.drafts = other ? {} : l.drafts || {};  // another approach: the other language is rewritten that way too
      l.drafts[lang] = { text: res.text, template: res.template, model: res.model, approach: res.approach, picked_by: res.picked_by };
      const e = edits[l.id];
      // A message Stefan has started editing is never replaced under his hands.
      if (e && !fresh && (e[lang] != null || e.template)) { /* keep his edit or his choice of the template */ } else if (e) { delete e[lang]; delete e.template; }
    }
    refreshComposer(l);
  }

  function refreshComposer(l) {
    if (!data || data.leads[cursor] !== l || !isTriage()) return;
    const old = $(".composer", stage);
    if (!old) return;
    const focused = document.activeElement === $("textarea", old);
    old.outerHTML = composer(l);
    wireComposer(l, $(".composer", stage));
    if (focused) $(".composer textarea", stage).focus();
  }

  function autoDraft(l) {
    if (!canDraft() || !data.drafting.auto) return;
    const lang = msgFor(l).lang, key = l.id + ":" + lang;
    if (!(l.drafts && l.drafts[lang]) && !draftErr[key]) requestDraft(l, lang);
    const nx = data.leads[cursor + 1];  // the next ad's message is ready when Stefan gets there
    if (nx && !(nx.drafts && nx.drafts[nx.lang || "fr"])) requestDraft(nx, nx.lang || "fr", false, true);
  }

  // The ad opens with the message after #zdesk= (it stays in the browser), for the Fill from desk bookmark.
  function openAd(l, text) {
    const payload = encodeURIComponent(JSON.stringify({ m: text, ...(data.agent || {}) }));
    window.open((l.url || "").split("#")[0] + "#zdesk=" + payload, "_blank", "noopener");
  }

  // ------------------------------------------------------------ one-at-a-time view
  function renderCard() {
    const n = data.leads.length;
    if (!n) return renderEmpty();
    cursor = Math.max(0, Math.min(cursor, n - 1));
    const l = data.leads[cursor];
    const fl = flagsHTML(l);
    const reasons = data.reasons.map((r, i) => `<button class="chip" data-reason="${esc(r)}"><kbd>${i + 1}</kbd>${esc(r)}${r === "Broker" ? `<small>flags this poster next time</small>` : ""}</button>`).join("");
    stage.innerHTML = `
      <div class="triage">
        <article class="lead-card card" id="leadcard">
          <div class="media">${l.image ? `<img src="${esc(l.image)}" alt="">` : `<div class="noimg">No photo in the ad</div>`}
            <span class="pos">${(cursor + 1).toLocaleString("en-CA")} of ${n.toLocaleString("en-CA")}</span></div>
          <div class="facts">
            ${fl.banners}
            <div class="price-row"><span class="price">${money(l.price)}</span>${l.price ? `<span class="per">/ month</span>` : ""}</div>
            <h2 class="title">${esc(l.title)}</h2>
            <p class="where">${esc(l.area || "Area unknown")}${l.address ? ` · ${esc(l.address)}` : ""}</p>
            <div class="keyfacts">${keyFacts(l)}</div>
            ${phoneLine(l)}
            ${l.snippet ? `<p class="snippet">${esc(l.snippet)}</p>` : ""}
            ${fl.chips ? `<div class="tags">${fl.chips}</div>` : ""}
            ${others(l)}
            <div class="links"><a href="${esc(l.url)}" target="_blank" rel="noopener">View ad ↗</a>
              <a href="${centrisLink(l)}" target="_blank" rel="noopener">Check Centris / Realtor ↗</a></div>
          </div>
        </article>
        ${composer(l)}
      </div>
      <div class="actionbar ${rejecting ? "rejecting" : ""}">
        <div class="ab-main">
          <div class="ab-left">
            <button class="btn-reject" data-do="reject" title="Reject (R)">✕ Reject <kbd>R</kbd></button>
            ${tab === "later" ? `<button data-do="back" title="Back to review (L)">Back to review <kbd>L</kbd></button>`
              : `<button data-do="later" title="Later (L)">Later <kbd>L</kbd></button>`}
          </div>
          <div class="ab-nav"><button class="icon" data-do="prev" aria-label="Previous (K)" title="Previous (K)">‹</button>
            <span>${(cursor + 1).toLocaleString("en-CA")} / ${n.toLocaleString("en-CA")}</span>
            <button class="icon" data-do="next" aria-label="Next (J)" title="Next (J)">›</button></div>
          <button class="primary big" data-do="approve" title="Approve (A)">Approve: open ad to send <kbd>A</kbd></button>
        </div>
        <div class="ab-reasons"><span class="q">Why reject?</span>${reasons}<button class="ghost" data-do="cancel">Cancel <kbd>Esc</kbd></button></div>
      </div>`;
    wireComposer(l, $(".composer", stage));
    autoDraft(l);
    $$("[data-do]", stage).forEach((b) => b.addEventListener("click", () => cardAction(b.dataset.do)));
    $$("[data-reason]", stage).forEach((b) => b.addEventListener("click", () => act(l, { action: "reject", reason: b.dataset.reason })));
    swipe($("#leadcard"));
    // Preload the next photo so the next card appears instantly.
    const nx = data.leads[cursor + 1];
    if (nx && nx.image) { const im = new Image(); im.src = nx.image; }
  }

  function cardAction(what) {
    const l = data.leads[cursor];
    if (what === "next") return move(1);
    if (what === "prev") return move(-1);
    if (what === "reject") { rejecting = true; return render(); }
    if (what === "cancel") { rejecting = false; return render(); }
    if (what === "later") return act(l, { action: "later" });
    if (what === "back") return act(l, { action: "undo" });
    if (what === "approve") return approve(l);
  }

  function move(d) {
    if (!data.leads.length) return;
    cursor = Math.max(0, Math.min(cursor + d, data.leads.length - 1));
    rejecting = false;
    if (isTriage()) render(); else markRow();
  }

  function renderEmpty() {
    const c = data.counts, t = data.today || {};
    const msgs = {
      review: [`You're through the pile${phase ? ` for phase ${phase}` : ""}.`, `New ads arrive with tomorrow's morning fetch.`],
      broker: ["No likely brokers waiting.", ""],
      later: ["Nothing parked for later.", ""],
      approved: ["Nothing waiting to be sent.", "Approved ads wait here until you mark them sent."],
      sent: ["No messages sent yet.", ""],
      rejected: ["Nothing rejected.", ""],
      hidden: ["Nothing hidden.", ""],
    }[tab];
    const toSend = c.approved && tab !== "approved" ? `<button class="primary" data-go="approved">${c.approved} approved ad${c.approved > 1 ? "s" : ""} still to send</button>` : "";
    const sum = ["review", "broker"].includes(tab) && (t.approved || t.rejected) ? `<p class="muted">Today: ${t.approved || 0} approved, ${t.sent || 0} sent, ${t.rejected || 0} rejected, ${t.later || 0} for later.</p>` : "";
    stage.innerHTML = `<div class="empty card"><div class="big-ok">${tab === "review" ? "✓" : "·"}</div><h2>${msgs[0]}</h2>${msgs[1] ? `<p class="muted">${msgs[1]}</p>` : ""}${sum}${toSend}</div>`;
    $$("[data-go]", stage).forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
  }

  // ------------------------------------------------------------ list view
  function rowActions(l) {
    if (tab === "approved") return `<button data-r="reopen" title="Copies the message and opens the ad, ready for Fill from desk">Open to send</button><button data-r="sent" class="primary">Mark sent ✓</button><button data-r="back" class="ghost">Back to review</button>`;
    if (tab === "sent") {
      const quiet = !l.outcome && daysUp(l.sent_at) >= ((data.learning || {}).no_reply_days || 14);
      return `<span class="muted small"${quiet ? ` title="Nothing marked after ${data.learning.no_reply_days} days: counted as no reply until you mark what happened."` : ""}>Sent ${esc((l.sent_at || "").slice(0, 10))}${quiet ? " · no answer" : ""}</span>
        <div class="seg small outcome">${OUTCOMES.map(([k, v]) => `<button data-r="o:${k}" class="${l.outcome === k ? "on" : ""}">${v}</button>`).join("")}</div>
        <button data-r="followup">Follow-up…</button>`;
    }
    if (tab === "rejected") return `<span class="muted small">${esc(l.reject_reason || "")}</span><button data-r="back">Back to review</button>`;
    if (tab === "hidden") return `<button data-r="unhide">Move to review</button>`;
    if (tab === "later") return `<button data-r="back">Back to review</button>`;
    return "";
  }

  function renderList() {
    const n = data.leads.length;
    if (!n) return renderEmpty();
    cursor = Math.max(0, Math.min(cursor, n - 1));
    const sel = SELECTABLE.includes(tab);
    const head = sel ? `<div class="listhead"><label class="check"><input type="checkbox" id="selall"> Select all ${n.toLocaleString("en-CA")}</label>
      <span class="muted small">Shift-click to select a range. ${TRIAGE.includes(tab) ? "Click a row to open it." : ""}</span></div>` : "";
    stage.innerHTML = head + `<div class="rows card">` + data.leads.map((l, i) => {
      const fl = flagsHTML(l);
      const broker = l.flags.some((f) => f.level === "broker");
      return `<div class="row ${i === cursor ? "cur" : ""} ${selected.has(l.id) ? "sel" : ""}" data-i="${i}">
        ${sel ? `<input type="checkbox" class="pick" ${selected.has(l.id) ? "checked" : ""} aria-label="Select">` : ""}
        ${l.image ? `<img src="${esc(l.image)}" alt="" loading="lazy">` : `<div class="noimg"></div>`}
        <div class="rt"><a class="t" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a>
          <div class="rm">${broker ? `<span class="tag red">Likely broker</span>` : ""}${keyFacts(l)}${l.phone ? `<span class="kf">☎ ${esc(l.phone.split(", ")[0])}</span>` : ""}<span class="muted">${esc(l.area || "")}</span></div></div>
        <div class="rp">${money(l.price)}</div>
        <div class="ra">${rowActions(l)}</div>
        ${tab === "sent" && l._open ? `<div class="rx">${composer(l)}</div>` : ""}
      </div>`;
    }).join("") + `</div>` + (selected.size ? bulkBar() : "");
    wireList();
  }

  function bulkBar() {
    const canDecide = ["review", "broker", "later"].includes(tab);
    return `<div class="actionbar bulk ${rejecting ? "rejecting" : ""}"><div class="ab-main">
      <span class="ab-count"><b>${selected.size}</b> selected</span>
      <div class="ab-left">
        ${canDecide ? `<button class="btn-reject" data-b="reject">✕ Reject…</button>` : ""}
        ${tab === "review" || tab === "broker" ? `<button data-b="later">Later</button>` : ""}
        ${tab === "later" || tab === "rejected" ? `<button data-b="back">Back to review</button>` : ""}
        ${tab === "hidden" ? `<button data-b="unhide">Move to review</button>` : ""}
        <button class="ghost" data-b="clear">Clear selection</button></div></div>
      <div class="ab-reasons"><span class="q">Reject ${selected.size} because</span>${data.reasons.map((r, i) => `<button class="chip" data-breason="${esc(r)}"><kbd>${i + 1}</kbd>${esc(r)}</button>`).join("")}<button class="ghost" data-b="cancel">Cancel</button></div></div>`;
  }

  function wireList() {
    const all = $("#selall");
    if (all) {
      all.checked = selected.size === data.leads.length;
      all.addEventListener("change", () => { data.leads.forEach((l) => all.checked ? selected.add(l.id) : selected.delete(l.id)); render(); });
    }
    $$(".row", stage).forEach((row) => {
      const i = +row.dataset.i, l = data.leads[i];
      const pick = $(".pick", row);
      if (pick) pick.addEventListener("click", (e) => { e.stopPropagation(); toggle(i, e.shiftKey); });
      row.addEventListener("click", (e) => {
        if (e.target.closest("a,button,textarea,input,.rx")) return;
        if (e.shiftKey && SELECTABLE.includes(tab)) return toggle(i, true);
        cursor = i;
        if (TRIAGE.includes(tab)) { setView("card"); } else markRow();
      });
      $$("[data-r]", row).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); rowAction(l, b.dataset.r, row); }));
      const comp = $(".composer", row);
      if (comp) wireComposer(l, comp);
    });
    $$("[data-b]", stage).forEach((b) => b.addEventListener("click", () => bulk(b.dataset.b)));
    $$("[data-breason]", stage).forEach((b) => b.addEventListener("click", () => bulk("reject:" + b.dataset.breason)));
  }

  function toggle(i, range) {
    const id = data.leads[i].id;
    if (range && lastClicked != null) {
      const [a, b] = [Math.min(lastClicked, i), Math.max(lastClicked, i)];
      const on = !selected.has(id);
      for (let k = a; k <= b; k++) on ? selected.add(data.leads[k].id) : selected.delete(data.leads[k].id);
    } else selected.has(id) ? selected.delete(id) : selected.add(id);
    lastClicked = i; cursor = i;
    const y = window.scrollY; render(); window.scrollTo(0, y);
  }

  function markRow() {
    $$(".row", stage).forEach((r) => r.classList.toggle("cur", +r.dataset.i === cursor));
    const cur = $(".row.cur", stage);
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  async function rowAction(l, what, row) {
    if (what === "sent") return act(l, { action: "sent" });
    if (what === "reopen") {
      const text = l.message || l["message_" + (l.lang || "fr")];
      const ok = await copy(text);
      openAd(l, text);
      pendingSend = { lead: l, at: Date.now() };
      return toast(ok ? "Message copied and ad opened." : "Ad opened, but the message couldn't be copied. Open the ad in To send again.");
    }
    if (what === "back") return act(l, { action: "undo" });
    if (what === "unhide") return act(l, { action: "rebucket", bucket: "review" });
    if (what === "followup") { l._open = !l._open; const y = window.scrollY; render(); window.scrollTo(0, y); return; }
    if (what.startsWith("o:")) {
      const k = what.slice(2);
      return act(l, l.outcome === k ? { action: "set_outcome", outcome: "" } : { action: k });
    }
  }

  async function bulk(what) {
    if (what === "clear") { selected.clear(); rejecting = false; return render(); }
    if (what === "reject") { rejecting = true; return render(); }
    if (what === "cancel") { rejecting = false; return render(); }
    const leads = data.leads.filter((l) => selected.has(l.id));
    let body;
    if (what === "later") body = { action: "later" };
    else if (what === "back") body = { action: "undo" };
    else if (what === "unhide") body = { action: "rebucket", bucket: "review" };
    else if (what.startsWith("reject:")) body = { action: "reject", reason: what.slice(7) };
    await fetch("/api/decide-many", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: leads.map((l) => l.id), ...body }) });
    const entry = { tab, items: leads.map((l) => ({ lead: l, index: data.leads.indexOf(l), inverse: inverse(l, body) })) };
    undoStack.push(entry);
    data.leads = data.leads.filter((l) => !selected.has(l.id));
    selected.clear(); rejecting = false;
    render(); refreshCounts();
    toast(`${leads.length} ${verb(body)}.`, true);
  }

  // ------------------------------------------------------------ message box
  // The board's own cap when one is known, else the desk's length target (Kijiji's box has none: send.py).
  function limitFor(l) {
    const r = l.send || {}, board = r.name || "the board";
    if (r.max_chars) return { max: Math.min(r.max_chars, data.limit), what: r.max_chars < data.limit ? `${board}'s limit` : "the desk's length target", note: `${board} takes up to ${r.max_chars.toLocaleString("en-CA")}.` };
    const note = r.max_chars_checked ? `${board}'s message box has no length limit (checked ${r.max_chars_checked}); shorter messages get read.` : `${board}'s own limit isn't known yet.`;
    return { max: data.limit, what: "the desk's length target", note };
  }

  function wireComposer(l, el) {
    const ta = $("textarea", el), c = $(".count", el);
    const count = () => {
      const n = ta.value.length, lim = limitFor(l);
      c.textContent = `${n.toLocaleString("en-CA")} / ${lim.max.toLocaleString("en-CA")}`;
      c.classList.toggle("over", n > lim.max);
      c.title = (n > lim.max ? `Longer than ${lim.what}: shorten it before sending. ` : `Characters, against ${lim.what}. `) + lim.note;
    };
    count();
    ta.addEventListener("input", () => { const e = (edits[l.id] = edits[l.id] || {}); e.lang = e.lang || msgFor(l).lang; e[e.lang] = ta.value; count(); });
    $$("[data-lang]", el).forEach((b) => b.addEventListener("click", () => setLang(l, b.dataset.lang, el)));
    $$("[data-draft]", el).forEach((b) => b.addEventListener("click", () => draftAction(l, b.dataset.draft)));
    if (tab === "sent") {
      const btn = document.createElement("button");
      btn.className = "primary"; btn.textContent = "Copy follow-up and open ad";
      btn.addEventListener("click", async () => { const ok = await copy(ta.value); openAd(l, ta.value); toast(ok ? "Follow-up copied and ad opened." : "Couldn't copy: select the text and copy it by hand."); });
      $("footer", el).appendChild(btn);
    }
  }

  function setLang(l, lang, el) {
    const e = (edits[l.id] = edits[l.id] || {});
    e.lang = lang;
    if (isTriage() && $(".composer", stage) === el) { refreshComposer(l); autoDraft(l); return; }
    const ta = $("textarea", el);
    ta.value = msgFor(l).text;
    $$("[data-lang]", el).forEach((b) => b.classList.toggle("on", b.dataset.lang === lang));
    ta.dispatchEvent(new Event("input"));
  }

  function draftAction(l, what) {
    const lang = msgFor(l).lang, e = (edits[l.id] = edits[l.id] || {});
    if (what === "template") { e.template = true; delete e[lang]; return refreshComposer(l); }
    if (what === "custom") { delete e.template; delete e[lang]; return refreshComposer(l); }
    if (e[lang] != null && !confirm("Replace your edits with a new message for this ad?")) return;
    delete e.template; delete e[lang];
    requestDraft(l, lang, what === "fresh" || what === "other", false, what === "other");
  }

  // ------------------------------------------------------------ actions, undo
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall back below */ }
    const t = document.createElement("textarea");  // older route, works where the clipboard API is refused
    t.value = text; t.setAttribute("readonly", ""); t.style.cssText = "position:fixed;opacity:0;top:0";
    document.body.appendChild(t); t.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    t.remove();
    return ok;
  }

  async function approve(l, force) {
    const ta = $(".composer textarea", stage), m = msgFor(l);
    const text = ta ? ta.value : m.text;
    const lim = limitFor(l);
    if (text.length > lim.max && !confirm(`The message is ${text.length} characters, over ${lim.what} (${lim.max}). Approve anyway?`)) return;
    if (!force && !(await copy(text))) {
      if (ta) { ta.focus(); ta.select(); }
      return toast("The message couldn't be copied automatically. It's selected: press ⌘C first.", false, ["I copied it, open the ad", () => approve(l, true)]);
    }
    openAd(l, text);
    await act(l, { action: "approve", message: text, shown: { custom: m.custom, lang: m.lang } }, "Approved. Ad opened: its message box fills by itself with the desk's Chrome extension (or click Fill from desk, or ⌘V). Send it, then come back.");
    pendingSend = { lead: l, at: Date.now() };
  }

  const verb = (b) => ({ approve: "approved", reject: `rejected${b.reason ? " · " + b.reason : ""}`, later: "moved to Later", undo: "moved back to review",
    sent: "marked sent", unsend: "moved back to To send", rebucket: b.bucket === "review" ? "moved to review" : "hidden", set_outcome: "outcome cleared", declined: "marked as said no" }[b.action]
    || "marked " + (OUTCOMES.find((o) => o[0] === b.action) || [, b.action])[1].toLowerCase());

  function inverse(l, b) {
    switch (b.action) {
      case "approve": case "reject": case "later": return { action: "undo" };
      case "sent": return { action: "unsend" };
      case "rebucket": return { action: "rebucket", bucket: l.bucket };
      case "undo":
        if (l.status === "later") return { action: "later" };
        if (l.status === "rejected") return { action: "reject", reason: l.reject_reason };
        if (l.status === "approved") return { action: "approve", message: l.message };
        return null;
      default: return { action: "set_outcome", outcome: l.outcome || "" };  // outcome buttons
    }
  }

  async function act(l, body, msg) {
    try { await post(l.id, body); } catch (e) { return toast("That didn't save. Check your connection (and that you're still signed in), then try again."); }
    const index = data.leads.indexOf(l);
    undoStack.push({ tab, items: [{ lead: { ...l }, index, inverse: inverse(l, body) }] });
    const stays = tab === "sent" && !["unsend"].includes(body.action);
    if (stays) l.outcome = body.action === "set_outcome" ? body.outcome || null : body.action;
    else data.leads.splice(index, 1);
    rejecting = false;
    const y = window.scrollY; render(); if (!isTriage()) window.scrollTo(0, y);
    refreshCounts();
    toast(msg || `${short(l)} ${verb(body)}.`, true);
  }

  async function undo() {
    const entry = undoStack.pop();
    if (!entry) return toast("Nothing to undo.");
    for (const it of entry.items) if (it.inverse) await post(it.lead.id, it.inverse);
    if (pendingSend && entry.items.some((it) => it.lead.id === pendingSend.lead.id)) pendingSend = null;
    hideSendCheck();
    if (entry.tab === tab) {
      const sorted = entry.items.slice().sort((a, b) => a.index - b.index);
      for (const it of sorted) {
        const at = data.leads.findIndex((x) => x.id === it.lead.id);
        if (at >= 0) data.leads[at] = it.lead; else data.leads.splice(Math.min(it.index, data.leads.length), 0, it.lead);
      }
      cursor = sorted[0].index;
      render(); refreshCounts();
    } else refreshCounts();
    toast(`Undone: ${entry.items.length > 1 ? entry.items.length + " ads" : short(entry.items[0].lead)} back where ${entry.items.length > 1 ? "they were" : "it was"}.`);
  }

  const short = (l) => "“" + (l.title.length > 48 ? l.title.slice(0, 46) + "…" : l.title) + "”";

  let toastTimer;
  function toast(text, withUndo, extra) {
    const t = $("#toast");
    t.innerHTML = `<span>${esc(text)}</span>` + (withUndo ? `<button data-undo>Undo <kbd>Z</kbd></button>` : "") + (extra ? `<button data-extra>${esc(extra[0])}</button>` : "");
    if (extra) $("[data-extra]", t).addEventListener("click", () => { t.hidden = true; extra[1](); });
    t.hidden = false;
    const u = $("[data-undo]", t);
    if (u) u.addEventListener("click", undo);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), extra ? 30000 : withUndo ? 8000 : 6000);
  }

  // After Approve, Stefan sends on the board in another tab. When he comes back, ask once.
  function showSendCheck() {
    if (!pendingSend || Date.now() - pendingSend.at > 45 * 60000) return;
    const l = pendingSend.lead, b = $("#sendcheck");
    b.innerHTML = `<span>Did you send the message to ${esc(short(l))}?</span>
      <button class="primary" data-s="yes">Yes, mark sent <kbd>S</kbd></button><button class="ghost" data-s="no">Not yet, keep it in To send</button>`;
    b.hidden = false;
    $("[data-s=yes]", b).addEventListener("click", markPendingSent);
    $("[data-s=no]", b).addEventListener("click", () => { pendingSend = null; hideSendCheck(); });
  }
  function hideSendCheck() { $("#sendcheck").hidden = true; }
  async function markPendingSent() {
    const l = pendingSend.lead;
    pendingSend = null; hideSendCheck();
    try { await post(l.id, { action: "sent" }); } catch (e) { return toast("That didn't save. Check your connection (and that you're still signed in), then try again."); }
    undoStack.push({ tab: "approved", items: [{ lead: { ...l, status: "approved" }, index: 0, inverse: { action: "unsend" } }] });
    if (tab === "approved") { data.leads = data.leads.filter((x) => x.id !== l.id); render(); }
    refreshCounts();
    toast(`${short(l)} marked sent.`, true);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) showSendCheck(); });

  // ------------------------------------------------------------ swipe (phone / tablet)
  function swipe(card) {
    let x0 = null, dx = 0;
    card.addEventListener("pointerdown", (e) => { if (e.pointerType !== "touch" || e.target.closest("a,button,textarea,summary")) return; x0 = e.clientX; dx = 0; });
    card.addEventListener("pointermove", (e) => {
      if (x0 == null) return;
      dx = e.clientX - x0;
      card.style.transform = `translateX(${dx}px) rotate(${dx / 40}deg)`;
      card.dataset.swipe = dx > 60 ? "yes" : dx < -60 ? "no" : "";
    });
    const end = () => {
      if (x0 == null) return;
      x0 = null; card.style.transform = ""; card.dataset.swipe = "";
      if (dx > 110) approve(data.leads[cursor]);
      else if (dx < -110) { rejecting = true; render(); }
    };
    card.addEventListener("pointerup", end);
    card.addEventListener("pointercancel", end);
  }

  // ------------------------------------------------------------ navigation
  function render() {
    if (!data) return;
    document.body.classList.toggle("has-bar", (isTriage() && data.leads.length > 0) || selected.size > 0);
    isTriage() ? renderCard() : renderList();
  }
  function setView(v) { view = v; store.set("view", v); rejecting = false; renderChrome(); render(); window.scrollTo(0, 0); }
  function go(t) { tab = t; history.replaceState(null, "", "#" + t); load(); window.scrollTo(0, 0); }

  // Toolbar buttons give focus back, so Enter and the letter keys keep acting on the ad, not the button.
  $$(".deskbar button").forEach((b) => b.addEventListener("click", () => b.blur()));
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => go(b.dataset.tab)));
  $$("#phase button").forEach((b) => b.addEventListener("click", () => { phase = b.dataset.phase; store.set("phase", phase); load(); }));
  $$("#view button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("#helpbtn").addEventListener("click", () => $("#help").showModal());

  const manual = $("#manual"), addbtn = $("#addbtn");
  const showManual = (on) => { manual.hidden = !on; addbtn.setAttribute("aria-expanded", on); if (on) $("input[name=url]", manual).focus(); };
  addbtn.addEventListener("click", () => showManual(manual.hidden));
  $("#manualcancel").addEventListener("click", () => showManual(false));
  manual.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(manual).entries());
    const r = await (await fetch("/api/manual", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    manual.reset(); showManual(false);
    const where = { review: "Review", broker: "Likely broker", hidden: "Hidden (it looks like a tenant ad or is outside the areas)" }[r.bucket] || "the list";
    tab = r.bucket === "hidden" ? "hidden" : r.bucket || "review";
    await load();
    const i = data.leads.findIndex((l) => l.id === r.id);
    if (i >= 0) { cursor = i; render(); }
    toast(`Added to ${where}.`);
  });

  document.addEventListener("keydown", (e) => {
    const typing = ["TEXTAREA", "INPUT", "SELECT"].includes(e.target.tagName);
    if (e.key === "Escape") {
      if (typing) { e.target.blur(); return; }
      if (rejecting) { rejecting = false; render(); }
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); return undo(); }
    if (typing || e.metaKey || e.ctrlKey || e.altKey || !data || $("#help").open) return;
    const k = e.key.toLowerCase(), l = data.leads[cursor];
    if (k === "?" || k === "/") return $("#help").showModal();
    if (k === "z") return undo();
    if (k === "v" && TRIAGE.includes(tab)) return setView(view === "card" ? "list" : "card");
    if (k === "s" && pendingSend && !$("#sendcheck").hidden) return markPendingSent();
    if (!l) return;
    if (rejecting && /^[1-9]$/.test(k)) {
      const r = data.reasons[+k - 1];
      if (r) { e.preventDefault(); return isTriage() ? act(l, { action: "reject", reason: r }) : bulk("reject:" + r); }
    }
    if (k === "j" || k === "arrowright" || (k === "arrowdown" && !isTriage())) { e.preventDefault(); return move(1); }
    if (k === "k" || k === "arrowleft" || (k === "arrowup" && !isTriage())) { e.preventDefault(); return move(-1); }
    if (!isTriage()) {
      if (k === "x" && SELECTABLE.includes(tab)) return toggle(cursor, false);
      if (k === "r" && selected.size && ["review", "broker", "later"].includes(tab)) { rejecting = true; return render(); }
      if (k === "enter" && TRIAGE.includes(tab)) return setView("card");
      if (k === "s" && tab === "approved") return act(l, { action: "sent" });
      if (k === "a" && tab === "approved") return rowAction(l, "reopen");
      return;
    }
    if (k === "a" || k === "enter") { e.preventDefault(); return approve(l); }
    if (k === "r") { rejecting = true; return render(); }
    if (k === "l") return act(l, { action: tab === "later" ? "undo" : "later" });
    if (k === "w" && canDraft()) return draftAction(l, l.drafts && l.drafts[msgFor(l).lang] ? "fresh" : "write");
    if (k === "e") { e.preventDefault(); const ta = $(".composer textarea", stage); if (ta) ta.focus(); return; }
    if (k === "f") { const el = $(".composer", stage); return setLang(l, msgFor(l).lang === "fr" ? "en" : "fr", el); }
  });

  // ------------------------------------------------------------ background reading of the boards
  // The desk opens on what it already has; stale boards are read behind it. New ads join the counts
  // without moving the ad Stefan is looking at.
  const NAMES = { kijiji: "Kijiji", craigslist: "Craigslist", logisquebec: "LogisQuébec", livrent: "liv.rent", appartogo: "Appartogo", lespac: "LesPAC", rentalsca: "Rentals.ca", padmapper: "PadMapper / Zumper", louer: "Louer.ca", facebook: "Facebook", alerts: "alert emails" };
  const ago = (s) => { const m = Math.round((Date.now() - Date.parse(s)) / 60000); return isNaN(m) ? "" : m < 2 ? "just now" : m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };
  let scanWasRunning = false, scanTimer = null;
  async function pollScan() {
    clearTimeout(scanTimer);
    let s;
    try { s = await (await fetch("/api/scan")).json(); } catch (e) { return; }
    const bar = $("#scanbar");
    if (s.running) {
      const line = (s.lines || []).filter((l) => !l.startsWith("!")).slice(-1)[0] || "Starting";
      bar.textContent = `Checking the boards for new ads in the background. ${line.replace(/^\s+/, "")}`;
      bar.hidden = false;
      scanWasRunning = true;
      scanTimer = setTimeout(pollScan, 3000);
      return;
    }
    const read = (s.boards || []).filter((b) => b.last_ok && !b.waiting).map((b) => `${NAMES[b.source] || b.source} ${ago(b.last_ok)}`);
    const failed = (s.boards || []).filter((b) => b.last_error).map((b) => NAMES[b.source] || b.source);
    const pending = (s.boards || []).filter((b) => b.letter_pending && !b.waiting).map((b) => NAMES[b.source] || b.source);
    bar.textContent = (read.length ? `Boards last read: ${read.join(", ")}.` : "") + (failed.length ? ` Couldn't read ${failed.join(", ")} last time.` : "")
      + (pending.length ? ` Permission letter pending: ${pending.join(", ")}.` : "");
    // On the online desk, the boards are read by the office Mac: say so when it hasn't been heard from.
    if (s.mac_away) bar.textContent += s.mac_seen ? ` The Mac that reads the boards was last heard from ${ago(s.mac_seen)}.` : " The Mac that reads the boards hasn't connected yet.";
    bar.hidden = !bar.textContent;
    if (scanWasRunning) {
      scanWasRunning = false;
      const n = (s.last && s.last.review) || 0;
      if (data) { data.leads.length ? refreshCounts() : load(); }
      if (n) toast(`${n} new ad${n > 1 ? "s" : ""} to review.`, false, data && data.leads.length ? ["Show them", () => load()] : null);
    }
  }
  $("#scanbtn").addEventListener("click", async () => {
    const r = await (await fetch("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
    if (r.mac_away) toast("The Mac that reads the boards isn't answering. Check that it's on and awake.");
    else if (!r.started && !r.running) toast("Every board was read recently. Nothing to do.");
    pollScan();
  });
  pollScan();

  const start = location.hash.slice(1);
  if ($(`#tabs button[data-tab="${start}"]`)) tab = start;
  load();
})();
