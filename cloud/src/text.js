// Small text helpers shared by the checks: accent folding, address normalisation (port of desk/text.py).

const STREET_WORDS = {
  rue: "", street: "st", st: "st", avenue: "", av: "", ave: "",
  boulevard: "", boul: "", blvd: "", bd: "", chemin: "", ch: "",
  place: "", pl: "", croissant: "", crescent: "", cres: "",
  montee: "", rang: "", drive: "", dr: "", road: "", rd: "",
  de: "", du: "", des: "", la: "", le: "", l: "", d: "",
  o: "w", ouest: "w", west: "w", w: "w",
  e: "e", est: "e", east: "e",
  saint: "st", sainte: "ste",
};

const POSTAL_RE = /\b([A-Za-z]\d[A-Za-z])\s?(\d[A-Za-z]\d)?\b/g;

/** Lower-case and strip accents: 'Île-Bizard' -> 'ile-bizard'. */
export function fold(s) {
  return String(s || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().replace(/’/g, "'");
}

/** First three characters of a Québec postal code in the address, upper-case, or ''. */
export function postalFsa(address) {
  for (const m of String(address || "").matchAll(POSTAL_RE)) {
    const fsa = m[1].toUpperCase();
    if ("HJGK".includes(fsa[0])) return fsa;
  }
  return "";
}

/** City segment of a Kijiji-style address: '123 Rue X, Pierrefonds, QC H8Z 1V2' -> 'pierrefonds'. */
export function cityOf(address) {
  const ps = fold(address).split(",").map((p) => p.trim());
  for (let i = 0; i < ps.length; i++) {
    if (ps[i].startsWith("qc") || ps[i].startsWith("quebec")) return i > 0 ? ps[i - 1] : "";
  }
  if (ps.length >= 2) return ps[ps.length - 1];
  return ps.length && !/^\s*\d/.test(ps[0]) ? ps[0] : "";
}

/** Civic number + street words, for matching the same building across sources. '' without a civic number. */
export function addressKey(address) {
  const a = fold(address).split(",");
  let head = a[0];
  if (/^\s*\d+[a-z]?\s*$/.test(head) && a.length > 1) head = head + " " + a[1];
  head = head.trim().replace(/^(app|apt|unit|suite|#)\s*\S+\s+/, "");
  head = head.replace(/^\d+\s*-\s*(?=\d)/, "");
  const m = head.match(/^\s*(\d+)[a-z]?\b(.*)$/);
  if (!m) return "";
  const out = [];
  for (const w of m[2].match(/[a-z0-9]+/g) || []) {
    const w2 = Object.hasOwn(STREET_WORDS, w) ? STREET_WORDS[w] : w;
    if (w2) out.push(w2);
  }
  if (out.length > 1 && out[out.length - 1] === "st") out.pop();
  return [m[1], ...out].join(" ");
}
