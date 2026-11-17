"""Landlords' phone numbers, on boards whose permission covers collecting contact details.

Off unless the board's `permissions` entry has `allow_contact_capture` and says in `contact_basis` where the
permission covers it, on top of the board's own permission to be read. Two ways in:

- printed: numbers the poster wrote in the ad's own title or text, kept as the ad is read (no extra request);
- revealed: on boards listed under `contacts.reveal`, the number behind the ad page's "show number" button, clicked
  in a local browser (Playwright) for ads waiting in review that have no number yet, a few per read, well spaced.

The browser identifies as the desk, obeys robots.txt, the board's cool-offs and its permission's limits, and never
signs in. At a CAPTCHA, a bot check (shown on the page or fired by the click) or a sign-in it keeps nothing from that
page and leaves the reveal alone on that board for `blocked_days`. Every page it opens is logged in the reveal_log
table, which also holds the stops, so they hold across runs and processes. Numbers are stored only in the local
database (leads.phone).
"""
import fcntl
import re
import time
import urllib.parse
from datetime import datetime, timedelta

from .details import find_phones, nanp

# A request to one of these after the click means the reveal sits behind a bot check: the desk stops there.
BOT_CHECK_RE = re.compile(r"recaptcha/(?:api2|enterprise)/(?:reload|userverify)|challenges\.cloudflare\.com|"
                          r"/cdn-cgi/challenge-platform|hcaptcha\.com|arkoselabs|funcaptcha|captcha-delivery|"
                          r"datadome\.co|perimeterx|px-captcha|px-cloud\.net|px-cdn\.net|geetest|awswaf|"
                          r"friendlycaptcha|/_sec/cp_challenge|kpsdk", re.I)
# Response headers bot-check services put on a challenge.
CHALLENGE_HEADERS = ("cf-mitigated", "x-amzn-waf-action", "x-datadome", "x-datadome-cid")
# A page title that is a challenge or a block rather than the ad.
BOT_TITLE_RE = re.compile(r"captcha|are you a robot|êtes-vous un robot|not a robot|human|humain|just a moment|"
                          r"un instant|checking your browser|attention required|security check|vérification|"
                          r"access denied|accès refusé|unusual traffic|pardon our interruption", re.I)
# Text only a challenge page carries (ordinary ad copy can say "just a moment" or "access denied").
BOT_BODY_RE = re.compile(r"verify you are human|vérifie[rz]? que vous êtes (?:un )?humain|confirm you are (?:a )?human|"
                         r"i'?m not a robot|je ne suis pas un robot|checking your browser before|"
                         r"vérification de votre navigateur|unusual traffic from your|enable javascript and cookies to continue|"
                         r"access to this page has been denied|pardon our interruption", re.I)
# Visible challenge frames (not the invisible reCAPTCHA badge many sites load on every page).
CHALLENGE_FRAMES = ("iframe[src*='recaptcha/api2/bframe'], iframe[src*='recaptcha/enterprise/bframe'], "
                    "iframe[src*='recaptcha/api2/anchor']:not([src*='size=invisible']), "
                    "iframe[src*='recaptcha/enterprise/anchor']:not([src*='size=invisible']), "
                    "iframe[src*='hcaptcha.com'], iframe[src*='challenges.cloudflare.com'], iframe[src*='captcha-delivery'], "
                    "iframe[src*='geetest'], iframe[src*='arkoselabs']")
SIGN_IN_RE = re.compile(r"sign in|log in|login|connexion|connectez-vous|se connecter|create an account|"
                        r"créer un compte|continue with google|continuer avec google", re.I)


def allowed(settings, source):
    """True when the desk may keep contact details from this board: it is a board the desk reads, its permission
    to be read is in force, and allow_contact_capture is on with contact_basis saying where that permission covers
    contact details."""
    from ..scan import SCANNERS, permission
    p = (settings.get("permissions") or {}).get(source) or {}
    return (source in SCANNERS and permission(settings, source) is None and bool(p.get("allow_contact_capture"))
            and bool(str(p.get("contact_basis") or "").strip()))


def in_text(*texts):
    """The North American phone numbers written in an ad, in order, without repeats."""
    return list(dict.fromkeys(n for text in texts for _s, _e, n in find_phones(text) if n))


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
    """After a read: keep the numbers written in each ad's title and text (check_* hold the text before blanking)."""
    if not allowed(settings, source):
        return 0
    n = 0
    for lead in leads:
        n += save(conn, lead["id"], in_text(lead.get("check_title") or lead.get("title"),
                                             lead.get("check_text") or lead.get("snippet")), "ad text")
    conn.commit()
    return n


class Stop(Exception):
    """The board showed a CAPTCHA, a bot check, a refusal or a sign-in: leave the reveal alone there."""


class Cooling(Exception):
    """The board asked the desk to slow down (429/503): try again after `seconds`."""

    def __init__(self, why, seconds):
        super().__init__(why)
        self.seconds = seconds


class Gone(Exception):
    """The ad is no longer there (404/410): nothing to reveal, ever."""


class Skip(Exception):
    """This ad could not be checked this time (error page, network): try it again on a later read."""


# ------------------------------------------------------------------ the browser
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


def _tel_links(scope):  # pragma: no cover
    return [n for n in (nanp(re.sub(r"\D", "", h.split(":", 1)[1]))
                        for h in scope.locator("a[href^='tel:']").evaluate_all("els => els.map(e => e.href)")) if n]


def _blocked(page):  # pragma: no cover
    """Why the page in front of the desk is a challenge or a sign-in rather than the ad, or None."""
    if BOT_TITLE_RE.search(page.title() or "") or BOT_BODY_RE.search(page.inner_text("body")[:5000]):
        return "showed a bot check"
    if any(f.is_visible() for f in page.locator(CHALLENGE_FRAMES).all()):
        return "showed a CAPTCHA"
    if any(f.is_visible() for f in page.locator("input[type=password]").all()):
        return "asked to sign in"
    for d in page.locator("[role=dialog], [aria-modal=true], dialog[open]").all():
        if d.is_visible() and SIGN_IN_RE.search(d.inner_text()[:2000]):
            return "asked to sign in"
    return None


def reveal_one(context, url, button):  # pragma: no cover - drives a real browser
    """Open one ad, click its show-number button, and return the numbers that appeared next to it (empty if none).

    Raises Stop at any challenge, refusal or sign-in, Cooling when asked to slow down, Gone for a removed ad and
    Skip when the ad could not be read this time."""
    from .http import _retry_after
    seen, flagged, opened = [], [], []  # every request of the visit, so the bot-check test sees all of them
    on_request = lambda r: seen.append(r.url)  # noqa: E731
    on_response = lambda r: flagged.append(r.url) if any(h in r.headers for h in CHALLENGE_HEADERS) else None  # noqa: E731
    on_page = lambda p: opened.append(p)  # noqa: E731
    context.on("request", on_request)
    context.on("response", on_response)
    context.on("page", on_page)
    page = context.new_page()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = resp.status if resp else 0
        page.wait_for_timeout(2000)  # Playwright's own wait: page events keep arriving meanwhile
        why = "showed a bot check" if flagged else _blocked(page)
        if why:  # whatever the status: a challenge served as 503 is still a challenge
            raise Stop(why + " when the ad opened")
        if status in (429, 503):
            wait = _retry_after((resp.headers or {}).get("retry-after"), time.time())
            raise Cooling(f"asked the desk to slow down ({status})", wait if wait is not None else 3600)
        if status in (401, 403, 405, 407, 451):
            raise Stop(f"refused the browser ({status})")
        if status in (404, 410):
            raise Gone(f"ad removed ({status})")
        if status == 202 or not 200 <= status < 300:  # 202: a silent challenge page
            raise Skip(f"answered {status}")
        target = page.get_by_role("button", name=re.compile(button, re.I))
        if not target.count():
            target = page.get_by_text(re.compile(button, re.I))
        if not target.count():
            return []
        # The number is read only next to the button (its container three levels up), never elsewhere on the page.
        box = target.first.locator("xpath=ancestor::*[3]")
        before = set(in_text(box.inner_text())) | set(_tel_links(box))
        where = urllib.parse.urlsplit(page.url)[:3]
        mark, pages_before = len(seen), len(opened)
        target.first.click(timeout=10000)
        page.wait_for_timeout(3000)
        if urllib.parse.urlsplit(page.url)[:3] != where or len(opened) > pages_before:
            raise Stop("left the ad when the show-number button was clicked (likely a sign-in)")
        why = _blocked(page)
        if why:
            raise Stop(why + " to show the number")
        shown = _tel_links(box) + in_text(box.inner_text()) if box.count() else []
        page.wait_for_timeout(500)
        if flagged or any(BOT_CHECK_RE.search(u) for u in seen[mark:]):  # checked last, once every event has arrived
            raise Stop("runs a bot check behind its show-number button")
        return list(dict.fromkeys(n for n in shown if n not in before))
    except (Stop, Cooling, Gone, Skip):
        raise
    except Exception as e:  # timeouts, a crashed tab
        raise Skip(str(e).splitlines()[0][:200] if str(e) else type(e).__name__) from e
    finally:
        context.remove_listener("request", on_request)
        context.remove_listener("response", on_response)
        context.remove_listener("page", on_page)
        for p in opened + [page]:
            try:
                p.close()
            except Exception:
                pass


# ------------------------------------------------------------------ the run
def _log(conn, board, pages=0, numbers=0, stop_until=None, note=None, lead_id=None):
    conn.execute("INSERT INTO reveal_log (board, at, pages, numbers, stop_until, note, lead_id) VALUES (?,?,?,?,?,?,?)",
                 (board, datetime.now().isoformat(timespec="seconds"), pages, numbers, stop_until, note, lead_id))
    conn.commit()


def stopped(conn, board, now=None):
    """Why the reveal is off on this board for now (a bot check met earlier, or a cool-off), or None."""
    r = conn.execute("SELECT stop_until, note FROM reveal_log WHERE board=? AND stop_until IS NOT NULL "
                     "ORDER BY stop_until DESC LIMIT 1", (board,)).fetchone()
    if r and r["stop_until"] > (now or datetime.now()).isoformat(timespec="seconds"):
        return f"{r['note']}; left alone until {r['stop_until'][:16].replace('T', ' ')}"
    return None


def _checked(conn, lead_id):
    conn.execute("UPDATE leads SET phone_checked_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), lead_id))


def reveal(conn, settings, fetcher, log=print, only=None, open_browser=_open_browser, reveal_one=reveal_one,
           sleep=time.sleep, clock=time.time, lock_path=None):
    """Click show-number on boards that allow it, for review ads with no number yet. Returns numbers found.

    `only` limits it to the boards just read. One reveal at a time on this computer (a lock file). Each page is
    spaced from the last request to that board (the scanner's or the reveal's, this run or an earlier one) and
    counted against the permission's hourly limit."""
    from . import http
    from ..config import DATA_DIR
    from ..scan import HOSTS, requests_last_hour
    cfg = settings.get("contacts") or {}
    boards = [b for b in (cfg.get("reveal") or {})
              if (only is None or b in only) and b in HOSTS and allowed(settings, b)]
    if not boards:
        return 0
    lock_path = lock_path or DATA_DIR / "phones.lock"
    lock_path.parent.mkdir(exist_ok=True)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        log("  Phone numbers: another reveal is running, skipping this one")
        return 0
    found, opened = 0, None
    max_skips = int(cfg.get("max_failures_per_ad", 3))
    try:
        for board in boards:
            why = stopped(conn, board)
            if why:
                log(f"  {board} phone reveal: {why}")
                continue
            terms = (settings.get("permissions") or {}).get(board) or {}
            rows = conn.execute("SELECT id, url FROM leads WHERE source=? AND status='new' AND bucket='review' "
                                "AND COALESCE(phone, '')='' AND phone_checked_at IS NULL AND url LIKE 'http%' "
                                "ORDER BY first_seen DESC LIMIT ?", (board, int(cfg.get("max_reveals_per_run", 10)))).fetchall()
            if not rows:
                continue
            got, skips_in_a_row = 0, 0
            for row in rows:
                host = urllib.parse.urlsplit(row["url"]).netloc.lower()
                if host not in HOSTS[board]:
                    continue
                if terms.get("max_requests_per_hour") and requests_last_hour(conn, board) >= int(terms["max_requests_per_hour"]):
                    log(f"  {board} phone reveal: the permission's hourly limit is used up, next read later")
                    break
                try:
                    fetcher._check_cooling(host)
                except http.HostCooling as e:
                    log(f"  {board} phone reveal: {e}")
                    break
                robots = fetcher.robots(host)
                if not robots.allowed(row["url"]):
                    log(f"  {board}: robots.txt asks robots not to open its ads; no numbers revealed")
                    break
                # Spaced from the last request to the board: the reveal's (any run) and the scanner's (this process).
                gap = max(float(cfg.get("min_interval_seconds", 90)), float(terms.get("min_interval_seconds") or 0),
                          float(robots.crawl_delay or 0), float(getattr(fetcher, "min_interval", 0)))
                last = conn.execute("SELECT MAX(at) FROM reveal_log WHERE board=? AND pages>0", (board,)).fetchone()[0]
                last = max(datetime.fromisoformat(last).timestamp() + 1 if last else 0,  # +1: `at` is whole seconds
                           (getattr(fetcher, "_last", {}) or {}).get(host) or 0)
                wait = gap - (clock() - last) if last else 0
                if wait > 0:
                    sleep(wait)
                if opened is None:
                    try:
                        opened = open_browser(http.user_agent(settings))
                    except ImportError:
                        log("  ! Phone numbers: Playwright is not installed (python3 -m pip install playwright, "
                            "then python3 -m playwright install chromium)")
                        return found
                try:
                    numbers = reveal_one(opened[0], row["url"], cfg["reveal"][board]["button"])
                except Stop as e:
                    until = (datetime.now() + timedelta(days=float(cfg.get("blocked_days", 30)))).isoformat(timespec="seconds")
                    _log(conn, board, pages=1, stop_until=until, note=f"{board} {e}", lead_id=row["id"])
                    log(f"  ! {board} {e}. The desk does not get past those: no numbers revealed there "
                        f"for {cfg.get('blocked_days', 30)} days, and nothing kept from that page")
                    break
                except Cooling as e:
                    until = (datetime.now() + timedelta(seconds=e.seconds)).isoformat(timespec="seconds")
                    _log(conn, board, pages=1, stop_until=until, note=f"{board} {e}", lead_id=row["id"])
                    if hasattr(fetcher, "_cool"):  # the scanner holds off too
                        fetcher._cool(host, max(getattr(fetcher, "cooloff", 0), e.seconds), str(e))
                    log(f"  {board} phone reveal: {e}; trying again later")
                    break
                except Gone as e:
                    _log(conn, board, pages=1, note=f"{row['id']}: {e}", lead_id=row["id"])
                    _checked(conn, row["id"])
                    conn.commit()
                    continue
                except Skip as e:
                    _log(conn, board, pages=1, note=f"{row['id']}: {e}", lead_id=row["id"])
                    log(f"  ! {board} phone reveal on {row['id']}: {e}")
                    tries = conn.execute("SELECT COUNT(*) FROM reveal_log WHERE lead_id=? AND stop_until IS NULL "
                                         "AND numbers=0 AND note LIKE ?", (row["id"], row["id"] + ":%")).fetchone()[0]
                    if tries >= max_skips:  # give up on this ad
                        _checked(conn, row["id"])
                        conn.commit()
                    skips_in_a_row += 1
                    if skips_in_a_row >= 2:
                        log(f"  {board} phone reveal: two ads in a row failed, trying again on a later read")
                        break
                    continue
                finally:
                    if hasattr(fetcher, "_last"):
                        fetcher._last[host] = clock()  # the scanner's next request is spaced from this page too
                skips_in_a_row = 0
                _checked(conn, row["id"])
                new = save(conn, row["id"], numbers, "revealed")
                got += new
                _log(conn, board, pages=1, numbers=int(new), lead_id=row["id"])
            found += got
            log(f"  {board}: {got} phone number{'s' if got != 1 else ''} revealed")
    finally:
        if opened:
            opened[1]()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return found
