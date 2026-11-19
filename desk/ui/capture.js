// Fills the capture form from the "Send to desk" bookmark (the data is in the address fragment, never sent anywhere).
(function () {
  const form = document.getElementById("capture");
  let d = {};
  try { d = JSON.parse(decodeURIComponent(location.hash.slice(1)) || "{}"); } catch (e) { d = {}; }
  history.replaceState(null, "", location.pathname);
  const HOSTS = [[/(^|\.)facebook\.com$/, "facebook"], [/(^|\.)kijiji\.ca$/, "kijiji"], [/(^|\.)craigslist\.org$/, "craigslist"],
    [/(^|\.)rentals\.ca$/, "rentalsca"], [/(^|\.)(padmapper|zumper)\.com$/, "padmapper"], [/(^|\.)appartogo\.com$/, "appartogo"],
    [/(^|\.)lespac\.com$/, "lespac"], [/(^|\.)louer\.ca$/, "louer"], [/(^|\.)logisquebec\.com$/, "logisquebec"], [/(^|\.)liv\.rent$/, "livrent"],
    [/(^|\.)duproprio\.com$/, "duproprio"]];
  let source = "other";
  try {
    const u = new URL(d.url || "");
    const hit = HOSTS.find(([rx]) => rx.test(u.hostname));
    if (hit) source = hit[1] === "facebook" && u.pathname.startsWith("/groups/") ? "group" : hit[1];
  } catch (e) { /* no url */ }
  const found = {};
  const walk = (o) => {
    if (Array.isArray(o)) return o.forEach(walk);
    if (!o || typeof o !== "object") return;
    for (const k of ["price", "lowPrice"]) if (k in o && !found.price) found.price = String(o[k]);
    const a = o.address;
    if (a && typeof a === "object" && !found.address) found.address = [a.streetAddress, a.addressLocality, ("QC " + (a.postalCode || "")).trim()].filter(Boolean).join(", ");
    Object.values(o).forEach(walk);
  };
  try { walk(JSON.parse(d.ld || "[]")); } catch (e) { /* no structured data */ }
  let price = found.price;
  if (!price) { const m = ((d.title || "") + " " + (d.text || "")).match(/\$\s?(\d[\d\s,]{2,6})|(\d[\d\s,]{2,6})\s?\$/); price = m ? m[1] || m[2] : ""; }
  price = String(price || "").replace(/[.,]\d{2}$/, "").replace(/\D/g, "");
  const set = (n, v) => { if (v) form.elements[n].value = v; };
  set("source", source); set("url", d.url); set("title", d.title); set("price", price); set("address", found.address); set("text", d.text);
  if (!d.url) document.getElementById("capintro").textContent = "Nothing was sent from a page. Fill the ad in by hand, or click Send to desk on an ad.";

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(form).entries());
    const done = document.getElementById("capdone");
    try {
      const r = await (await fetch("/api/manual", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
      const where = { review: "Review", broker: "Likely broker", hidden: "Hidden (it looks like a tenant ad or is outside the areas)" }[r.bucket] || "the desk";
      done.textContent = `Added to ${where}. This window closes by itself.`; done.className = "banner"; done.hidden = false; form.hidden = true;
      setTimeout(() => window.close(), 1500);
    } catch (err) {
      done.textContent = "The desk didn't answer. Check that the desk opens in this browser (and you're signed in), then click Add again."; done.className = "banner bad"; done.hidden = false;
    }
  });
})();
