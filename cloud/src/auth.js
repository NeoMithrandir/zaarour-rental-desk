// Who is asking. Cloudflare Access stands in front of the desk and lets in only the team's zaarour.ca addresses
// (the "Rentals desk" Access app). Every request it lets through carries a signed token (Cf-Access-Jwt-Assertion);
// the Worker checks that signature too, so a request that somehow skipped Access gets nothing.
//
// People: the token names their email, which becomes "who decided" on each lead and picks their own contact details.
// The Mac: an Access service token gets it past Access, and the AGENT_TOKEN secret proves it is the desk's Mac.
// On this computer (wrangler dev), DEV_USER_EMAIL in .dev.vars stands in for Access, and only on localhost.
import { sameSecret } from "./util.js";

const certs = { url: "", keys: null, at: 0 };

function b64urlBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
const b64urlJson = (s) => JSON.parse(new TextDecoder().decode(b64urlBytes(s)));

function teamUrl(env) {
  const t = String(env.ACCESS_TEAM_DOMAIN || "").trim().replace(/\/+$/, "");
  if (!t) return "";
  return t.startsWith("https://") ? t : `https://${t.includes(".") ? t : t + ".cloudflareaccess.com"}`;
}

async function keys(env, fresh = false) {
  const url = teamUrl(env) + "/cdn-cgi/access/certs";
  if (!fresh && certs.url === url && certs.keys && Date.now() - certs.at < 3600000) return certs.keys;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Access certs: HTTP ${r.status}`);
  certs.keys = (await r.json()).keys || [];
  certs.url = url; certs.at = Date.now();
  return certs.keys;
}

/** The checked claims of an Access token, or null. */
export async function verifyAccess(token, env) {
  const aud = String(env.ACCESS_AUD || "").trim();
  if (!token || !aud || !teamUrl(env)) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  let head, claims;
  try { head = b64urlJson(parts[0]); claims = b64urlJson(parts[1]); } catch { return null; }
  if (head.alg !== "RS256") return null;
  let jwk = (await keys(env)).find((k) => k.kid === head.kid);
  if (!jwk) jwk = (await keys(env, true)).find((k) => k.kid === head.kid); // Access rotated its keys
  if (!jwk) return null;
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlBytes(parts[2]), new TextEncoder().encode(parts[0] + "." + parts[1]));
  if (!ok) return null;
  const nowS = Date.now() / 1000;
  if (typeof claims.exp !== "number" || claims.exp < nowS - 30) return null;
  if (typeof claims.nbf === "number" && claims.nbf > nowS + 30) return null;
  const auds = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!auds.includes(aud)) return null;
  if (claims.iss && claims.iss !== teamUrl(env)) return null;
  return claims;
}

const isLocal = (url) => ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);

function allowed(env, email) {
  const list = String(env.ALLOWED_EMAILS || "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  return !list.length || list.includes(email);
}

/**
 * {kind: "person", email} | {kind: "agent"} | {kind: "none", why}.
 * Agent routes need the AGENT_TOKEN as well as getting through Access (with the service token).
 */
export async function identify(request, env) {
  const url = new URL(request.url);
  const agentRoute = url.pathname.startsWith("/api/agent/");
  const bearer = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");

  if (isLocal(url) && env.DEV_USER_EMAIL) { // wrangler dev on this computer only
    if (agentRoute) return sameSecret(bearer, env.AGENT_TOKEN) ? { kind: "agent" } : { kind: "none", why: "agent token" };
    return { kind: "person", email: String(env.DEV_USER_EMAIL).toLowerCase() };
  }
  let claims = null;
  try {
    claims = await verifyAccess(request.headers.get("Cf-Access-Jwt-Assertion"), env);
  } catch (e) {
    return { kind: "none", why: `couldn't check the sign-in (${e.message})` };
  }
  if (!claims) return { kind: "none", why: "not signed in through Cloudflare Access" };
  if (agentRoute) {
    // The Mac signs in with a service token (no email); a person's browser can't reach these routes.
    if (claims.email) return { kind: "none", why: "agent route" };
    return sameSecret(bearer, env.AGENT_TOKEN) ? { kind: "agent" } : { kind: "none", why: "agent token" };
  }
  const email = String(claims.email || "").toLowerCase();
  if (!email) return { kind: "none", why: "no email in the sign-in" };
  if (!allowed(env, email)) return { kind: "none", why: "not on the desk's list" };
  return { kind: "person", email };
}
