"""Polite fetching, the scan cache, alert emails, the capture button and the learning loop."""
import email
import email.policy
import json
import sys
import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import alerts, capture, db, feed, learn, phone_search, scan  # noqa: E402
from desk.checks import Checker  # noqa: E402
from desk.config import CONFIG_DIR, load_areas, load_settings  # noqa: E402
from desk.sources import api_feed, appartogo, craigslist, details, http, kijiji, lespac, phones  # noqa: E402
from tests.test_desk import FIXTURE, BoardTests, ad, page_html  # noqa: E402

KIJIJI_ROBOTS = """User-Agent: Mediapartners-Google
Disallow: /j-contact-seller.json*

User-agent: *
Disallow: /v-*/*/*/c*
Disallow: /t-login*
Disallow: /*?*address=*
Disallow: /*?*ll=*
Disallow: /*?*radius=*
Disallow: /*?*sort=*
"""


class FakeWeb:
    """Answers like a web server; records what was asked and how long the fetcher slept."""

    def __init__(self, pages, robots=KIJIJI_ROBOTS):
        self.pages, self.robots, self.asked, self.t = pages, robots, [], 1000.0
        self.slept = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    def open(self, url, headers, timeout):
        self.asked.append((url, dict(headers)))
        if url.endswith("/robots.txt"):
            return 200, {}, self.robots
        answer = self.pages.get(url, (404, {}, ""))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            return answer(headers)
        return answer


def fetcher_for(web, **cfg):
    settings = {"agent": {"email": "stefan@zaarour.ca", "rental_page_url": "https://zaarour.ca/location"},
                "scan": {"min_interval_seconds": 6, "jitter": 0.5, **cfg}}
    return http.PoliteFetcher(settings, None, opener=web.open, sleep=web.sleep, clock=web.clock, rand=lambda: 1.0)


class RobotsTests(unittest.TestCase):
    def test_kijiji_rules(self):
        r = http.Robots(KIJIJI_ROBOTS, "ZaarourRentalsDesk")
        old = "https://www.kijiji.ca/b-apartments-condos/ville-de-montreal/c37l1700281?sort=dateDesc&ll=45.5,-73.7&radius=6.0"
        self.assertFalse(r.allowed(old))  # what the desk used to request
        self.assertTrue(r.allowed(kijiji.search_url({"slug": "ville-de-montreal", "location_id": 1700281}, 2)))
        self.assertFalse(r.allowed("https://www.kijiji.ca/v-apartments-condos/city/title/c123"))

    def test_longest_match_allow_and_agent_group(self):
        r = http.Robots("User-agent: *\nDisallow: /a\nAllow: /a/b$\n\nUser-agent: zaarourrentalsdesk\nDisallow: /private\nCrawl-delay: 9",
                        "ZaarourRentalsDesk")
        self.assertTrue(r.allowed("https://x/a"))         # our own group applies, not *
        self.assertFalse(r.allowed("https://x/private/1"))
        self.assertEqual(r.crawl_delay, 9)
        r2 = http.Robots("User-agent: *\nDisallow: /a\nAllow: /a/b$")
        self.assertFalse(r2.allowed("https://x/a/c"))
        self.assertTrue(r2.allowed("https://x/a/b"))
        self.assertFalse(r2.allowed("https://x/a/b/c"))


class FetcherTests(unittest.TestCase):
    URL = "https://www.kijiji.ca/b-apartments-condos/ville-de-montreal/c37l1700281"

    def test_honest_agent_pacing_and_conditional(self):
        web = FakeWeb({self.URL: [(200, {"etag": '"v1"'}, "page one"), (304, {}, "")]})
        f = fetcher_for(web)
        self.assertEqual(f(self.URL), "page one")
        with self.assertRaises(http.NotModified):
            f(self.URL)
        ua = web.asked[-1][1]["User-Agent"]
        self.assertTrue(ua.startswith("ZaarourRentalsDesk/") and "stefan@zaarour.ca" in ua and "Mozilla" not in ua)
        self.assertEqual(web.asked[-1][1].get("If-None-Match"), '"v1"')
        self.assertTrue(all(s >= 6 for s in web.slept))  # never two requests to one host closer than the interval

    def test_refuses_what_robots_disallows(self):
        web = FakeWeb({})
        with self.assertRaises(http.Disallowed):
            fetcher_for(web)(self.URL + "?sort=dateDesc")
        self.assertEqual([u for u, _h in web.asked], ["https://www.kijiji.ca/robots.txt"])

    def test_backs_off_on_429_then_cools_off(self):
        web = FakeWeb({self.URL: [(429, {"retry-after": "40"}, ""), (200, {}, "ok")]})
        f = fetcher_for(web)
        self.assertEqual(f(self.URL), "ok")
        self.assertIn(40.0, web.slept)
        web2 = FakeWeb({self.URL: [(503, {}, "")]})
        f2 = fetcher_for(web2, max_retries=1)
        with self.assertRaises(http.HostCooling):
            f2(self.URL)
        n = len(web2.asked)
        with self.assertRaises(http.HostCooling):  # left alone: no new request
            f2(self.URL)
        self.assertEqual(len(web2.asked), n)

    def test_403_is_never_retried(self):
        web = FakeWeb({self.URL: [(403, {}, "")]})
        f = fetcher_for(web)
        with self.assertRaises(http.HostCooling) as e:
            f(self.URL)
        self.assertIn("does not try to get around", str(e.exception))
        self.assertEqual(sum(1 for u, _h in web.asked if u == self.URL), 1)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        self.settings["sources"] = ["kijiji"]
        self.settings["permissions"] = {"kijiji": {"scan_enabled": True, "letter": "letters/kijiji.pdf"}}
        self.settings["kijiji"] = {"max_pages_per_search": 5,
                                   "searches": [{"name": "Island", "slug": "ville-de-montreal", "location_id": 1700281}]}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def url(self, page):
        return kijiji.search_url(self.settings["kijiji"]["searches"][0], page)

    def test_stops_when_caught_up_and_reuses_fresh_reads(self):
        p1 = page_html([ad(i, f"4 1/2 Pierrefonds {i}", "Pierrefonds, QC H8Z 1V2", poster=f"p{i}") for i in range(1, 4)])
        p2 = page_html([ad(i, f"4 1/2 Pierrefonds {i}", "Pierrefonds, QC H8Z 1V2", poster=f"p{i}") for i in range(4, 7)])
        web = FakeWeb({self.url(1): (200, {}, p1), self.url(2): (200, {}, p2), self.url(3): (200, {}, p2)})
        c = scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual(c["new"], 6)
        pages = [u for u, _h in web.asked if "robots" not in u]
        self.assertEqual(pages, [self.url(1), self.url(2), self.url(3)])  # page 3 had nothing new: stop
        # Read moments ago: the next boot reuses it and asks nothing.
        web.asked.clear()
        c2 = scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual((c2["boards_read"], web.asked), (0, []))
        # Once stale, only page 1 is read: nothing on it is new.
        self.conn.execute("UPDATE scan_runs SET finished_at=?", ((datetime.now() - timedelta(hours=5)).isoformat(),))
        scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual([u for u, _h in web.asked if "robots" not in u], [self.url(1)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scan_runs WHERE ok=1").fetchone()[0], 2)

    def test_failed_board_is_logged_and_retried_next_time(self):
        web = FakeWeb({self.url(1): (403, {}, "")})
        logs = []
        scan.run(self.conn, self.settings, log=logs.append, fetcher=fetcher_for(web))
        self.assertTrue(any("refused" in l for l in logs))
        self.assertEqual(scan.due(self.conn, self.settings), ["kijiji"])  # a failed read isn't a fresh one


    def test_no_board_is_read_without_written_permission(self):
        web = FakeWeb({})
        logs = []
        defaults = json.loads((CONFIG_DIR / "settings.example.json").read_text())  # as shipped: every scanner off
        self.assertEqual(scan.due(self.conn, defaults), ["alerts"])
        for p in ({}, {"scan_enabled": True}, {"scan_enabled": False, "letter": "x.pdf"},
                  {"scan_enabled": True, "letter": "x.pdf", "expires": "2020-01-01"}):
            self.settings["permissions"] = {"kijiji": p}
            c = scan.run(self.conn, self.settings, log=logs.append, fetcher=fetcher_for(web))
            self.assertEqual((c["boards_read"], web.asked), (0, []))
        self.assertTrue(any("written permission" in l for l in logs))
        self.assertIn("expired", scan.board_status(self.conn, self.settings)[0]["waiting"])

    def test_limits_from_the_permission_are_kept(self):
        pages = {self.url(i): (200, {}, page_html([ad(i * 10 + j, f"4 1/2 Lachine {i}{j}", "Lachine, QC H8S 1A1",
                                                      poster=f"p{i}{j}") for j in range(3)])) for i in range(1, 6)}
        web = FakeWeb(pages)
        self.settings["permissions"]["kijiji"].update(max_requests_per_hour=3, min_interval_seconds=20)
        scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual(len([u for u, _h in web.asked if "robots" not in u]), 3)  # capped at 3 pages
        self.assertTrue(all(s >= 20 for s in web.slept))  # the letter's spacing, not the default 6 s
        # The hour's allowance is used up: forcing a read asks nothing more.
        web.asked.clear()
        scan.run(self.conn, self.settings, force=True, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual(web.asked, [])

    def test_lespac_and_appartogo_read_newest_first_at_the_letters_pace(self):
        self.settings["sources"] = ["lespac", "appartogo"]
        self.settings["appartogo"] = {"categories": ["appartement-louer"], "regions": ["ile-de-montreal"], "pages_per_region": 3}
        limits = {"scan_enabled": True, "letter": "letters/x.pdf", "min_interval_seconds": 30}
        self.settings["permissions"] = {"lespac": dict(limits), "appartogo": dict(limits)}
        a1, a2 = (appartogo.page_url("appartement-louer", "ile-de-montreal", n) for n in (1, 2))
        web = FakeWeb({lespac.URL: (200, {}, BoardTests.LESPAC), a1: (200, {}, BoardTests.APPARTOGO),
                       a2: (200, {}, BoardTests.APPARTOGO)}, robots="User-agent: *\nDisallow: /recherche\n")
        c = scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual(c["boards_read"], 2)
        asked = [(u, h) for u, h in web.asked if "robots" not in u]
        self.assertEqual([u for u, _h in asked], [lespac.URL, a1, a2])  # page 2 had nothing new: stop
        self.assertEqual(asked[0][1].get("Cookie"), "searchSortOrder=Date+desc")
        self.assertTrue(all(s >= 30 for s in web.slept))
        ids = {r[0] for r in self.conn.execute("SELECT id FROM leads")}
        self.assertTrue({"lespac:228047031", "appartogo:3385138", "appartogo:7"} <= ids)


CL_SEARCH = """<script type="application/ld+json">{"@type":"ItemList","itemListElement":[
{"item":{"@type":"Apartment","latitude":45.45,"longitude":-73.69,"numberOfBedrooms":2}},
{"item":{"@type":"Apartment","latitude":45.45,"longitude":-73.69,"numberOfBedrooms":3}}]}</script>
<li class="cl-static-search-result" title="Beau 4 1/2 Lachine"><a href="https://www.craigslist.org/view/d/lachine/AAA">
<div class="price">$1,650</div><div class="location">Lachine</div></a></li>
<li class="cl-static-search-result" title="5 1/2 Lachine"><a href="https://www.craigslist.org/view/d/lachine/BBB">
<div class="price">$1,900</div><div class="location">Lachine</div></a></li>"""


def cl_ad(text):
    return (f'<section id="postingbody"><div class="print-information print-qrcode-container"><p>QR</p>'
            f'<div class="print-qrcode"></div></div>{text}</section>'
            '<div class="attrgroup"><div class="attr">2BR / 1Ba</div><div class="attr">laundry in bldg</div></div>'
            '<p class="postinginfo reveal">posted: <time class="date timeago" datetime="2026-11-13T09:00:00-0500">x</time></p>')


class DetailTests(unittest.TestCase):
    URL = "https://montreal.craigslist.org/search/apa?purveyor=owner&sort=date"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        self.settings["sources"] = ["craigslist"]
        self.settings["permissions"] = {"craigslist": {"scan_enabled": True, "letter": "x"}}
        self.settings["details"] = {"boards": ["craigslist"], "max_per_run": 60}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_redact_blanks_phones_and_emails_but_not_rents(self):
        out = details.redact("Call 514Two66One355, (514) 266-1355 or bob@x.ca. 1 650 $ for a 4 1/2, 2 rooms.")
        self.assertNotIn("266", out)
        self.assertNotIn("bob@", out)
        self.assertIn("1 650 $ for a 4 1/2, 2 rooms", out)

    def test_craigslist_ad_page(self):
        d = craigslist.parse_ad(cl_ad("Bright 4 1/2<br>Near the train"))
        self.assertEqual(d["text"].splitlines()[:2], ["Bright 4 1/2", "Near the train"])
        self.assertIn("laundry in bldg", d["text"])
        self.assertNotIn("QR", d["text"])
        self.assertEqual(d["posted_at"], "2026-11-13T09:00:00-0500")

    def test_new_ads_are_opened_once_and_their_text_kept(self):
        web = FakeWeb({self.URL: (200, {}, CL_SEARCH),
                       "https://www.craigslist.org/view/d/lachine/AAA": (200, {}, cl_ad("Owner here. Text 514 555 0101")),
                       "https://www.craigslist.org/view/d/lachine/BBB": (200, {}, cl_ad("Courtier immobilier, RE/MAX"))},
                      robots="")
        scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        rows = {r["source_id"]: r for r in self.conn.execute("SELECT * FROM leads")}
        self.assertIn("Owner here", rows["AAA"]["snippet"])
        self.assertNotIn("555", rows["AAA"]["snippet"])  # the number is never stored
        self.assertEqual(rows["BBB"]["bucket"], "broker")  # the full text caught the broker
        # Next read: the search page again, no ad opened again, and the full text survives.
        web.asked.clear()
        scan.run(self.conn, self.settings, force=True, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual([u for u, _h in web.asked if "robots" not in u], [self.URL])
        snippet = self.conn.execute("SELECT snippet FROM leads WHERE source_id='AAA'").fetchone()[0]
        self.assertIn("Owner here", snippet)
        bucket = self.conn.execute("SELECT bucket FROM leads WHERE source_id='BBB'").fetchone()[0]
        self.assertEqual(bucket, "broker")  # still checked against the full text

    def test_cap_per_read(self):
        self.settings["details"]["max_per_run"] = 1
        web = FakeWeb({self.URL: (200, {}, CL_SEARCH),
                       "https://www.craigslist.org/view/d/lachine/AAA": (200, {}, cl_ad("one")),
                       "https://www.craigslist.org/view/d/lachine/BBB": (200, {}, cl_ad("two"))}, robots="")
        scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual(len([u for u, _h in web.asked if "/view/" in u]), 1)
        scan.run(self.conn, self.settings, force=True, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leads WHERE detailed=1").fetchone()[0], 2)


ALERT = """From: Kijiji <alerts@kijiji.ca>
To: stefan@zaarour.ca
Subject: 3 new ads match your search
Date: Thu, 15 Oct 2026 07:02:00 -0400
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<table><tr><td><a href="https://click.kijiji.ca/t?url=https%3A%2F%2Fwww.kijiji.ca%2Fv-appartement-condo%2Fville-de-montreal%2Fbeau-4-1-2-pierrefonds%2F1712345678">Beau 4 1/2 Pierrefonds</a>
<p>1 650 $ · Pierrefonds-Roxboro</p></td></tr>
<tr><td><a href="https://www.kijiji.ca/v-appartement-condo/laval/5-1-2-chomedey/1712345679">5 1/2 Chomedey, libre maintenant</a> <span>$1,900</span></td></tr>
<tr><td><a href="https://www.kijiji.ca/m-my-ads">My ads</a> <a href="https://www.kijiji.ca/unsubscribe?x=1">Unsubscribe</a></td></tr></table>
"""


class AlertAndCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_alert_email(self):
        msg = email.message_from_string(ALERT, policy=email.policy.default)
        leads = {l["id"]: l for l in alerts.parse_message(msg, "2026-10-15T07:02:00")}
        self.assertEqual(set(leads), {"kijiji:1712345678", "kijiji:1712345679"})  # tracker unwrapped; nav links skipped
        self.assertEqual(leads["kijiji:1712345678"]["title"], "Beau 4 1/2 Pierrefonds")
        self.assertEqual(leads["kijiji:1712345678"]["price"], 1650)
        self.assertEqual(leads["kijiji:1712345679"]["price"], 1900)
        folder = Path(self.tmp.name) / "inbox"
        folder.mkdir()
        (folder / "a.eml").write_text(ALERT, encoding="utf-8")
        self.assertEqual(len(alerts.collect({"alerts": {"folder": str(folder)}}, log=lambda *_: None)), 2)

    def test_capture_matches_scanned_ad_and_keeps_it(self):
        settings = load_settings()
        feed.process(self.conn, kijiji.parse(FIXTURE, "test"), Checker(load_areas(), settings))
        form = capture.from_capture({"url": "https://www.kijiji.ca/v-apartments-condos/x/1?utm=1", "title": "Beau 4 1/2",
                                     "text": "", "ld": "[]"})
        self.assertEqual(form["source"], "kijiji")
        lid = feed.add_manual(self.conn, form)
        self.assertEqual(lid, "kijiji:1")  # the same ad, not a second card
        row = self.conn.execute("SELECT lat, search FROM leads WHERE id='kijiji:1'").fetchone()
        self.assertEqual((row["lat"], row["search"]), (45.5, "test"))  # the scanned details are kept

    def test_capture_from_facebook_without_address_is_reviewed_not_hidden(self):
        ld = json.dumps([{"@type": "Offer", "price": "1850.00"}])
        form = capture.from_capture({"url": "https://www.facebook.com/marketplace/item/123456789/?ref=x",
                                     "title": "4½ à louer", "text": "Grand 4½ lumineux", "ld": ld})
        self.assertEqual((form["source"], form["price"]), ("facebook", "1850"))
        lid = feed.add_manual(self.conn, form)
        r = self.conn.execute("SELECT bucket, flags FROM leads WHERE id=?", (lid,)).fetchone()
        self.assertEqual((lid, r["bucket"]), ("facebook:123456789", "review"))
        self.assertIn("area_unknown", r["flags"])

    def test_bookmarklet_is_a_javascript_link(self):
        link = capture.bookmarklet()
        self.assertTrue(link.startswith("javascript:"))
        self.assertIn("127.0.0.1:8765/capture", link)


class LearningTests(unittest.TestCase):
    def setUp(self):
        from desk import web
        self.web = web
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        ads = [ad(i, ("Beau 4 1/2 rénové" if i % 2 else "Condo moderne") + f" {i}", "Pierrefonds, QC H8Z 1V2", poster=f"p{i}")
               for i in range(1, 41)]
        feed.process(self.conn, kijiji.parse(page_html(ads), "test"), Checker(load_areas(), self.settings))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_decisions_are_logged_with_template_and_signals(self):
        lead = db.lead_dict(self.conn.execute("SELECT * FROM leads WHERE id='kijiji:1'").fetchone())
        from desk import outreach
        msg = outreach.render(lead, self.settings, "fr")
        self.web.api_decide(self.conn, "kijiji:1", {"action": "approve", "message": msg}, self.settings)
        self.web.api_decide(self.conn, "kijiji:3", {"action": "approve", "message": msg + " Merci!"}, self.settings)
        self.web.api_decide(self.conn, "kijiji:2", {"action": "reject", "reason": "Bad property"}, self.settings)
        ev = self.conn.execute("SELECT * FROM events ORDER BY id").fetchall()
        self.assertEqual([e["action"] for e in ev], ["approve", "approve", "reject"])
        self.assertTrue(ev[0]["template"].startswith("outreach_fr@"))
        self.assertEqual((ev[0]["edited"], ev[1]["edited"]), (0, 1))
        self.assertEqual(ev[2]["reason"], "Bad property")
        self.assertEqual(json.loads(ev[0]["signals"])["poster_type"], "owner")

    def test_learnings_are_written_and_small_samples_marked(self):
        for i in range(1, 41):
            lid = f"kijiji:{i}"
            if i % 2:  # 'rénové' titles approved and sent
                self.web.api_decide(self.conn, lid, {"action": "approve", "message": "x"}, self.settings)
                self.web.api_decide(self.conn, lid, {"action": "sent"}, self.settings)
                self.web.api_decide(self.conn, lid, {"action": "replied" if i % 4 == 1 else "no_reply"}, self.settings)
            else:
                self.web.api_decide(self.conn, lid, {"action": "reject", "reason": "Bad property"}, self.settings)
        out = Path(self.tmp.name) / "learnings"
        learn.write(self.conn, self.settings, out)
        summary = (out / "summary.md").read_text(encoding="utf-8")
        self.assertIn("40 decisions", summary)
        self.assertIn("Never send a message to a landlord automatically", summary)
        self.assertIn("Bad property", summary)
        self.assertTrue((out / "platforms" / "kijiji.md").exists())
        hooks = (out / "hooks.md").read_text(encoding="utf-8")
        self.assertIn("renove", hooks)
        self.assertIn("tentative", hooks)  # 20 ads per word is under min_sample
        f = json.loads((out / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(f["platforms"]["kijiji"]["approved"], 20)


class PhoneTests(unittest.TestCase):
    """Landlords' numbers: kept only where the board's permission covers contact details."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        self.settings["sources"] = ["kijiji"]
        self.settings["permissions"] = {"kijiji": {"scan_enabled": True, "letter": "Nesi's statement, 17 Nov 2026",
                                                   "letter_pending": True, "allow_contact_capture": True,
                                                   "contact_basis": "letter, point 4"}}
        self.settings["kijiji"] = {"max_pages_per_search": 1,
                                   "searches": [{"name": "Island", "slug": "ville-de-montreal", "location_id": 1700281}]}
        # No reveal on the board reads below: the tests that click call phones.reveal with a fake browser.
        self.settings["contacts"] = {"reveal": {}, "max_reveals_per_run": 5, "min_interval_seconds": 90,
                                     "blocked_days": 30}
        url = kijiji.search_url(self.settings["kijiji"]["searches"][0])
        self.web = FakeWeb({url: (200, {}, page_html([
            ad(1, "4 1/2 Lachine, call 514-555-0101", "Lachine, QC H8S 1A1", poster="a",
               desc="Or text (438) 555 0199. 1 650 $ for a 4 1/2."),
            ad(2, "5 1/2 Lachine", "Lachine, QC H8S 1A1", poster="b"),
            ad(3, "3 1/2 Lachine", "Lachine, QC H8S 1A1", poster="c")]))})
        self.slept = []
        self.fetcher = self.real_clock_fetcher(self.web)

    def real_clock_fetcher(self, web):
        """The reveal paces itself on the wall clock (its log is in the database), so its tests use one too."""
        settings = {"agent": {"email": "stefan@zaarour.ca", "rental_page_url": "https://zaarour.ca/location"},
                    "scan": {"min_interval_seconds": 6, "jitter": 0}}
        return http.PoliteFetcher(settings, None, opener=web.open, sleep=lambda s: None, clock=time.time)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def phone(self, lead_id):
        r = self.conn.execute("SELECT phone, phone_via FROM leads WHERE id=?", (lead_id,)).fetchone()
        return (r["phone"], r["phone_via"])

    def read_board(self, **kw):
        return scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=self.fetcher, **kw)

    def reveal(self, results, fetcher=None, reset=True, **kw):
        """phones.reveal with a fake browser that answers `results` in turn (a list, or an exception to raise)."""
        self.settings["contacts"]["reveal"] = {"kijiji": {"button": "Reveal phone number"}}
        if reset:
            self.conn.execute("UPDATE leads SET bucket='review', phone=NULL, phone_via=NULL, phone_checked_at=NULL")
        calls, logs = [], []

        def open_browser(ua):
            calls.append(("open", ua))
            return "context", lambda: calls.append(("closed",))

        def reveal_one(context, url, button):
            calls.append(("page", url))
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        n = phones.reveal(self.conn, self.settings, fetcher or self.fetcher, logs.append,
                          open_browser=open_browser, reveal_one=reveal_one, sleep=self.slept.append,
                          lock_path=Path(self.tmp.name) / "phones.lock", **kw)
        return n, calls, logs

    def test_numbers_in_text(self):
        self.assertEqual(phones.in_text("Call 514Two66One355 or 1 (514) 266-1355, 1 650 $ for a 4 1/2"),
                         ["514-266-1355"])
        self.assertEqual(phones.in_text("Grand 5 1/2 514.555.1234"), ["514-555-1234"])
        self.assertEqual(phones.in_text("Disponible juillet 2026 514 555 1234"), ["514-555-1234"])
        self.assertEqual(phones.in_text("Loyer 1450 514-555-1234"), ["514-555-1234"])
        self.assertEqual(phones.in_text("514-555-1234 438-555-9876"), ["514-555-1234", "438-555-9876"])
        self.assertEqual(phones.in_text("Lot 1234567890, 2 chambres, 1 650 $"), [])

    def test_numbers_are_blanked_from_stored_text_and_kept_only_with_contact_permission(self):
        self.read_board()
        self.assertEqual(self.phone("kijiji:1"), ("514-555-0101, 438-555-0199", "ad text"))
        self.assertEqual(self.phone("kijiji:2"), (None, None))
        stored = self.conn.execute("SELECT title, snippet FROM leads WHERE id='kijiji:1'").fetchone()
        self.assertNotIn("555", stored["title"] + stored["snippet"])
        self.assertIn("1 650 $", stored["snippet"])
        self.assertTrue(scan.board_status(self.conn, self.settings)[0]["letter_pending"])
        for off in ({"allow_contact_capture": False}, {"contact_basis": ""}, {"scan_enabled": False},
                    {"expires": "2020-01-01"}):
            self.conn.execute("UPDATE leads SET phone=NULL, phone_via=NULL")
            self.settings["permissions"]["kijiji"].update({"scan_enabled": True, "expires": "",
                                                           "allow_contact_capture": True, "contact_basis": "x", **off})
            self.assertFalse(phones.allowed(self.settings, "kijiji"))
            self.read_board(force=True)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL").fetchone()[0], 0)

    def test_reveal_spaces_its_pages_and_stops_at_a_bot_check(self):
        self.read_board()
        n, calls, logs = self.reveal([["514-555-0303"], phones.Stop("runs a bot check behind its show-number button")])
        self.assertEqual(n, 1)
        self.assertIn("ZaarourRentalsDesk", calls[0][1])  # the honest user agent
        self.assertEqual(calls[-1], ("closed",))
        self.assertEqual(len([c for c in calls if c[0] == "page"]), 2)  # nothing opened after the stop
        # The first page waited out the gap after the scanner's last request, the second the gap after the first.
        self.assertEqual(len(self.slept), 2)
        self.assertTrue(all(85 < s <= 91 for s in self.slept), self.slept)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leads WHERE phone_via='revealed'").fetchone()[0], 1)
        self.assertTrue(any("does not get past" in l for l in logs))
        self.assertTrue(phones.stopped(self.conn, "kijiji"))
        # The stop holds on the next run (it is in the database), and reading the board's ads still works.
        n, calls, logs = self.reveal([["514-555-0404"]])
        self.assertEqual((n, calls), (0, []))
        self.assertEqual(self.read_board(force=True, reveal=False)["boards_read"], 1)

    def test_reveal_needs_the_board_permission_and_obeys_robots_and_limits(self):
        self.read_board()
        for off in ({"allow_contact_capture": False}, {"scan_enabled": False}, {"expires": "2020-01-01"}):
            self.settings["permissions"]["kijiji"].update({"scan_enabled": True, "expires": "",
                                                           "allow_contact_capture": True, **off})
            self.assertEqual(self.reveal([["514-555-0303"]] * 3)[:2], (0, []))
        self.settings["permissions"]["kijiji"].update(scan_enabled=True, expires="", allow_contact_capture=True)
        # Only after a read of that board, never after an alerts-only read.
        n, calls, _ = self.reveal([["514-555-0303"]] * 3, only=["alerts"])
        self.assertEqual((n, calls), (0, []))
        n, calls, logs = self.reveal([["514-555-0303"]] * 3,
                                     fetcher=self.real_clock_fetcher(FakeWeb({}, robots="User-agent: *\nDisallow: /v-")))
        self.assertEqual(n, 0)
        self.assertNotIn("page", [c[0] for c in calls])
        # The permission's hourly limit counts the scanner's pages and the reveal's together.
        self.settings["permissions"]["kijiji"]["max_requests_per_hour"] = scan.requests_last_hour(self.conn, "kijiji") + 1
        n, calls, _ = self.reveal([["514-555-0303"], ["438-555-0404"]])
        self.assertEqual(len([c for c in calls if c[0] == "page"]), 1)
        self.assertEqual(self.reveal([["438-555-0404"]])[:2], (0, []))

    def test_rate_limit_cools_the_board_and_a_failed_page_is_tried_again_a_few_times(self):
        self.read_board()
        n, calls, logs = self.reveal([phones.Skip("answered 500"), phones.Cooling("asked the desk to slow down (429)", 600)])
        self.assertEqual(n, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leads WHERE phone_checked_at IS NOT NULL").fetchone()[0], 0)
        self.assertIn("slow down", phones.stopped(self.conn, "kijiji"))
        with self.assertRaises(http.HostCooling):  # the scanner holds off too
            self.fetcher._check_cooling("www.kijiji.ca")
        # A removed ad is never opened again; one that keeps failing is given up after three tries.
        self.conn.execute("DELETE FROM reveal_log")
        self.fetcher.state["cooling"].clear()
        self.reveal([phones.Gone("ad removed (404)"), phones.Skip("timeout"), ["514-555-0303"]])
        unchecked = lambda: [r[0] for r in self.conn.execute("SELECT id FROM leads WHERE phone_checked_at IS NULL")]  # noqa: E731
        self.assertEqual(len(unchecked()), 1)  # the removed ad and the revealed one are done
        self.reveal([phones.Skip("timeout")], reset=False)
        self.assertEqual(len(unchecked()), 1)
        self.reveal([phones.Skip("timeout")], reset=False)  # third failure: given up
        self.assertEqual(unchecked(), [])
        n, calls, _ = self.reveal([], reset=False)
        self.assertEqual((n, calls), (0, []))

    def test_one_reveal_at_a_time(self):
        import fcntl
        self.read_board()
        held = open(Path(self.tmp.name) / "phones.lock", "w")
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            n, calls, logs = self.reveal([["514-555-0303"]])
        finally:
            held.close()
        self.assertEqual((n, calls), (0, []))
        self.assertTrue(any("another reveal" in l for l in logs))

    def test_blanking_keeps_the_checks_and_leaves_hand_captures_alone(self):
        url = kijiji.search_url(self.settings["kijiji"]["searches"][0])
        self.web.pages[url] = (200, {}, page_html([
            ad(7, "4 1/2 Lachine", "Lachine, QC H8S 1A1", poster="g",
               desc="Beau grand logement rénové. Je cherche un locataire sérieux. Appelez 514-555-0177."),
            ad(8, "5 1/2 Lachine", "Lachine, QC H8S 1A1", poster="h", desc="Info: 438 555 0188")]))
        self.settings["permissions"]["kijiji"]["allow_contact_capture"] = False
        checker = feed.checker_for(self.conn, self.settings)
        checker.phones.add("4385550188")  # a known broker's number
        with unittest.mock.patch.object(feed, "checker_for", lambda conn, settings=None: checker):
            self.read_board()
            # The landlord's "je cherche un locataire" is not read across into the title as a tenant's ad.
            self.assertEqual(self.conn.execute("SELECT bucket FROM leads WHERE id='kijiji:7'").fetchone()[0], "review")
            self.assertEqual(self.conn.execute("SELECT bucket FROM leads WHERE id='kijiji:8'").fetchone()[0], "broker")
            # Rechecked from the blanked text, the broker match found in the original still stands.
            feed.recheck_all(self.conn)
            self.assertEqual(self.conn.execute("SELECT bucket FROM leads WHERE id='kijiji:8'").fetchone()[0], "broker")
            # Stefan's own capture keeps what he pasted.
            feed.add_manual(self.conn, {"url": "https://www.kijiji.ca/v-apartments-condos/x/99", "title": "4 1/2",
                                        "text": "Owner, call 438 555 0199", "address": "Lachine, QC H8S 1A1"})
        self.assertIn("438 555 0199", self.conn.execute("SELECT snippet FROM leads WHERE url LIKE '%/99'").fetchone()[0])

    def test_revealed_number_is_not_replaced_from_text(self):
        self.read_board()
        phones.save(self.conn, "kijiji:2", ["514-555-0303"], "revealed")
        self.assertFalse(phones.save(self.conn, "kijiji:2", ["438-555-0000"], "ad text"))
        self.assertEqual(self.phone("kijiji:2"), ("514-555-0303", "revealed"))

class PhoneSearchTests(unittest.TestCase):
    """A captured number found online with a broker adds a broker flag."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        self.settings["phone_search"] = {"provider": "brave", "brave_api_key": "k", "max_per_run": 5,
                                         "min_interval_seconds": 3, "recheck_days": 60}
        lead = {"id": "kijiji:1", "source": "kijiji", "source_id": "1", "url": "u", "title": "4 1/2 Lachine",
                "snippet": "", "address": "Lachine, QC H8S 1A1", "poster_type": "owner"}
        feed.process(self.conn, [lead, dict(lead, id="kijiji:2", source_id="2")], feed.checker_for(self.conn, self.settings))
        self.conn.execute("UPDATE leads SET phone='514-555-0101' WHERE id='kijiji:1'")
        self.conn.execute("UPDATE leads SET phone='438-555-0199' WHERE id='kijiji:2'")
        self.asked, self.slept = [], []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def get(self, url, headers):
        self.asked.append((url, headers))
        if "514" in url:
            return {"web": {"results": [{"url": "https://www.remax-quebec.com/fr/courtiers/jean", "title": "Jean, courtier",
                                         "description": "Jean Tremblay 514-555-0101"}]}}
        return {"web": {"results": [{"url": "https://www.kijiji.ca/v-x/1", "title": "4 1/2 Lachine",
                                     "description": "Owner, call 438-555-0199"}]}}

    def bucket(self, lead_id):
        return self.conn.execute("SELECT bucket FROM leads WHERE id=?", (lead_id,)).fetchone()[0]

    def test_off_without_a_key(self):
        self.settings["phone_search"]["brave_api_key"] = ""
        self.assertEqual(phone_search.run(self.conn, self.settings, lambda *_: None, self.get, self.slept.append), 0)
        self.assertEqual(self.asked, [])

    def test_broker_number_flags_the_lead_and_each_number_is_searched_once(self):
        n = phone_search.run(self.conn, self.settings, lambda *_: None, self.get, self.slept.append)
        self.assertEqual(n, 1)
        self.assertEqual((self.bucket("kijiji:1"), self.bucket("kijiji:2")), ("broker", "review"))
        flags = json.loads(self.conn.execute("SELECT flags FROM leads WHERE id='kijiji:1'").fetchone()[0])
        self.assertIn("found online with a broker", flags[-1]["text"] if flags[-1]["code"] == "broker" else
                      next(f["text"] for f in flags if f["code"] == "broker"))
        self.assertEqual(self.slept, [3.0])
        self.assertEqual(self.asked[0][1]["X-Subscription-Token"], "k")
        self.asked.clear()
        phone_search.run(self.conn, self.settings, lambda *_: None, self.get, self.slept.append)
        self.assertEqual(self.asked, [])

    def test_owner_ad_mentioning_the_number_is_not_a_broker(self):
        self.assertIsNone(phone_search.classify("438-555-0199", [{"url": "https://www.kijiji.ca/v/1",
                                                                  "title": "Logement", "snippet": "438-555-0199"}]))
        self.assertTrue(phone_search.classify("438-555-0199", [{"url": "https://site.ca/x", "title": "Équipe Dubé",
                                                                "snippet": "Courtier immobilier, 438 555 0199"}]))
        self.assertIsNone(phone_search.classify("438-555-0199", [{"url": "https://site.ca/x", "title": "Courtier",
                                                                  "snippet": "Other number 514 555 0000"}]))


class ApiBoardTests(unittest.TestCase):
    """Rentals.ca, PadMapper, Louer.ca and Facebook: read only through access the board issues."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        self.settings["sources"] = ["rentalsca"]
        self.settings["permissions"] = {"rentalsca": {"scan_enabled": True, "letter": "letters/rentalsca.pdf"}}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_idle_until_access_is_issued(self):
        self.assertIn("API access", scan.permission(self.settings, "rentalsca"))
        web = FakeWeb({})
        self.assertEqual(scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))["boards_read"], 0)
        self.assertEqual(web.asked, [])

    def test_reads_the_issued_feed_with_its_key_and_field_map(self):
        endpoint = "https://api.rentals.ca/v1/listings?city=montreal"
        self.settings["rentalsca"] = {"api": {
            "endpoint": endpoint, "key": "secret", "key_header": "X-Api-Key", "key_prefix": "", "items": "data.listings",
            "fields": {"source_id": "id", "url": "url", "title": "title", "price": "rent.amount",
                       "address": "location.address", "lat": "location.lat", "lng": "location.lng", "image": "photos.0.url"},
            "page_param": "", "max_pages": 1}}
        body = json.dumps({"data": {"listings": [
            {"id": 42, "url": "https://rentals.ca/montreal/x", "title": "4 1/2 Lachine", "rent": {"amount": "1650"},
             "location": {"address": "Lachine, QC H8S 1A1", "lat": 45.43, "lng": -73.67},
             "photos": [{"url": "https://img/1.jpg"}]}]}})
        # The public site's robots.txt disallows everything; the issued API endpoint is still read.
        web = FakeWeb({endpoint: (200, {}, body)}, robots="User-agent: *\nDisallow: /")
        self.assertIsNone(scan.permission(self.settings, "rentalsca"))
        c = scan.run(self.conn, self.settings, log=lambda *_: None, fetcher=fetcher_for(web))
        self.assertEqual(c["new"], 1)
        (url, headers), = [a for a in web.asked if "robots" not in a[0]]
        self.assertEqual(headers["X-Api-Key"], "secret")
        self.assertIn("ZaarourRentalsDesk", headers["User-Agent"])
        row = self.conn.execute("SELECT * FROM leads WHERE id='rentalsca:42'").fetchone()
        self.assertEqual((row["price"], row["image"], row["source"]), (1650, "https://img/1.jpg", "rentalsca"))


if __name__ == "__main__":
    unittest.main()
