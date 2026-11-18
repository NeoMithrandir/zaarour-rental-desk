"""Fetching public search pages politely.

Every request the desk makes goes through one PoliteFetcher, which:
- says who it is (an honest user agent with a contact address, not a disguised browser);
- reads each host's robots.txt (RFC 9309, with * and $ wildcards) and refuses any URL it disallows;
- spaces requests to one host (at least `min_interval` seconds, or the host's Crawl-delay if longer)
  with random jitter, so the desk never bursts;
- sends If-None-Match / If-Modified-Since when it has seen the page before, and reports an
  unchanged page as NotModified instead of downloading it again;
- backs off on 429/503 (honouring Retry-After), and after repeated refusals or any 403 it leaves
  that host alone for a cool-off period instead of retrying. It never tries to get around a block.

Curl is the fallback for Macs whose Python lacks root certificates.
"""
import email.utils
import gzip
import json
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

LANG = "fr-CA,fr;q=0.9,en;q=0.8"
LD_JSON_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
PRODUCT = "ZaarourRentalsDesk"
VERSION = "1.1"


class FetchError(Exception):
    """A page could not be read; the message says why in plain words."""


class NotModified(FetchError):
    """The page is the same as last time (HTTP 304): nothing new on it."""


class Disallowed(FetchError):
    """robots.txt asks robots not to read this URL, so the desk doesn't."""


class HostCooling(FetchError):
    """The host refused or rate-limited the desk recently; it is left alone until the cool-off ends."""


def user_agent(settings=None):
    agent = (settings or {}).get("agent", {})
    contact = "; ".join(p for p in [agent.get("rental_page_url"), agent.get("email")] if p)
    return f"{PRODUCT}/{VERSION} (+{contact})" if contact else f"{PRODUCT}/{VERSION}"


# ---------------------------------------------------------------- robots.txt
class Robots:
    """A parsed robots.txt. Groups are picked by user-agent product token; the longest matching rule wins,
    and Allow wins a tie (RFC 9309)."""

    def __init__(self, text, agent=PRODUCT):
        self.rules, self.crawl_delay, self.sitemaps = self._parse(text or "", agent.lower())

    @staticmethod
    def _parse(text, agent):
        groups, current, in_rules = [], None, False
        sitemaps = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "sitemap":
                sitemaps.append(val)
                continue
            if key == "user-agent":
                if current is None or in_rules:
                    current = {"agents": [], "rules": [], "delay": None}
                    groups.append(current)
                    in_rules = False
                current["agents"].append(val.lower())
            elif current is not None and key in ("allow", "disallow"):
                in_rules = True
                if val:
                    current["rules"].append((key == "allow", val))
            elif current is not None and key == "crawl-delay":
                in_rules = True
                try:
                    current["delay"] = float(val)
                except ValueError:
                    pass
        mine = [g for g in groups if any(a != "*" and a in agent for a in g["agents"])]
        chosen = mine or [g for g in groups if "*" in g["agents"]]
        rules = [r for g in chosen for r in g["rules"]]
        delays = [g["delay"] for g in chosen if g["delay"] is not None]
        return rules, (max(delays) if delays else None), sitemaps

    @staticmethod
    def _match(pattern, path):
        rx = "".join(".*" if c == "*" else "$" if c == "$" and i == len(pattern) - 1 else re.escape(c)
                     for i, c in enumerate(pattern))
        return re.match(rx, path) is not None

    def allowed(self, url):
        u = urllib.parse.urlsplit(url)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        best = None  # (length, allow)
        for allow, pattern in self.rules:
            if self._match(pattern, path):
                cand = (len(pattern), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]


# ---------------------------------------------------------------- transport
def _decode(body, encoding):
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        body = zlib.decompress(body)
    return body.decode("utf-8", "replace")


def urllib_open(url, headers, timeout):
    """One HTTP GET. Returns (status, headers dict with lower-case keys, text)."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            h = {k.lower(): v for k, v in r.headers.items()}
            return r.status, h, _decode(r.read(), h.get("content-encoding", ""))
    except urllib.error.HTTPError as e:
        h = {k.lower(): v for k, v in (e.headers or {}).items()}
        return e.code, h, ""
    except urllib.error.URLError as e:
        # python.org installs on macOS often lack root certificates; curl uses the system keychain.
        if "CERTIFICATE_VERIFY_FAILED" not in str(e) or not shutil.which("curl"):
            raise FetchError(f"network error: {e.reason}") from e
        return curl_open(url, headers, timeout)


def curl_open(url, headers, timeout):
    args = ["curl", "-sSL", "--compressed", "--max-time", str(timeout), "-D", "-", "-o", "-"]
    for k, v in headers.items():
        if k.lower() != "accept-encoding":
            args += ["-H", f"{k}: {v}"]
    out = subprocess.run(args + [url], capture_output=True)
    raw = out.stdout.decode("utf-8", "replace")
    # Headers of every hop come first; the last block before the body is the final response.
    status, h, pos = 0, {}, 0
    while raw.startswith("HTTP/", pos):
        end = raw.find("\r\n\r\n", pos)
        if end < 0:
            break
        lines = raw[pos:end].split("\r\n")
        status = int(lines[0].split()[1])
        h = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
        pos = end + 4
    if not status:
        raise FetchError(f"network error: {out.stderr.decode('utf-8', 'replace').strip() or 'no response'}")
    return status, h, raw[pos:]


def _retry_after(value, now):
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- the fetcher
class PoliteFetcher:
    ROBOTS_TTL = 24 * 3600

    def __init__(self, settings=None, state_path=None, opener=urllib_open, sleep=time.sleep, clock=time.time,
                 rand=random.random):
        cfg = (settings or {}).get("scan", {})
        self.ua = user_agent(settings)
        self.min_interval = float(cfg.get("min_interval_seconds", 6))
        self.jitter = float(cfg.get("jitter", 0.5))
        self.max_retries = int(cfg.get("max_retries", 2))
        self.backoff = float(cfg.get("backoff_seconds", 30))
        self.max_backoff = float(cfg.get("max_backoff_seconds", 300))
        self.cooloff = float(cfg.get("cooloff_minutes", 60)) * 60
        self.blocked_cooloff = float(cfg.get("blocked_cooloff_hours", 24)) * 3600
        self.timeout = int(cfg.get("timeout_seconds", 25))
        self.opener, self.sleep, self.clock, self.rand = opener, sleep, clock, rand
        self.state_path = state_path
        self.state = {"pages": {}, "robots": {}, "cooling": {}}
        if state_path and state_path.exists():
            try:
                self.state.update(json.loads(state_path.read_text(encoding="utf-8")))
            except ValueError:
                pass
        self._last = {}       # host -> time of last request
        self._robots = {}     # host -> Robots
        self.host_min_interval = {}  # host -> seconds, from that board's written permission
        self.requests = 0
        self.not_modified = 0

    def save(self):
        if self.state_path:
            self.state_path.parent.mkdir(exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    # -- pacing
    def _wait(self, host, delay):
        last = self._last.get(host)
        if last is not None:
            gap = delay * (1 + self.jitter * self.rand())
            left = last + gap - self.clock()
            if left > 0:
                self.sleep(left)
        self._last[host] = self.clock()

    def _headers(self, extra=None):
        h = {"User-Agent": self.ua, "Accept-Language": LANG, "Accept-Encoding": "gzip"}
        h.update(extra or {})
        return h

    def _check_cooling(self, host):
        until = self.state["cooling"].get(host)
        if until and until["until"] > self.clock():
            mins = round((until["until"] - self.clock()) / 60)
            raise HostCooling(f"{host} {until['why']}; leaving it alone for another {mins} min")

    def _cool(self, host, seconds, why):
        self.state["cooling"][host] = {"until": self.clock() + seconds, "why": why}

    # -- robots
    def robots(self, host, scheme="https"):
        if host in self._robots:
            return self._robots[host]
        cached = self.state["robots"].get(host)
        if cached and self.clock() - cached["at"] < self.ROBOTS_TTL:
            r = Robots(cached["text"]) if cached.get("ok", True) else Robots("User-agent: *\nDisallow: /")
        else:
            self._wait(host, self.min_interval)
            self.requests += 1
            try:
                status, _h, text = self.opener(f"{scheme}://{host}/robots.txt", self._headers(), self.timeout)
            except FetchError:
                status, text = 599, ""
            if 200 <= status < 300:
                ok = True
            elif 400 <= status < 500:  # RFC 9309: no robots.txt means no restrictions
                ok, text = True, ""
            else:                      # server error: assume everything is off limits for now
                ok = False
            self.state["robots"][host] = {"at": self.clock(), "text": text, "ok": ok}
            r = Robots(text) if ok else Robots("User-agent: *\nDisallow: /")
        self._robots[host] = r
        return r

    # -- the request
    def __call__(self, url, conditional=True, headers=None, issued_api=False):
        """GET one page. `headers` adds request headers a board needs (e.g. LesPAC's sort-order cookie).

        issued_api: the URL is an API endpoint the board issued the desk a key for (sources/api_feed.py). Its
        robots.txt speaks to crawlers of the public site, so it is not consulted; pacing and back-off still apply."""
        u = urllib.parse.urlsplit(url)
        host = u.netloc.lower()
        self._check_cooling(host)
        robots = Robots("") if issued_api else self.robots(host, u.scheme or "https")
        if not robots.allowed(url):
            raise Disallowed(f"robots.txt on {host} asks robots not to read {u.path}{'?' + u.query if u.query else ''}")
        delay = max(self.min_interval, robots.crawl_delay or 0, self.host_min_interval.get(host, 0))
        page = self.state["pages"].get(url, {})
        extra = dict(headers or {})
        if conditional and page.get("etag"):
            extra["If-None-Match"] = page["etag"]
        if conditional and page.get("last_modified"):
            extra["If-Modified-Since"] = page["last_modified"]
        for attempt in range(self.max_retries + 1):
            self._wait(host, delay)
            self.requests += 1
            status, h, text = self.opener(url, self._headers(extra), self.timeout)
            if status == 304:
                self.not_modified += 1
                page["checked_at"] = self.clock()
                self.state["pages"][url] = page
                raise NotModified(f"{u.path} unchanged since last check")
            if 200 <= status < 300:
                self.state["pages"][url] = {"etag": h.get("etag"), "last_modified": h.get("last-modified"),
                                            "checked_at": self.clock()}
                return text
            if status in (429, 503):
                wait = _retry_after(h.get("retry-after"), self.clock())
                if attempt < self.max_retries:
                    self.sleep(min(self.max_backoff, wait if wait is not None else self.backoff * 2 ** attempt))
                    continue
                self._cool(host, max(self.cooloff, wait or 0), f"asked the desk to slow down ({status})")
                raise HostCooling(f"{host} is rate-limiting the desk ({status}); trying again after a cool-off")
            if status == 403:
                self._cool(host, self.blocked_cooloff, "refused the desk (403)")
                raise HostCooling(f"{host} refused the desk (403). The desk does not try to get around blocks: "
                                  "use that board's email alerts or the capture button instead")
            raise FetchError(f"{host} answered {status}")
        raise FetchError(f"{host}: no answer")  # pragma: no cover


_default = None


def default_fetcher(settings=None):
    """The fetcher one scan shares across boards (so per-host pacing holds across them)."""
    global _default
    if _default is None or settings is not None:
        from ..config import DATA_DIR
        _default = PoliteFetcher(settings, DATA_DIR / "http_state.json")
    return _default


def fetch(url, timeout=None):
    return default_fetcher()(url)


# ---------------------------------------------------------------- page helpers
def ld_json(html):
    """Every JSON-LD block on a page (the structured data sites publish for search engines)."""
    out = []
    for m in LD_JSON_RE.finditer(html):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return out


def money(text):
    """'1 195$' / '$1,200' -> 1195 / 1200."""
    m = re.search(r"\d[\d\s,.  ]*", text or "")
    if not m:
        return None
    num = re.sub(r"[.,]\d{2}\s*$", "", m.group(0).strip())  # drop cents
    digits = re.sub(r"\D", "", num)
    return int(digits) if digits else None
