"""Polite fetching, the scan cache, alert emails, the capture button and the learning loop."""
import email
import email.policy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import alerts, capture, db, feed, learn, scan  # noqa: E402
from desk.checks import Checker  # noqa: E402
from desk.config import CONFIG_DIR, load_areas, load_settings  # noqa: E402
from desk.sources import appartogo, craigslist, details, http, kijiji, lespac  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
