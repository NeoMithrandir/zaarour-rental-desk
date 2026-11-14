// The "Fill from desk" bookmark (see desk/send.py). Fills the board's message box on the open page; never sends.
(function () {
  var d = null;
  try { var m = location.hash.match(/zdesk=([^&]*)/); if (m) d = JSON.parse(decodeURIComponent(m[1])); } catch (e) { d = null; }
  var vis = function (el) { var r = el.getBoundingClientRect(); return r.width > 40 && r.height > 15 && getComputedStyle(el).visibility !== "hidden"; };
  var set = function (el, v) {
    el.focus();
    if (el.isContentEditable) { document.execCommand("selectAll", false, null); document.execCommand("insertText", false, v); return; }
    var proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  var boxes = Array.prototype.slice.call(document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')).filter(vis);
  boxes.sort(function (a, b) { var x = a.getBoundingClientRect(), y = b.getBoundingClientRect(); return y.width * y.height - x.width * x.height; });
  var box = boxes[0];
  var go = function (msg) {
    if (!box) { alert("Open the board's message box first (Send message, Contact, Reply), then click Fill from desk again. The message is also on your clipboard: ⌘V."); return; }
    set(box, msg);
    if (d) {
      var scope = box.closest("form") || document;
      Array.prototype.slice.call(scope.querySelectorAll("input")).filter(vis).forEach(function (i) {
        if (i.value) return;
        var k = ((i.name || "") + " " + (i.id || "") + " " + (i.placeholder || "") + " " + (i.getAttribute("autocomplete") || "")).toLowerCase();
        if (i.type === "email" || /mail|courriel/.test(k)) { if (d.e) set(i, d.e); }
        else if (i.type === "tel" || /phone|t[ée]l/.test(k)) { if (d.p) set(i, d.p); }
        else if (/name|nom/.test(k) && i.type === "text") { if (d.n) set(i, d.n); }
      });
    }
    box.scrollIntoView({ block: "center" });
    box.style.outline = "3px solid #2f7d5b";
    box.focus();
  };
  if (d && d.m) return go(d.m);
  if (navigator.clipboard && navigator.clipboard.readText) {
    navigator.clipboard.readText().then(go, function () { alert("The desk's message isn't in this page's address. Click in the message box and press ⌘V."); });
  } else alert("Click in the message box and press ⌘V.");
})();
