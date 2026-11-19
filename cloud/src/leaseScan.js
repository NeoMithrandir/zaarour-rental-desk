// Fill the New lease form from photos or scans of a lease, using Claude's vision (port of desk/lease_scan.py).
// The pages go to the Claude API, which streams back one field per line; each field is normalised with the lease
// import's rules. Nothing is saved to the tracker here: the form fills in, the person checks it and clicks Save.
// With an R2 bucket bound as SCANS, a copy of the pages is kept there (private); without one, nothing is kept.
import { FIELDS, FIELD_KEYS } from "./leases.js";
import { cleanField } from "./leaseImport.js";
import { ClaudeError, stream } from "./claude.js";
import { now } from "./util.js";

const IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];
const PDF = "application/pdf";
const MAX_FILES = 20;
const EXT = { "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", [PDF]: ".pdf" };
const DEAL = ["commission", "double_ended", "co_broker", "photographer", "lead_source", "notes_for_office"];

function fieldGuide() {
  return FIELDS.filter(([k]) => !DEAL.includes(k)).map(([key, label, kind]) => {
    let fmt = { date: "YYYY-MM-DD", money: "number, dollars per month unless the label says otherwise", check: "true or false",
      email: "email address", tel: "phone number as written" }[kind] || "text";
    if (kind.startsWith("select:")) fmt = "one of: " + kind.slice(7).split("|").join(" | ");
    return `- ${key}: ${label} (${fmt})`;
  }).join("\n");
}

export const SYSTEM = `You read residential lease documents from Québec (usually the TAL "Bail de logement" form, in French or English, \
sometimes with annexes or handwriting) and extract the lease's details for a broker's office.

Output one JSON object per line and nothing else: no prose, no code fences. As soon as you have read a field, write
{"field": "<key>", "value": <value>, "confidence": <0 to 1>, "evidence": "<the words on the page, short>", "page": <page number>}
Only use these keys:
${fieldGuide()}

Rules:
- Only report what the pages actually say. Leave a field out rather than guess. If you infer something (e.g. the end date
  from a stated 12-month term), say so in evidence and lower confidence below 0.9.
- confidence is how sure you are the value is right and correctly read: below 0.9 for hard-to-read handwriting, a partly
  visible page, or an inference.
- Several lessors or lessees: join the names with ", ". The lessor (locateur) is the landlord; the lessee (locataire) is the tenant.
- Rent is the monthly rent (loyer) for the dwelling; services_cost is the monthly cost of services and other charges.
- "Who pays" fields (heating, hot_water, electricity) are Landlord or Tenant.
- After the last field, write one final line: {"done": true, "document": "<what the pages are>", "notes": "<anything a person should check, or empty>"}`;

/** Check the uploaded pages: [{type, data}] with bare base64. Throws ClaudeError for something the person can fix. */
export function pagesFrom(files) {
  if (!files || !files.length) throw new ClaudeError("Add at least one photo or PDF.");
  if (files.length > MAX_FILES) throw new ClaudeError(`At most ${MAX_FILES} pages at a time.`);
  return files.map((f) => {
    const type = String(f.type || "").toLowerCase();
    if (!IMAGE_TYPES.includes(type) && type !== PDF) throw new ClaudeError(`${f.name || "A file"} isn't a photo (JPEG, PNG, WebP) or a PDF.`);
    return { type, data: String(f.data || "").replace(/^data:[^,]*,/, "").replace(/\s+/g, "") };
  });
}

function content(pages) {
  const blocks = [];
  pages.forEach((p, i) => {
    blocks.push({ type: "text", text: `Page ${i + 1}:` });
    blocks.push(p.type === PDF
      ? { type: "document", source: { type: "base64", media_type: PDF, data: p.data } }
      : { type: "image", source: { type: "base64", media_type: p.type, data: p.data } });
  });
  blocks.push({ type: "text", text: "Extract this lease's details, one JSON line per field." });
  return blocks;
}

/** One line of the model's output -> an event for the browser, or null. */
export function parseLine(line, minConf = 0.9) {
  line = line.trim().replace(/^`+|`+$/g, "");
  if (!line.startsWith("{")) return null;
  let obj;
  try { obj = JSON.parse(line); } catch { return null; }
  if (obj.done) return { type: "done", document: obj.document || "", notes: obj.notes || "" };
  const key = obj.field;
  if (!FIELD_KEYS.includes(key)) return null;
  const [value, err, warn] = cleanField(key, obj.value);
  let conf = parseFloat(obj.confidence ?? 0.5);
  conf = Number.isNaN(conf) ? 0.5 : Math.max(0, Math.min(1, conf));
  if (err || warn || value === "") return { type: "skipped", field: key, raw: obj.value, reason: err || warn || "empty" };
  return { type: "field", field: key, value, confidence: conf, check: conf < minConf, evidence: String(obj.evidence || "").slice(0, 300), page: obj.page };
}

async function keep(env, scanId, files, pages) {
  if (!env.SCANS) return;
  await Promise.all(pages.map((p, i) => {
    const bytes = Uint8Array.from(atob(p.data), (c) => c.charCodeAt(0));
    return env.SCANS.put(`${scanId}/page-${String(i + 1).padStart(2, "0")}${EXT[p.type]}`, bytes, { httpMetadata: { contentType: p.type } });
  }));
}

/** Stream the scan to the browser as server-sent events, one field at a time. */
export async function scanResponse(env, ctx, settings, body) {
  let pages;
  try {
    pages = pagesFrom(body.files);
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 400, headers: { "Content-Type": "application/json" } });
  }
  if (!settings.anthropic_api_key) {
    return new Response(JSON.stringify({ error: "The photo scan needs a Claude API key on the desk. Nesi adds it once with: npx wrangler secret put ANTHROPIC_API_KEY" }),
      { status: 400, headers: { "Content-Type": "application/json" } });
  }
  const minConf = parseFloat(settings.import_min_confidence ?? 0.9);
  const scanId = now().replace(/[-:]/g, "").replace("T", "-");
  const { readable, writable } = new TransformStream();
  const out = writable.getWriter();
  const enc = new TextEncoder();
  const send = (ev) => out.write(enc.encode("data: " + JSON.stringify(ev) + "\n\n"));

  const work = (async () => {
    try {
      await send({ type: "start", scan_id: scanId, pages: pages.length });
      try { await keep(env, scanId, body.files, pages); } catch { /* the copy is a convenience: never stop the scan over it */ }
      const seen = new Set();
      let buf = "";
      const handle = async (line) => {
        const ev = parseLine(line, minConf);
        if (!ev || (ev.type === "field" && seen.has(ev.field))) return;
        if (ev.type === "field") seen.add(ev.field);
        await send(ev);
      };
      const stop = await stream(settings, {
        model: settings.lease_scan_model || "claude-opus-5-5", effort: settings.lease_scan_effort || "low",
        system: SYSTEM, content: content(pages),
      }, async (chunk) => {
        buf += chunk;
        let i;
        while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); await handle(line); }
      });
      if (buf.trim()) await handle(buf);
      if (stop === "refusal") await send({ type: "error", message: "The pages couldn't be read automatically. Fill the form by hand." });
      else if (stop === "max_tokens") await send({ type: "error", message: "The reading stopped early. Check the remaining fields by hand." });
    } catch (e) {
      await send({ type: "error", message: e instanceof ClaudeError ? e.message : `The scan stopped: ${e?.message || e}` });
    }
    try { await send({ type: "end" }); await out.close(); } catch { /* the browser left */ }
  })();
  ctx.waitUntil(work);
  return new Response(readable, { headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" } });
}
