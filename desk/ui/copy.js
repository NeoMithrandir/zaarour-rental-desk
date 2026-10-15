document.querySelectorAll("[data-copy]").forEach((b) =>
  b.addEventListener("click", async () => {
    const el = document.querySelector(b.dataset.copy);
    try { await navigator.clipboard.writeText(el.textContent); b.textContent = "Copied"; } catch (e) { b.textContent = "Select and copy by hand"; }
  })
);
