// The desk's Chrome extension (see desk/send.py): when an ad was opened from the desk (#zdesk= in its address), it
// waits for the board's message box and fills it by itself, the same way the Fill from desk bookmark does. Never sends.
(function () {
  var KEY = "zdesk", WAIT = 15 * 60 * 1000;
  var d = null;
  try {
    var m = location.hash.match(/zdesk=([^&]*)/);
    if (m) { d = JSON.parse(decodeURIComponent(m[1])); sessionStorage.setItem(KEY, JSON.stringify({ d: d, at: Date.now() })); }
  } catch (e) { d = null; }
  if (!d) {  // the board's sign-in page dropped the address: this tab kept it
    try { var s = JSON.parse(sessionStorage.getItem(KEY) || "null"); if (s && Date.now() - s.at < WAIT) d = s.d; } catch (e) { d = null; }
  }
  if (!d || !d.m) return;

  var vis = function (el) { var r = el.getBoundingClientRect(); return r.width > 40 && r.height > 15 && getComputedStyle(el).visibility !== "hidden"; };
  var set = function (el, v) {
    el.focus();
    if (el.isContentEditable) { document.execCommand("selectAll", false, null); document.execCommand("insertText", false, v); return; }
    var proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  var findBox = function () {
    var boxes = Array.prototype.slice.call(document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')).filter(vis);
    boxes.sort(function (a, b) { var x = a.getBoundingClientRect(), y = b.getBoundingClientRect(); return y.width * y.height - x.width * x.height; });
    return boxes[0];
  };
  var note = function (text) {
    var n = document.createElement("div");
    n.textContent = text;
    n.style.cssText = "position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:320px;padding:10px 14px;border-radius:8px;" +
      "background:#2f7d5b;color:#fff;font:14px/1.4 -apple-system,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25);cursor:pointer";
    n.onclick = function () { n.remove(); };
    document.body.appendChild(n);
    setTimeout(function () { n.remove(); }, 12000);
  };
  var fill = function () {
    var box = findBox();
    if (!box) return false;
    set(box, d.m);
    var scope = box.closest("form") || document;
    Array.prototype.slice.call(scope.querySelectorAll("input")).filter(vis).forEach(function (i) {
      if (i.value) return;
      var k = ((i.name || "") + " " + (i.id || "") + " " + (i.placeholder || "") + " " + (i.getAttribute("autocomplete") || "")).toLowerCase();
      if (i.type === "email" || /mail|courriel/.test(k)) { if (d.e) set(i, d.e); }
      else if (i.type === "tel" || /phone|t[ée]l/.test(k)) { if (d.p) set(i, d.p); }
      else if (/name|nom/.test(k) && i.type === "text") { if (d.n) set(i, d.n); }
    });
    box.scrollIntoView({ block: "center" });
    box.style.outline = "3px solid #2f7d5b";
    setTimeout(function () {  // a page still starting up can wipe the box once: put it back if it's empty
      var now = box.isContentEditable ? box.textContent : box.value;
      if (box.isConnected && !now.trim()) set(box, d.m);
    }, 1500);
    sessionStorage.removeItem(KEY);  // once per ad: a box filled and then cleared by hand stays cleared
    note("Message from the desk filled in. Read it, then click the board's Send.");
    return true;
  };

  if (fill()) return;
  // Most boards show the box only after a click (Send message, Contact, Reply): watch for it.
  var started = Date.now(), queued = false;
  var obs = new MutationObserver(function () {
    if (queued) return;
    queued = true;
    setTimeout(function () {
      queued = false;
      if (fill() || Date.now() - started > WAIT) obs.disconnect();
    }, 300);
  });
  obs.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "class", "hidden"] });
})();
