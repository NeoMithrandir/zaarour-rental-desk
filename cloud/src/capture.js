// The capture button and the browser helpers on the online desk (port of desk/capture.py and desk/send.py's routes).
// The bookmarks open this desk's own address, whatever it is, so they work at rentals.zaarour.ca.
import { esc, sha1hex } from "./util.js";

const HOSTS = [
  [/(^|\.)facebook\.com$/, "facebook"], [/(^|\.)kijiji\.ca$/, "kijiji"], [/(^|\.)craigslist\.org$/, "craigslist"],
  [/(^|\.)rentals\.ca$/, "rentalsca"], [/(^|\.)(padmapper|zumper)\.com$/, "padmapper"],
  [/(^|\.)appartogo\.com$/, "appartogo"], [/(^|\.)lespac\.com$/, "lespac"], [/(^|\.)louer\.ca$/, "louer"],
  [/(^|\.)logisquebec\.com$/, "logisquebec"], [/(^|\.)liv\.rent$/, "livrent"], [/(^|\.)duproprio\.com$/, "duproprio"],
];

function split(url) {
  try { return new URL(url); } catch { return null; }
}

export function sourceOf(url) {
  const u = split(url || "");
  if (!u) return "";
  const host = u.hostname.toLowerCase();
  for (const [rx, key] of HOSTS) if (rx.test(host)) return key === "facebook" && u.pathname.startsWith("/groups/") ? "group" : key;
  return "";
}

/** The same ad gets the same id however it reached the desk (scan, alert email, capture or by hand). */
export async function canonicalId(url, source) {
  source = source || sourceOf(url);
  const u = split(url || "");
  const path = (u ? u.pathname : "").replace(/\/+$/, "");
  let m;
  if (source === "kijiji" && (m = path.match(/\/(\d+)$/))) return `kijiji:${m[1]}`;
  if (source === "craigslist" && path.includes("/d/")) return `craigslist:${path.split("/").pop()}`;
  if (source === "logisquebec" && (m = path.match(/-l(\d+)$/))) return `logisquebec:${m[1]}`;
  if (source === "livrent" && path.includes("/building/")) return `livrent:${path.split("/building/").pop().split("/")[0]}`;
  if (source === "appartogo" && (m = path.match(/\/(\d+)$/))) return `appartogo:${m[1]}`;
  if (source === "lespac" && (m = path.match(/_(\d+)D\d+\.jsa$/))) return `lespac:${m[1]}`;
  if (source === "facebook" && (m = path.match(/\/marketplace\/item\/(\d+)/))) return `facebook:${m[1]}`;
  if (!url) return null;
  // Python's urlunsplit with no query or fragment.
  const clean = u ? `${u.protocol}//${u.host}${u.pathname === "/" && !/\/$/.test(url.split(/[?#]/)[0]) ? "" : u.pathname}` : url.split(/[?#]/)[0];
  return `${source || "other"}:` + (await sha1hex(clean)).slice(0, 12);
}

/** A bookmarklet from one of the desk's scripts, pointing at this desk. */
export function bookmarklet(js, origin) {
  js = js.replace("__DESK__", `${origin}/capture`);
  js = js.replace(/^\s*\/\/.*$/gm, "").replace(/\s*\n\s*/g, " ").trim();
  const safe = new Set("(){}[]=>;:,.'!*+-/&|?_$~");
  let out = "";
  for (const ch of js) {
    if (/[A-Za-z0-9]/.test(ch) || safe.has(ch)) out += ch;
    else out += [...new TextEncoder().encode(ch)].map((b) => "%" + b.toString(16).toUpperCase().padStart(2, "0")).join("");
  }
  return "javascript:" + out;
}

export function installBody(captureLink, fillLink) {
  return `<h1>The capture button</h1>
<p class="lede">For boards the desk doesn't read by itself: Facebook Marketplace and groups, Rentals.ca, PadMapper/Zumper,
Appartogo, LesPAC and Louer.ca. Browse them as usual. On an ad worth a message, click the button: the desk opens a small
window with the ad filled in, you check it and click Add. It goes through the same checks as every other ad.</p>
<ol class="steps">
<li><strong>Show the bookmarks bar</strong> in your browser (Chrome: ⌘⇧B. Safari: ⌘⇧B, or View, Show Favourites Bar).</li>
<li><strong>Drag this button onto the bar:</strong> <a class="btn primary" href="${esc(captureLink)}" onclick="event.preventDefault(); alert('Drag this button onto your bookmarks bar, then click it on an ad.');">Send to desk</a></li>
<li><strong>On an ad</strong>, optionally select the ad's description with the mouse (it's used as the ad text), then click
<b>Send to desk</b> on the bar. You need to be signed in to the desk in the same browser.</li>
</ol>
<p class="muted">The button reads only the page you are looking at, only when you click it, and only what is on screen. It does not
open other pages, sign in, or read your messages or contacts. If you dragged it onto the bar before the desk moved online,
drag this one again: the old one still points at the Mac.</p>

<h2 id="fill">Filling the board's message box</h2>
<p class="lede">When you approve a message, the desk copies it and opens the ad. With the desk's Chrome extension, the
board's message box fills by itself as soon as it's on the page (on Kijiji, right away; elsewhere, once you click Send
message, Contact or Reply). You read it and click the board's own Send.</p>
<ol class="steps">
<li><strong>Download the extension:</strong> <a class="btn primary" href="/extension.zip" download>Zaarour desk extension (.zip)</a>, then double-click the file to unzip it.</li>
<li><strong>Once, in Chrome:</strong> open <code>chrome://extensions</code>, turn on <b>Developer mode</b> (top right), click
<b>Load unpacked</b> and choose the unzipped folder. Keep the folder where it is (Chrome reads it from there).</li>
<li><strong>That's it.</strong> Approve on the desk, and the ad opens with the message in its box and a green note saying so.
It also fills your name, email and phone where the form asks for them and they're empty.</li>
</ol>
<p class="muted">The extension works with the desk wherever it runs: it acts only on ads the desk opened (it reads the message
the desk put after # in the ad's address, which stays in your browser). It never clicks Send, never signs in, and reads nothing
else on the page. If the board sends you to its sign-in page first, the message waits in that tab for 15 minutes.</p>

<h3>Without the extension: the Fill button</h3>
<p class="lede">In Safari, or a Chrome without the extension: open the board's message box (Send message, Contact,
Reply), click <b>Fill from desk</b> on the bar, read the message, then click the board's own Send.</p>
<ol class="steps">
<li><strong>Drag this button onto the bar:</strong> <a class="btn primary" href="${esc(fillLink)}" onclick="event.preventDefault(); alert('Drag this button onto your bookmarks bar, then click it in a board\\'s message box.');">Fill from desk</a></li>
<li><strong>On the ad</strong>, signed in to the board as yourself, open its message box and click <b>Fill from desk</b>. It fills
the message, and your name, email and phone where the form asks for them and they're empty.</li>
<li><strong>Read it and click the board's Send.</strong> If the board asks for a CAPTCHA, answer it. Back on the desk, say whether it went.</li>
</ol>
<p class="muted">The Fill button never clicks Send and never signs in. It takes the message from the ad's address (the desk adds it
after #, which stays in your browser), or from your clipboard if the board's sign-in page dropped it.</p>`;
}

// How an approved message reaches the landlord on each board (desk/send.py ROUTES): shown in the composer.
export const ROUTES = {
  kijiji: { name: "Kijiji", where: "the Send message box on the ad", needs: ["Kijiji account, signed in"], checked: "", max_chars: null, max_chars_checked: "2026-11-19" },
  craigslist: { name: "Craigslist", where: "Reply, then email: Craigslist gives a relay address, the message goes from your mailbox", needs: ["a CAPTCHA before the reply address shows", "your mail app or webmail"], checked: "" },
  logisquebec: { name: "LogisQuébec", where: "the contact form on the ad (name, email, phone, message)", needs: ["to confirm: possible CAPTCHA"], checked: "" },
  livrent: { name: "liv.rent", where: "the in-app chat with the landlord", needs: ["liv.rent account, signed in"], checked: "" },
  appartogo: { name: "Appartogo", where: "the contact form on the ad", needs: ["to confirm: account or CAPTCHA"], checked: "" },
  lespac: { name: "LesPAC", where: "Contacter l'annonceur on the ad", needs: ["LesPAC account, signed in"], checked: "" },
  facebook: { name: "Facebook Marketplace", where: "Message seller (Messenger)", needs: ["Facebook account, signed in"], checked: "" },
  group: { name: "Facebook group", where: "a Messenger message to the poster", needs: ["Facebook account, signed in"], checked: "" },
  rentalsca: { name: "Rentals.ca", where: "the contact form on the ad", needs: ["bot check on every page"], checked: "" },
  padmapper: { name: "PadMapper / Zumper", where: "the contact form on the ad", needs: ["account and bot check"], checked: "" },
  louer: { name: "Louer.ca", where: "the contact form on the ad", needs: ["to confirm"], checked: "" },
  duproprio: { name: "DuProprio", where: "the contact form on the ad", needs: ["to confirm"], checked: "" },
};
const OTHER = { name: "the board", where: "the board's message box", needs: [], checked: "" };
export const route = (source) => ROUTES[source] || OTHER;
