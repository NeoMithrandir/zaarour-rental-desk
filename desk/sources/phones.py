"""Landlords' phone numbers, on boards whose permission covers collecting contact details.

Off unless the board's `permissions` entry has `allow_contact_capture` and says in `contact_basis` where the
permission covers it. Two ways in:

- printed: numbers the poster wrote in the ad's own title or text, kept as the ad is read (no extra request);
- revealed: on boards listed under `contacts.reveal`, the number behind the ad page's "show number" button, clicked
  in a local browser (Playwright) for ads waiting in review that have no number yet, a few per read, well spaced.

The browser identifies as the desk, obeys robots.txt and the board's cool-offs, and never signs in. At a CAPTCHA,
bot check or sign-in prompt it keeps nothing from that page and leaves the board alone for `blocked_days`.
Numbers are stored only in the local database (leads.phone).
"""
import re
import time
import urllib.parse

from .details import PHONE_RE, _DIGIT
from .http import HostCooling

WORDS = {w: str(i) for i, ws in enumerate((("zero", "zéro", "zero"), ("one", "un"), ("two", "deux"), ("three", "trois"),
                                           ("four", "quatre"), ("five", "cinq"), ("six",), ("seven", "sept"),
                                           ("eight", "huit"), ("nine", "neuf"))) for w in ws}
# A request to one of these after the click means the reveal sits behind a bot check: the desk stops there.
BOT_CHECK_RE = re.compile(r"recaptcha/(?:api2|enterprise)/(?:reload|userverify)|challenges\.cloudflare\.com|"
                          r"hcaptcha\.com|arkoselabs|funcaptcha|geo\.captcha-delivery|perimeterx|px-captcha", re.I)
BOT_PAGE_RE = re.compile(r"captcha|are you a robot|êtes-vous un robot|verify you are human|vérifiez que vous êtes|"
                         r"unusual traffic|access denied|accès refusé", re.I)


def allowed(settings, source):
    """True when the desk may keep contact details from this board (its scanner permission is checked separately)."""
    p = (settings.get("permissions") or {}).get(source) or {}
    return bool(p.get("allow_contact_capture")) and bool(str(p.get("contact_basis") or "").strip())


def normalize(raw):
    """'(514) 555-1234', '514.555.1234', '514Two66One355' -> '514-555-1234'; None unless it is a North American number."""
    digits = "".join(WORDS.get(t.lower(), t) for t in re.findall(_DIGIT, raw, re.I))
    if len(digits) >= 11 and digits[0] == "1":
        digits = digits[1:]
    # A match can run on into the rent after the number ("(438) 555 0199. 1 650 $"): the first ten digits are the number.
    digits = digits[:10]
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def in_text(*texts):
    """The phone numbers written in an ad, in order, without repeats."""
    out = []
    for text in texts:
        for m in PHONE_RE.finditer(text or ""):
            n = normalize(m.group(0))
            if n and n not in out:
                out.append(n)
    return out


def save(conn, lead_id, numbers, via):
    """Keep numbers on the lead. A revealed number is never replaced by one read from the text."""
    if not numbers:
        return False
    row = conn.execute("SELECT phone, phone_via FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row or (row["phone_via"] == "revealed" and via != "revealed"):
        return False
    have = [p for p in (row["phone"] or "").split(", ") if p]
    merged = list(dict.fromkeys(numbers + have if via == "revealed" else have + numbers))
    if merged == have and (row["phone_via"] or via) == via:
        return False
    conn.execute("UPDATE leads SET phone=?, phone_via=? WHERE id=?",
                 (", ".join(merged), "revealed" if via == "revealed" else row["phone_via"] or via, lead_id))
    return True


def keep_printed(conn, source, leads, settings):
    """After a read: keep the numbers written in each ad's title and text."""
    if not allowed(settings, source):
        return 0
    n = 0
    for lead in leads:
        n += save(conn, lead["id"], in_text(lead.get("title"), lead.get("check_text") or lead.get("snippet")), "ad text")
    conn.commit()
    return n


class Stop(Exception):
    """The board showed a CAPTCHA, a bot check or a sign-in: leave it alone."""


def _open_browser(user_agent):  # pragma: no cover - needs Playwright and a browser on this computer
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(user_agent=user_agent, locale="fr-CA")

    def close():
        context.close()
        browser.close()
        pw.stop()
    return context, close


def reveal_one(page, url, button, wait=time.sleep):  # pragma: no cover - drives a real browser
    """Open one ad, click its show-number button, and return the numbers that appeared (empty if none)."""
    hits = []
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    wait(2)
    if BOT_PAGE_RE.search(page.title() or "") or page.locator("iframe[src*='captcha'], iframe[src*='challenges']").count():
        raise Stop("showed a bot check when opening the ad")
    before = set(in_text(page.inner_text("body")))
    target = page.get_by_role("button", name=re.compile(button, re.I))
    if not target.count():
        target = page.get_by_text(re.compile(button, re.I))
    if not target.count():
        return []
    page.on("request", lambda r: hits.append(r.url) if BOT_CHECK_RE.search(r.url) else None)
    target.first.click(timeout=10000)
    wait(3)
    if hits:
        raise Stop("runs a bot check behind its show-number button")
    if page.locator("input[type=password]").count() or page.locator("iframe[src*='captcha'], iframe[src*='challenges']").count():
        raise Stop("asked to sign in or prove it is human to show the number")
    shown = in_text(page.inner_text("body"))
    tel = [normalize(h.split(":", 1)[1]) for h in page.eval_on_selector_all("a[href^='tel:']", "els => els.map(e => e.href)")]
    return [n for n in [t for t in tel if t] + shown if n not in before]


def reveal(conn, settings, fetcher, log=print, open_browser=_open_browser, reveal_one=reveal_one, sleep=time.sleep):
    """Click show-number on boards that allow it, for review ads with no number yet. Returns numbers found."""
    from . import http
    cfg = settings.get("contacts") or {}
    boards = [b for b in (cfg.get("reveal") or {}) if allowed(settings, b)]
    if not boards:
        return 0
    per_run, gap = int(cfg.get("max_reveals_per_run", 10)), float(cfg.get("min_interval_seconds", 90))
    found, opened = 0, None
    try:
        for board in boards:
            rows = conn.execute("SELECT id, url FROM leads WHERE source=? AND status='new' AND bucket='review' "
                                "AND COALESCE(phone, '')='' AND phone_checked_at IS NULL AND url LIKE 'http%' "
                                "ORDER BY first_seen DESC LIMIT ?", (board, per_run)).fetchall()
            if not rows:
                continue
            try:
                if opened is None:
                    try:
                        opened = open_browser(http.user_agent(settings))
                    except ImportError:
                        log("  ! Phone numbers: Playwright is not installed (python3 -m pip install playwright, "
                            "then python3 -m playwright install chromium)")
                        return found
                page = opened[0].new_page()
                got = 0
                for i, row in enumerate(rows):
                    host = urllib.parse.urlsplit(row["url"]).netloc.lower()
                    fetcher._check_cooling(host)
                    fetcher._check_cooling(host + " show-number")  # a bot check met earlier stops only the reveal
                    if not fetcher.robots(host).allowed(row["url"]):
                        log(f"  {board}: robots.txt asks robots not to open its ads; no numbers revealed")
                        break
                    if i:
                        sleep(gap)
                    try:
                        numbers = reveal_one(page, row["url"], cfg["reveal"][board]["button"])
                    except Stop as e:
                        fetcher._cool(host + " show-number", float(cfg.get("blocked_days", 30)) * 86400, f"{e}; phone reveal stopped")
                        log(f"  ! {board} {e}. The desk does not get past those: no more numbers revealed there "
                            f"for {cfg.get('blocked_days', 30)} days")
                        break
                    except Exception as e:  # a removed ad or a page change: skip it
                        log(f"  ! {board} phone reveal on {row['id']}: {e}")
                        numbers = []
                    conn.execute("UPDATE leads SET phone_checked_at=? WHERE id=?",
                                 (time.strftime("%Y-%m-%dT%H:%M:%S"), row["id"]))
                    if save(conn, row["id"], numbers, "revealed"):
                        got += 1
                    conn.commit()
                page.close()
                found += got
                log(f"  {board}: {got} phone number{'s' if got != 1 else ''} revealed")
            except HostCooling as e:
                log(f"  {board} phone reveal: {e}")
    finally:
        if opened:
            opened[1]()
    return found
