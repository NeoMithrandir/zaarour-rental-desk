// Settings, config files and message text for the desk on Cloudflare.
//
// The Mac stays the source of truth for config/ and config/settings.json: each sync pushes them here (secrets
// stripped) into the kv table. Until the first sync, the copies bundled at deploy time apply. What people change
// on the desk itself (their contact details, the office email, the templates, the style samples) is kept here.
import EXAMPLE from "../../config/settings.example.json";
import AREAS from "../../config/areas.json";
import APPROACHES from "../../config/approaches.json";
import TAL_RATES from "../../config/tal_rates.json";
import BROKERS from "../../config/known_brokers.csv";
import outreach_fr from "../../templates/outreach_fr.txt";
import outreach_en from "../../templates/outreach_en.txt";
import followup_fr from "../../templates/followup_fr.txt";
import followup_en from "../../templates/followup_en.txt";
import renewal_landlord_fr from "../../templates/renewal_landlord_fr.txt";
import renewal_landlord_en from "../../templates/renewal_landlord_en.txt";
import { deepMerge, now } from "./util.js";

export const BUNDLED_TEMPLATES = { outreach_fr, outreach_en, followup_fr, followup_en, renewal_landlord_fr, renewal_landlord_en };
const BUNDLED = { areas: AREAS, approaches: APPROACHES, tal_rates: TAL_RATES, known_brokers: BROKERS };

export async function kvGet(db, key) {
  const r = await db.prepare("SELECT value FROM kv WHERE key=?").bind(key).first();
  if (!r) return null;
  try { return JSON.parse(r.value); } catch { return null; }
}

export function kvPut(db, key, value, by = "") {
  return db.prepare("INSERT INTO kv (key, value, updated_at, updated_by) VALUES (?,?,?,?) " +
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by")
    .bind(key, JSON.stringify(value), now(), by);
}

/** Everything one request needs, loaded once: settings, config files and templates. */
export class Desk {
  constructor(env, user) {
    this.env = env;
    this.db = env.DB;
    this.user = user || { email: "", name: "" };
    this.memo = {};
  }

  async kv(key) {
    if (!(key in this.memo)) this.memo[key] = await kvGet(this.db, key);
    return this.memo[key];
  }

  async put(key, value) {
    delete this.memo[key];
    delete this.memo._settings;
    await kvPut(this.db, key, value, this.user.email).run();
  }

  async config(name) {
    return (await this.kv("config:" + name)) ?? BUNDLED[name];
  }

  async areas() {
    return (await this.config("areas")).areas;
  }

  /** The team's settings: the example's defaults, the Mac's settings.json, then what was changed on the desk. */
  async teamSettings() {
    if (!this.memo._team) {
      let s = deepMerge(EXAMPLE, (await this.kv("mac_settings")) || {});
      s = deepMerge(s, (await this.kv("desk_settings")) || {});
      this.memo._team = s;
    }
    return this.memo._team;
  }

  /** The settings as the signed-in person sees them: their own contact details in agent. */
  async settings() {
    if (!this.memo._settings) {
      const s = structuredClone(await this.teamSettings());
      const mine = await this.profile();
      if (mine) s.agent = { ...(s.agent || {}), ...mine };
      if (this.env.ANTHROPIC_API_KEY) s.anthropic_api_key = this.env.ANTHROPIC_API_KEY;
      this.memo._settings = s;
    }
    return this.memo._settings;
  }

  async profile(email = this.user.email) {
    return email ? await this.kv("profile:" + email.toLowerCase()) : null;
  }

  async template(name) {
    const t = await this.kv("template:" + name);
    return typeof t === "string" ? t : BUNDLED_TEMPLATES[name];
  }
}
