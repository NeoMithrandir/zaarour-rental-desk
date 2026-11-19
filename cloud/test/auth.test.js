// The Worker checks Cloudflare Access's signed token itself, and the Mac's routes need the agent token as well.
import { test } from "node:test";
import assert from "node:assert/strict";
import { identify, verifyAccess } from "../src/auth.js";

const TEAM = "zaarour.cloudflareaccess.com", AUD = "aud-123";
const b64url = (buf) => Buffer.from(buf).toString("base64url");

let n = 0;
async function setup() {
  const kid = `k${++n}`; // a fresh key each test, as when Access rotates its keys
  const { publicKey, privateKey } = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid };
  globalThis.fetch = async () => new Response(JSON.stringify({ keys: [jwk] }));
  const sign = async (claims) => {
    const head = b64url(JSON.stringify({ alg: "RS256", kid })), body = b64url(JSON.stringify(claims));
    const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(`${head}.${body}`));
    return `${head}.${body}.${b64url(sig)}`;
  };
  return sign;
}

const env = { ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD, AGENT_TOKEN: "agent-secret", ALLOWED_EMAILS: "stefan@zaarour.ca,nesi@zaarour.ca" };
const exp = () => Math.floor(Date.now() / 1000) + 600;
const req = (path, headers = {}) => new Request("https://rentals.zaarour.ca" + path, { headers });

test("a person signed in through Access gets in, by email", async () => {
  const sign = await setup();
  const t = await sign({ aud: [AUD], email: "Stefan@zaarour.ca", exp: exp(), iss: `https://${TEAM}` });
  assert.deepEqual(await identify(req("/", { "Cf-Access-Jwt-Assertion": t }), env), { kind: "person", email: "stefan@zaarour.ca" });
});

test("bad tokens get nothing", async () => {
  const sign = await setup();
  const none = async (h) => (await identify(req("/", h), env)).kind;
  assert.equal(await none({}), "none");
  assert.equal(await none({ "Cf-Access-Jwt-Assertion": await sign({ aud: ["other"], email: "stefan@zaarour.ca", exp: exp() }) }), "none");
  assert.equal(await none({ "Cf-Access-Jwt-Assertion": await sign({ aud: [AUD], email: "stefan@zaarour.ca", exp: 1000 }) }), "none");
  assert.equal(await none({ "Cf-Access-Jwt-Assertion": await sign({ aud: [AUD], email: "someone@gmail.com", exp: exp() }) }), "none");
  const good = await sign({ aud: [AUD], email: "stefan@zaarour.ca", exp: exp() });
  assert.equal(await none({ "Cf-Access-Jwt-Assertion": good.slice(0, -4) + "AAAA" }), "none");
  assert.equal(await verifyAccess(good, { ...env, ACCESS_AUD: "" }), null); // not configured: closed
});

test("the Mac needs the service token's sign-in and the agent token", async () => {
  const sign = await setup();
  const svc = await sign({ aud: [AUD], common_name: "abc.access", exp: exp() });
  const person = await sign({ aud: [AUD], email: "stefan@zaarour.ca", exp: exp() });
  const kind = async (h) => (await identify(req("/api/agent/poll", h), env)).kind;
  assert.equal(await kind({ "Cf-Access-Jwt-Assertion": svc, Authorization: "Bearer agent-secret" }), "agent");
  assert.equal(await kind({ "Cf-Access-Jwt-Assertion": svc, Authorization: "Bearer wrong" }), "none");
  assert.equal(await kind({ Authorization: "Bearer agent-secret" }), "none");
  assert.equal(await kind({ "Cf-Access-Jwt-Assertion": person, Authorization: "Bearer agent-secret" }), "none");
});

test("the stand-in sign-in works only on this computer", async () => {
  const dev = { ...env, DEV_USER_EMAIL: "nesi@zaarour.ca" };
  assert.equal((await identify(new Request("http://localhost:8790/"), dev)).kind, "person");
  assert.equal((await identify(req("/"), dev)).kind, "none");
});
