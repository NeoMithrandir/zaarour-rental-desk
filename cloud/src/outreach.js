// Outreach messages (plan items 1.5, 1.6, 1.10): pick the language, fill the template (port of desk/outreach.py).
import { fold } from "./text.js";
import { formatMap, sha1hex } from "./util.js";

export const DISCLAIMER = {
  fr: "Si vous êtes déjà sous contrat avec un courtier, veuillez ignorer ce message.",
  en: "If you're already under contract with a broker, please disregard this message.",
};

const FR = /\b(a louer|logement|appartement|chambres?|libre|inclus|electromenagers|disponible|le|la|les|et|avec|pres|metro|stationnement)\b/g;
const EN = /\b(for rent|apartment|available|included|bedrooms?|the|and|with|near|parking|utilities)\b/g;

export function detectLang(text) {
  const t = fold(text);
  const fr = (t.match(FR) || []).length, en = (t.match(EN) || []).length;
  return en > fr ? "en" : "fr"; // Québec default: French on a tie
}

function cityFor(address, area) {
  const ps = String(address || "").split(",").map((p) => p.trim());
  let city = "";
  for (let i = 0; i < ps.length; i++) {
    if (ps[i].toUpperCase().startsWith("QC") && i > 0) { city = ps[i - 1]; break; }
  }
  if (!city || /^\d/.test(city)) city = String(area || "").split(" / ")[0];
  if (["montreal", "montréal"].includes(fold(city)) && area) city = area.split(" / ")[0];
  return city || "votre secteur";
}

/** Renders templates for one request. desk: config.Desk (templates come from the desk, then the bundled copies). */
export class Outreach {
  constructor(desk, settings) {
    this.desk = desk;
    this.settings = settings;
    this.cache = {};
  }

  async load(kind, lang) {
    const name = `${kind}_${lang}`;
    if (!(name in this.cache)) this.cache[name] = String(await this.desk.template(name)).trim();
    return this.cache[name];
  }

  /** 'outreach_fr@1a2b3c4d': the template plus a hash of its text, so rewordings are told apart. */
  async templateId(kind, lang) {
    return `${kind}_${lang}@` + (await sha1hex(await this.load(kind, lang))).slice(0, 8);
  }

  async render(lead, lang, kind = "outreach") {
    lang = lang || lead.lang || "fr";
    const a = this.settings.agent || {};
    const values = {
      city: cityFor(lead.address, lead.area), agent_name: a.name || "", agent_title_fr: a.title_fr || "",
      agent_title_en: a.title_en || "", agent_phone: a.phone || "", agent_email: a.email || "",
      agency: a.agency || "", rental_page_url: a.rental_page_url || "",
    };
    let msg = formatMap(await this.load(kind, lang), values);
    // The disclaimer is required on every solicitation (1.6): never let an edited template drop it.
    if (!msg.includes(DISCLAIMER[lang])) msg = msg.trimEnd() + "\n\n" + DISCLAIMER[lang];
    return msg;
  }
}
