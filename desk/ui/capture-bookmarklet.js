// The "Send to desk" bookmark (see desk/capture.py). Reads only the open page, only when clicked.
(function () {
  var q = function (s) { return document.querySelector(s); };
  var meta = function (n) { var el = q('meta[property="' + n + '"]') || q('meta[name="' + n + '"]'); return el ? el.content || "" : ""; };
  var ld = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(function (s) { try { ld.push(JSON.parse(s.textContent)); } catch (e) {} });
  var sel = String(window.getSelection() || "").trim();
  var h1 = q("h1") ? q("h1").innerText : "";
  var d = { url: location.href, title: (meta("og:title") || h1 || document.title).slice(0, 300),
    text: (sel || meta("og:description") || meta("description")).slice(0, 4000), ld: JSON.stringify(ld).slice(0, 6000) };
  window.open("__DESK__#" + encodeURIComponent(JSON.stringify(d)), "zaarourdesk", "width=600,height=760");
})();
