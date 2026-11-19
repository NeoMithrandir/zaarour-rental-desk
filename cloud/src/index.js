// The rentals desk on Cloudflare: one Worker serving the pages, the review screen's API and the Mac's sync routes,
// with the data in D1. Cloudflare Access signs people in; auth.js checks every request again.
import * as api from "./api.js";
import { identify } from "./auth.js";
import { ROUTES, bookmarklet, installBody } from "./capture.js";
import { Desk } from "./config.js";
import * as drafts from "./drafts.js";
import { scanResponse } from "./leaseScan.js";
import * as leaseImport from "./leaseImport.js";
import * as leases from "./leases.js";
import * as pages from "./pages.js";
import * as profile from "./profile.js";
import * as sync from "./sync.js";
import * as tal from "./tal.js";
import * as approaches from "./approaches.js";
import { SECURITY, html, json, redirect } from "./util.js";

async function asset(env, request, path) {
  const r = await env.ASSETS.fetch(new Request(new URL(path, request.url)));
  if (!r.ok) throw new Error(`missing ${path}`);
  return r.text();
}

async function form(request) {
  const type = request.headers.get("Content-Type") || "";
  if (type.includes("json")) return request.json().catch(() => ({}));
  const fd = await request.formData();
  const out = {};
  for (const [k, v] of fd.entries()) out[k] = typeof v === "string" ? v : "";
  return out;
}

function csvCell(v) {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** A browser form or fetch from another site never gets to change anything here (Access's cookie rides along). */
function crossSite(request, url) {
  const origin = request.headers.get("Origin");
  if (origin) return origin !== url.origin;
  return request.headers.get("Sec-Fetch-Site") === "cross-site";
}

function denied(why, url) {
  if (url.pathname.startsWith("/api/")) return json({ error: "Sign in to the desk again (rentals.zaarour.ca).", why }, 401);
  return html(pages.page("Sign in", `<h1>Sign in to the desk</h1>
<p class="lede">The desk is for the Zaarour team. Open <a href="/">the desk</a> again and sign in with your zaarour.ca email.</p>`), 403);
}

async function getRoute(desk, env, request, url) {
  const p = url.pathname, who = desk.user.email;
  const settings = await desk.settings();
  if (p === "/") return html(pages.leadsPage(await asset(env, request, "/ui/leads.html"), await desk.areas(), who));
  if (p === "/api/scan") return json(await api.scanStatus(desk));
  if (p === "/capture") return html(pages.capturePage(await asset(env, request, "/ui/capture.html"), who));
  if (p === "/capture/install") {
    const cap = bookmarklet(await asset(env, request, "/ui/capture-bookmarklet.js"), url.origin);
    const fill = bookmarklet(await asset(env, request, "/ui/fill-bookmarklet.js"), url.origin);
    return html(pages.page("Browser buttons", installBody(cap, fill), "leads", who));
  }
  if (p === "/api/counts") return json({ counts: await api.leadCounts(desk.db, api.hiddenSources(settings)), today: await api.todayCounts(desk.db) });
  if (p === "/api/leads") return json(await api.apiLeads(desk, url.searchParams.get("tab") || "review", url.searchParams.get("phase") || ""));
  if (p === "/rentals" || p.startsWith("/rentals/") || p === "/rentals.csv") return redirect("/leases" + p.slice("/rentals".length) + url.search, 301);
  if (p === "/leases/new") return html(pages.leaseForm(settings, { keepsScans: Boolean(env.SCANS), who }));
  if (p === "/leases") return html(pages.trackerPage(await leases.exportRows(desk.db), settings, who));
  if (p === "/leases.csv") {
    const cols = ["id", "created_at", "followup_date", ...leases.FIELD_KEYS];
    const rows = await leases.exportRows(desk.db);
    const text = [cols.join(","), ...rows.map((r) => cols.map((c) => csvCell(r[c])).join(","))].join("\r\n") + "\r\n";
    return new Response(text, { headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": "attachment; filename=leases.csv" } });
  }
  if (p === "/stats") {
    const defs = approaches.load(await desk.config("approaches"));
    const on = drafts.status(settings, drafts.loadSamples((await desk.kv("samples")) || "")).on;
    return html(await pages.statsPage(desk.db, settings, defs, on, who));
  }
  if (p === "/me") return html(await pages.myDetailsPage(desk, { saved: url.searchParams.get("saved") || "" }));
  const parts = p.replace(/^\/+|\/+$/g, "").split("/");
  if (parts.length >= 2 && parts[0] === "leases" && /^\d+$/.test(parts[1])) {
    const r = await leases.get(desk.db, parseInt(parts[1], 10));
    if (!r) return html(pages.notFound(who), 404);
    if (parts.length === 2) return html(pages.leaseDone(r, settings, who));
    if (parts[2] === "followup.ics") {
      return new Response(leases.followupIcs(r), { headers: { "Content-Type": "text/calendar; charset=utf-8",
        "Content-Disposition": `attachment; filename=renewal-call-${r.id}.ics` } });
    }
    if (parts[2] === "lease") return html(pages.leasePage(r, who));
    if (parts[2] === "tal") return html(pages.talPage(r, await desk.config("tal_rates"), url.searchParams.has("saved"), who));
    if (parts[2] === "renewal") {
      const lang = url.searchParams.get("lang") === "en" ? "en" : "fr";
      return html(pages.renewalPage(r, settings, await desk.config("tal_rates"), await desk.template(`renewal_landlord_${lang}`), lang, who));
    }
  }
  return html(pages.notFound(who), 404);
}

async function postRoute(desk, env, ctx, request, url) {
  const p = url.pathname;
  const body = await form(request);
  let m;
  if ((m = p.match(/^\/api\/lead\/(.+)\/decide$/))) { const [res, code] = await api.apiDecide(desk, decodeURIComponent(m[1]), body); return json(res, code); }
  if ((m = p.match(/^\/api\/lead\/(.+)\/draft$/))) { const [res, code] = await api.apiDraft(desk, decodeURIComponent(m[1]), body); return json(res, code); }
  if (p === "/api/decide-many") {
    let ok = 0;
    for (const id of body.ids || []) if ((await api.apiDecide(desk, id, body))[1] === 200) ok += 1;
    return json({ ok });
  }
  if (p === "/api/manual") return json(await api.addManual(desk, body));
  if (p === "/api/lease-scan") return scanResponse(env, ctx, await desk.settings(), body);
  if (p === "/api/leases/import") return json(await api.apiLeaseImport(desk, body));
  if ((m = p.match(/^\/leases\/(\d+)\/tal$/))) {
    const keys = [tal.CURRENT_RENT[0], ...tal.INPUT_KEYS];
    if (await leases.saveTalInputs(desk.db, parseInt(m[1], 10), Object.fromEntries(keys.map((k) => [k, body[k] ?? ""])))) return redirect(`/leases/${m[1]}/tal?saved=1`);
    return json({ error: "not found" }, 404);
  }
  if ((m = p.match(/^\/leases\/(\d+)\/verify$/))) {
    if (await leaseImport.markVerified(desk.db, parseInt(m[1], 10))) return redirect(`/leases/${m[1]}/renewal`);
    return json({ error: "not found" }, 404);
  }
  if (p === "/api/scan") return json(await api.requestScan(desk, body));
  if (p === "/api/recheck") return json(await api.recheckAll(desk));
  if (p === "/me/details") { await profile.saveDetails(desk, body); return redirect("/me?saved=details"); }
  if (p === "/me/boards") {
    const s = (await desk.kv("desk_settings")) || {};
    s.hidden_sources = Object.keys(ROUTES).filter((k) => !(`show_${k}` in body));
    await desk.put("desk_settings", s);
    return redirect("/me?saved=boards#boards");
  }
  if ((m = p.match(/^\/me\/template\/([a-z_]+)$/)) && m[1] in profile.TEMPLATES) {
    const name = m[1];
    const errors = await profile.saveTemplate(desk, name, body.text || "");
    if (errors.length) return html(await pages.myDetailsPage(desk, { errors: { [name]: errors }, typed: { [name]: body.text || "" } }), 400);
    return redirect(`/me?saved=${name}#t-${name}`);
  }
  if (p === "/me/samples") {
    await profile.saveSamples(desk, body.text || "");
    const settings = await desk.settings();
    const samples = drafts.loadSamples(await profile.readSamples(desk));
    if (!drafts.status(settings, samples).on) return redirect("/me?saved=samples-off#samples");
    if (!samples.length) return redirect("/me?saved=samples#samples");
    const failed = await profile.suggestTemplates(desk, settings);
    if (Object.keys(failed).length) {
      const note = "The desk couldn't rewrite your wording this time: " + [...new Set(Object.values(failed))].sort().join(" ");
      return html(await pages.myDetailsPage(desk, { saved: "samples", note }));
    }
    return redirect("/me?saved=suggested#t-outreach_fr");
  }
  if ((m = p.match(/^\/me\/suggestion\/([a-z_]+)\/discard$/))) { await profile.dropSuggestion(desk, m[1]); return redirect("/me"); }
  if (p === "/leases/new" || p === "/rentals/new") {
    const errors = leases.validate(body);
    if (errors.length) return html(pages.leaseForm(await desk.settings(), { values: body, errors, keepsScans: Boolean(env.SCANS), who: desk.user.email }), 400);
    const rid = await leases.save(desk.db, body, await desk.settings(), desk.user.email);
    return redirect(`/leases/${rid}`);
  }
  return json({ error: "not found" }, 404);
}

async function handle(request, env, ctx) {
  const url = new URL(request.url);
  const me = await identify(request, env);
  if (me.kind === "none") return denied(me.why, url);

  if (me.kind === "agent") {
    const desk = new Desk(env, { email: "" });
    if (url.pathname === "/api/agent/leases-import" && request.method === "POST") {
      return json(await api.apiLeaseImport(desk, await request.json().catch(() => ({}))));
    }
    return sync.route(desk, request, url, ctx);
  }
  if (url.pathname.startsWith("/api/agent/")) return json({ error: "not found" }, 404);
  if (url.pathname.startsWith("/ui/") || url.pathname === "/extension.zip") {
    const r = await env.ASSETS.fetch(request);
    const out = new Response(r.body, r);
    for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
    if (url.pathname === "/extension.zip") out.headers.set("Content-Disposition", "attachment; filename=zaarour-desk-extension.zip");
    return out;
  }
  const desk = new Desk(env, { email: me.email });
  if (request.method === "GET" || request.method === "HEAD") return getRoute(desk, env, request, url);
  if (request.method === "POST") {
    if (crossSite(request, url)) return json({ error: "cross-site request refused" }, 403);
    return postRoute(desk, env, ctx, request, url);
  }
  return json({ error: "method not allowed" }, 405);
}

export default {
  async fetch(request, env, ctx) {
    try {
      return await handle(request, env, ctx);
    } catch (e) {
      console.error(e?.stack || e);
      const url = new URL(request.url);
      if (url.pathname.startsWith("/api/")) return json({ error: `The desk hit a problem: ${e?.message || e}` }, 500);
      return html(pages.page("Problem", `<h1>Something went wrong</h1><p class="lede">The desk hit a problem showing this page. Try again in a moment.</p>`), 500);
    }
  },

  /** Daily: set aside ads that have been waiting too long (the Mac's pushes do the same after each read). */
  async scheduled(event, env, ctx) {
    const desk = new Desk(env, { email: "" });
    ctx.waitUntil(api.expireOld(env.DB, (await desk.teamSettings()).expire_after_days ?? 30));
  },
};
