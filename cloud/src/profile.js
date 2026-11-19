// The "My details" page's data (port of desk/profile.py): each signed-in person's contact details (profile:<email>),
// the team's office email, the message templates and the style samples, all kept in the desk's kv table.
import * as drafts from "./drafts.js";
import { DISCLAIMER } from "./outreach.js";

export const DETAILS = [
  ["name", "Your name"], ["phone", "Phone"], ["email", "Email"], ["title_fr", "Title in French"],
  ["title_en", "Title in English"], ["agency", "Agency"], ["rental_page_url", "Rental page link"],
];
const AGENT = ["agent_name", "agent_phone"];
const MSG = [...AGENT, "city", "agent_title_fr", "agent_title_en", "agent_email", "agency", "rental_page_url"];
const RENEW = [...AGENT, "landlord_name", "address", "lease_end", "window_opens", "window_closes", "tal_paragraph"];
export const TEMPLATES = {
  outreach_fr: ["First message, French", MSG], outreach_en: ["First message, English", MSG],
  followup_fr: ["Follow-up, French", MSG], followup_en: ["Follow-up, English", MSG],
  renewal_landlord_fr: ["Lease renewal to the landlord, French", RENEW],
  renewal_landlord_en: ["Lease renewal to the landlord, English", RENEW],
};
export const SUGGEST = ["outreach_fr", "outreach_en"];

/** The {fill-ins} a template uses, like Python's string.Formatter().parse. Throws on an unpaired brace. */
export function fillIns(text) {
  const used = new Set();
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "{") {
      if (text[i + 1] === "{") { i++; continue; }
      const end = text.indexOf("}", i);
      const inner = end < 0 ? null : text.slice(i + 1, end);
      if (inner === null || inner.includes("{")) throw new SyntaxError("unpaired");
      used.add(inner.split(/[!:]/)[0]);
      i = end;
    } else if (c === "}") {
      if (text[i + 1] === "}") { i++; continue; }
      throw new SyntaxError("unpaired");
    }
  }
  return used;
}

export function checkTemplate(name, text) {
  if (!String(text).trim()) return ["The message is empty."];
  const allowed = TEMPLATES[name][1];
  let used;
  try { used = fillIns(text); } catch { return ["A curly bracket { or } is missing its pair. Fill-ins look like {agent_phone}."]; }
  const unknown = [...used].filter((f) => !allowed.includes(f)).map((f) => `{${f}}`).sort();
  const can = [...allowed].sort().map((a) => `{${a}}`).join(", ");
  return unknown.map((u) => `The desk doesn't know ${u}. It can fill in: ${can}`);
}

export async function saveDetails(desk, form) {
  const mine = { ...((await desk.profile()) || (await desk.teamSettings()).agent || {}) };
  for (const [key] of DETAILS) if (key in form) mine[key] = String(form[key]).trim();
  await desk.put("profile:" + desk.user.email.toLowerCase(), mine);
  if ("office_email" in form) {
    const s = (await desk.kv("desk_settings")) || {};
    s.office_email = String(form.office_email).trim();
    await desk.put("desk_settings", s);
  }
}

export async function saveTemplate(desk, name, text) {
  const problems = checkTemplate(name, text);
  if (!problems.length) {
    await desk.put("template:" + name, String(text).replace(/\r\n/g, "\n").trim() + "\n");
    await dropSuggestion(desk, name);
  }
  return problems;
}

export async function readSamples(desk) {
  return (await desk.kv("samples")) || "";
}

export async function saveSamples(desk, text) {
  await desk.put("samples", String(text).replace(/\r\n/g, "\n").trim() + "\n");
}

export async function suggestions(desk) {
  return (await desk.kv("suggestions")) || {};
}

export async function dropSuggestion(desk, name) {
  const s = await suggestions(desk);
  if (name in s) { delete s[name]; await desk.put("suggestions", s); }
}

function suggestPrompt(samples, name, current) {
  const lang = name.split("_").pop();
  const allowed = [...TEMPLATES[name][1]].sort();
  const shown = samples.map((s, i) => `<sample n="${i + 1}">\n${s.text}\n</sample>`).join("\n\n");
  return `You rewrite a real estate broker's standard first message to landlords who posted a rental ad themselves,
so it sounds like the broker. The desk fills in this message for every ad, so it must not mention any one ad.

Messages the broker wrote before (some may be in French, some in English; take the voice from all of them):
${shown}

The current standard message:
<current>
${current}
</current>

Rules:
- Write it in ${lang === "fr" ? "French (Québec)" : "English"}, in the broker's voice, length and structure.
- Keep what the current message offers; never invent facts, prices or promises.
- Use these fill-ins, written exactly like this, where the details go: ${allowed.map((a) => `{${a}}`).join(", ")}.
  Use {city} for the ad's city and {agent_phone} for the phone number. No other curly brackets.
- End with this line exactly: ${DISCLAIMER[lang]}
- Output only the message.`;
}

/** New first-message wording in French and English from the samples, kept as suggestions. Returns {name: problem}. */
export async function suggestTemplates(desk, settings) {
  const samples = drafts.loadSamples(await readSamples(desk));
  const run = drafts.runner(settings), sugg = await suggestions(desk), failed = {};
  for (const name of SUGGEST) {
    const lang = name.split("_").pop();
    let text;
    try {
      [text] = await run(suggestPrompt(drafts.pickSamples(samples, lang, settings), name, await desk.template(name)), "Write the standard message now.");
    } catch (e) {
      failed[name] = e.message;
      continue;
    }
    text = drafts.finish(text, lang);
    const problems = checkTemplate(name, text);
    if (problems.length) failed[name] = problems[0];
    else sugg[name] = text;
  }
  await desk.put("suggestions", sugg);
  return failed;
}
