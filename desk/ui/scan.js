// New lease: photos or scans of a lease fill the form live (server streams one field at a time).
(function () {
  const form = document.getElementById("leaseform");
  const input = document.getElementById("scanfiles");
  if (!form || !input) return;
  const drop = document.getElementById("dropzone");
  const list = document.getElementById("scanpages");
  const btn = document.getElementById("scanbtn");
  const status = document.getElementById("scanstatus");
  const metaInput = document.getElementById("scan_meta");
  const MAX_EDGE = 2200;          // px: plenty for reading print and handwriting, well under the API's image limits
  let pages = [];                 // {name, type, data}
  let scan = { scan_id: null, fields: {} };

  const say = (t) => { status.textContent = t; };
  const saveMeta = () => { metaInput.value = scan.scan_id ? JSON.stringify(scan) : ""; };

  function readAsDataURL(file) {
    return new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(file); });
  }

  // Phone photos are big (and sometimes HEIC): redraw as a JPEG of sensible size in the browser.
  async function shrink(file) {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, MAX_EDGE / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85);
  }

  async function add(files) {
    for (const f of files) {
      try {
        if (f.type === "application/pdf") pages.push({ name: f.name, type: f.type, data: await readAsDataURL(f) });
        else if (f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name)) pages.push({ name: f.name, type: "image/jpeg", data: await shrink(f) });
        else say(`${f.name} isn't a photo or a PDF.`);
      } catch (e) { say(`Couldn't open ${f.name}. Try a JPEG or PDF.`); }
    }
    render();
  }

  function render() {
    list.innerHTML = "";
    pages.forEach((p, i) => {
      const li = document.createElement("li");
      li.innerHTML = p.type === "application/pdf" ? `<span class="pdf">PDF</span>` : `<img alt="" src="${p.data}">`;
      const x = document.createElement("button");
      x.type = "button"; x.className = "ghost"; x.textContent = "×"; x.title = `Remove page ${i + 1}`;
      x.onclick = () => { pages.splice(i, 1); render(); };
      const n = document.createElement("small"); n.textContent = `Page ${i + 1}`;
      li.append(n, x); list.append(li);
    });
    btn.disabled = !pages.length;
    if (pages.length) say(`${pages.length} page${pages.length > 1 ? "s" : ""} ready.`);
  }

  input.addEventListener("change", () => { add([...input.files]); input.value = ""; });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => add([...e.dataTransfer.files]));

  function fill(ev) {
    const el = form.elements[ev.field];
    if (!el) return;
    if (el.type === "checkbox") el.checked = ev.value === "on";
    else el.value = ev.value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    const label = el.closest(".field");
    if (label) {
      label.classList.add("from-scan");
      label.classList.toggle("scan-check", ev.check);
      let tag = label.querySelector(".scantag");
      if (!tag) { tag = document.createElement("em"); tag.className = "scantag"; (label.querySelector(".lbl") || label).append(tag); }
      tag.textContent = ev.check ? `check · ${Math.round(ev.confidence * 100)} %` : "from photo";
      tag.title = (ev.evidence ? `“${ev.evidence}”` : "") + (ev.page ? ` (page ${ev.page})` : "");
    }
    scan.fields[ev.field] = { value: ev.value, confidence: ev.confidence, evidence: ev.evidence, page: ev.page, check: ev.check };
    saveMeta();
  }

  // A field the person types into is theirs now: drop the highlight, remember it was edited.
  form.addEventListener("input", (e) => {
    if (!e.isTrusted) return;
    const key = e.target.name, f = scan.fields[key];
    if (!f) return;
    f.edited = true; f.check = false; saveMeta();
    const label = e.target.closest(".field");
    if (label) { label.classList.remove("scan-check"); const t = label.querySelector(".scantag"); if (t) t.textContent = "checked"; }
  });

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    let filled = 0, toCheck = 0;
    say("Sending the pages…");
    try {
      const res = await fetch("/api/lease-scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ files: pages }) });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `The scan failed (${res.status}).`);
      }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "", notes = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!chunk.startsWith("data: ")) continue;
          const ev = JSON.parse(chunk.slice(6));
          if (ev.type === "start") { scan = { scan_id: ev.scan_id, fields: {} }; say(`Reading ${ev.pages} page${ev.pages > 1 ? "s" : ""}…`); }
          else if (ev.type === "field") { fill(ev); filled++; if (ev.check) toCheck++; say(`Reading… ${filled} field${filled > 1 ? "s" : ""} filled`); }
          else if (ev.type === "done") notes = ev.notes || "";
          else if (ev.type === "error") throw new Error(ev.message);
        }
      }
      say(`Done: ${filled} field${filled === 1 ? "" : "s"} filled from the pages` +
          (toCheck ? `, ${toCheck} to check (orange)` : "") + `. Review them, then save.` + (notes ? ` Note: ${notes}` : ""));
    } catch (e) {
      say(e.message + (filled ? ` ${filled} fields were filled before that.` : ""));
    } finally {
      btn.disabled = !pages.length;
    }
  });
})();
