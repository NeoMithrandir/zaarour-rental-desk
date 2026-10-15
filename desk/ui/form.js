// New rental form: section ticks, required fields left, 12-month lease end, renewal-call preview.
(function () {
  const form = document.getElementById("rentalform");
  if (!form) return;
  const months = +form.dataset.months || 6;
  const left = document.getElementById("left"), renewal = document.getElementById("renewal");
  const start = form.elements.lease_start, end = form.elements.lease_end;
  const iso = (d) => d.toISOString().slice(0, 10);
  const parse = (s) => (s ? new Date(s + "T12:00:00Z") : null);
  const addMonths = (d, m) => { const x = new Date(d); const day = x.getUTCDate(); x.setUTCDate(1); x.setUTCMonth(x.getUTCMonth() + m);
    const last = new Date(Date.UTC(x.getUTCFullYear(), x.getUTCMonth() + 1, 0)).getUTCDate(); x.setUTCDate(Math.min(day, last)); return x; };
  let endTouched = !!end.value;

  function update() {
    const req = Array.from(form.querySelectorAll("[required]"));
    const missing = req.filter((el) => !el.value.trim());
    left.textContent = missing.length ? `${missing.length} required field${missing.length > 1 ? "s" : ""} left` : "All required fields filled";
    left.classList.toggle("ok", !missing.length);
    document.querySelectorAll(".toc a").forEach((a) => {
      const sec = document.getElementById(a.dataset.sec);
      const r = sec.querySelectorAll("[required]");
      a.classList.toggle("done", r.length > 0 && Array.from(r).every((el) => el.value.trim()));
    });
    const s = parse(start.value), e = parse(end.value);
    if (s && e && e > s) {
      // Mirrors rentals.followup_date for the usual 12-month lease; the saved date is the one that counts.
      const leaseMonths = (e.getUTCFullYear() - s.getUTCFullYear()) * 12 + e.getUTCMonth() - s.getUTCMonth() + (e.getUTCDate() >= s.getUTCDate() - 1 ? 1 : 0);
      renewal.textContent = leaseMonths >= 12 ? `Renewal call: ${iso(addMonths(e, -months))}` : "Short lease: renewal call when the notice window opens";
    } else renewal.textContent = e && s ? "Lease end must be after the start" : "";
  }

  end.addEventListener("input", () => { endTouched = !!end.value; });
  start.addEventListener("change", () => {
    const s = parse(start.value);
    if (s && !endTouched) {  // the usual Québec lease: 12 months, ending the day before
      const e = addMonths(s, 12); e.setUTCDate(e.getUTCDate() - 1);
      end.value = iso(e);
      end.closest(".field").classList.add("filled");
    }
    update();
  });
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  form.addEventListener("invalid", (e) => e.target.closest(".field") && e.target.closest(".field").classList.add("missing"), true);

  // Highlight the section in view.
  const links = Array.from(document.querySelectorAll(".toc a"));
  const io = new IntersectionObserver((entries) => entries.forEach((en) => {
    if (en.isIntersecting) links.forEach((a) => a.classList.toggle("on", a.dataset.sec === en.target.id));
  }), { rootMargin: "-30% 0px -60% 0px" });
  document.querySelectorAll(".fsec").forEach((s) => io.observe(s));
  update();
})();
