"""Run with: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import db, feed, outreach, rentals  # noqa: E402
from desk.checks import Checker, bucket  # noqa: E402
from desk.config import load_areas, load_settings  # noqa: E402
from desk.sources import craigslist, kijiji, logisquebec  # noqa: E402
from desk.sources.http import money  # noqa: E402
from desk.text import address_key, city_of, postal_fsa  # noqa: E402


def ad(i, title, address, by="ownr", desc="", poster="p1", cents=150000):
    return {
        "__typename": "RealEstateListing", "id": str(i), "title": title, "description": desc,
        "imageUrls": [], "url": f"https://www.kijiji.ca/v-apartments-condos/x/{i}",
        "activationDate": "2026-10-14T12:00:00.000Z", "sortingDate": "2026-10-14T12:00:00.000Z",
        "adSource": "ORGANIC", "location": {"address": address, "coordinates": {"latitude": 45.5, "longitude": -73.8}},
        "price": {"amount": cents}, "posterInfo": {"posterId": poster},
        "attributes": {"all": [{"canonicalName": "forrentbyhousing", "canonicalValues": [by]},
                               {"canonicalName": "numberbedrooms", "canonicalValues": ["2"]}]},
    }


def page_html(ads):
    state = {f"RealEstateListing:{a['id']}": a for a in ads}
    data = {"props": {"pageProps": {"__APOLLO_STATE__": state}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


FIXTURE = page_html([
    ad(1, "Beau 4 1/2 rénové, eau chaude incluse", "Pierrefonds, QC H8Z 1V2"),
    ad(2, "Cession de bail 3 1/2", "Laval, QC H7N 3Z7"),
    ad(3, "Condo à louer", "1745 Rue de Plaisance, Terrebonne, QC J6X 0K7"),
    ad(4, "Spacious 5 1/2 for rent, all appliances included", "445 Av. Lepage, Dorval, QC H9S 3G1",
       desc="Listed by RE/MAX broker, call for showing"),
    ad(5, "Apartments for rent near metro", "1225 Alexis-Nihon, Saint-Laurent, QC H4R 2A6", by="reprofessional", poster="pm"),
    ad(6, "Bachelor 1 1/2", "2109 Rue Bonin, Montréal, QC H4M 1A1", cents=80000),
    ad(7, "4½ lumineux à louer", "Boulevard Saint-Laurent, Montréal, QC H2X 2T3"),
    ad(8, "Maison à louer", "12 Rue Principale, Gatineau, QC J8Y 1A1"),
])


class TextTests(unittest.TestCase):
    def test_address_key_matches_variants(self):
        a = address_key("4005, boul. des Sources O., Dollard-des-Ormeaux, QC H9B 2A6")
        self.assertEqual(a, address_key("4005 Sources Blvd West, DDO"))
        self.assertEqual(a, address_key("12-4005 boul des Sources ouest"))
        self.assertEqual(address_key("Pierrefonds, QC H8Z 1V2"), "")

    def test_postal_and_city(self):
        self.assertEqual(postal_fsa("Laval, QC H7N 3Z7"), "H7N")
        self.assertEqual(city_of("123 Rue X, Pierrefonds, QC H8Z 1V2"), "pierrefonds")


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        self.checker = Checker(load_areas(), self.settings)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def leads(self):
        return {l["source_id"]: l for l in kijiji.parse(FIXTURE, "test")}

    def test_parse(self):
        l = self.leads()["1"]
        self.assertEqual(l["price"], 1500)
        self.assertEqual(l["poster_type"], "owner")
        self.assertEqual(l["id"], "kijiji:1")

    def test_buckets(self):
        got = {}
        for sid, l in self.leads().items():
            area, flags = self.checker.run(l)
            got[sid] = (area, bucket(flags), {f["code"] for f in flags})
        self.assertEqual(got["1"][:2], ("Pierrefonds-Roxboro", "review"))
        self.assertIn("tenant_ad", got["2"][2])            # lease transfer: not a landlord
        self.assertEqual(got["3"][:2], ("North Shore", "review"))  # Terrebonne: phase 2 area
        self.assertIn("phase2", got["3"][2])
        self.assertEqual(got["4"][1], "broker")             # RE/MAX wording
        self.assertEqual(got["5"][1], "review")             # property managers are kept (plan 1.8)
        self.assertIn("property_manager", got["5"][2])
        self.assertIn("small", got["6"][2])
        self.assertEqual(got["8"][1], "hidden")             # Gatineau: outside every area
        # boul. Saint-Laurent downtown is not Ville Saint-Laurent: the postal code wins.
        self.assertEqual(got["7"][0], "Downtown / Quartier latin (McGill, Concordia, UQAM)")

    def test_process_keeps_decisions(self):
        leads = list(self.leads().values())
        c = feed.process(self.conn, leads, self.checker)
        self.assertEqual(c["new"], 8)
        self.conn.execute("UPDATE leads SET status='sent' WHERE id='kijiji:1'")
        c2 = feed.process(self.conn, list(self.leads().values()), self.checker)
        self.assertEqual(c2["already_seen"], 8)
        self.assertEqual(self.conn.execute("SELECT status FROM leads WHERE id='kijiji:1'").fetchone()[0], "sent")

    def test_rejected_broker_poster_is_learned(self):
        db.mark_poster(self.conn, "kijiji", "p1", "Rejected as broker by Stefan")
        checker = Checker(load_areas(), self.settings, db.known_posters(self.conn))
        _area, flags = checker.run(self.leads()["1"])
        self.assertEqual(bucket(flags), "broker")


class AreaTests(unittest.TestCase):
    def setUp(self):
        self.c = Checker(load_areas(), load_settings())

    def test_lookup_order(self):
        self.assertEqual(self.c.area_of("Pierrefonds, QC H8Z 1V2"), "Pierrefonds-Roxboro")   # FSA
        self.assertEqual(self.c.area_of("Rue X, Verdun, QC H4G 1J1"), "Montreal (other)")      # 2-letter catch-all
        self.assertEqual(self.c.area_of("Duffcourt, Lachine, QC"), "Lachine")                  # city name
        self.assertEqual(self.c.area_of("NDG, QC"), "NDG / Cote-des-Neiges (UdeM)")
        self.assertEqual(self.c.area_of("QC", 45.4487, -73.8166), "Pointe-Claire")             # map position
        self.assertEqual(self.c.area_of("Montreal, QC", 45.5440, -73.5960), "Rosemont-La Petite-Patrie")
        self.assertEqual(self.c.area_of("Brossard, QC J4W 1A1"), "Brossard (REM corridor)")
        self.assertEqual(self.c.area_of("Sherbrooke, QC J1H 1A1"), "")


class BoardTests(unittest.TestCase):
    CL = """<script type="application/ld+json">{"@type": "ItemList", "itemListElement": [
      {"item": {"@type": "Apartment", "latitude": 45.47, "longitude": -73.62, "numberOfBedrooms": 2,
                "address": {"addressLocality": "Montreal"}}}]}</script>
      <li class="cl-static-search-result" title="5 1/2 upper duplex">
        <a href="https://www.craigslist.org/view/d/montreal-5-1-2/abc123">
          <div class="title">5 1/2 upper duplex</div><div class="details"><div class="price">$1 950</div>
          <div class="location">NDG</div></div></a></li>"""
    LOGIS = """<script type="application/ld+json">{"@type": "SearchResultsPage", "mainEntity": [{"@type": "ItemList",
      "itemListElement": [{"item": {"url": "https://www.logisquebec.com/appartement-a-louer-lachine-l1", "geo": {"latitude": "45.44", "longitude": "-73.68"}}}]}]}</script>
      <li><a href="/appartement-a-louer-lachine-l1" class="property-result adaptive-link">
        <img src="https://i.logisquebec.com/x.jpg" alt=""><h2>Lachine 4 1/2</h2>
        <p class="box-result-unit-12A">Appartement 4½ </p><span class="prix-valeur">1 195$</span>
        <span class="adresse-rue">Duffcourt</span><br><span class="adresse-ville">Lachine (Montréal)</span>
        <div class="box-result-unit-3A">Semi sous-sol</div></a>
      </li>"""

    def test_craigslist(self):
        (l,) = craigslist.parse(self.CL)
        self.assertEqual((l["id"], l["price"], l["address"], l["poster_type"]), ("craigslist:abc123", 1950, "NDG, QC", "owner"))
        self.assertEqual(l["lat"], 45.47)

    def test_logisquebec(self):
        (l,) = logisquebec.parse(self.LOGIS)
        self.assertEqual((l["id"], l["price"], l["address"]), ("logisquebec:1", 1195, "Duffcourt, Lachine, QC"))
        self.assertEqual(l["lat"], "45.44")

    def test_money(self):
        self.assertEqual([money("1 195$"), money("$1,200"), money("1 200,50 $"), money("")], [1195, 1200, 1200, None])


class OutreachTests(unittest.TestCase):
    def test_messages_have_disclaimer_and_fit(self):
        s = load_settings()
        lead = {"address": "Pierrefonds, QC H8Z 1V2", "area": "Pierrefonds-Roxboro"}
        for lang in ("fr", "en"):
            for kind in ("outreach", "followup"):
                m = outreach.render(lead, s, lang, kind)
                self.assertIn(outreach.DISCLAIMER[lang], m)
                self.assertTrue(outreach.length_check(m, s)["ok"], (lang, kind, len(m)))
        self.assertIn("Pierrefonds", outreach.render(lead, s, "fr"))

    def test_language(self):
        self.assertEqual(outreach.detect_lang("Beau 4 1/2 à louer avec stationnement"), "fr")
        self.assertEqual(outreach.detect_lang("Spacious apartment for rent with parking"), "en")


class RentalTests(unittest.TestCase):
    FORM = {"address": "123 Rue Exemple, app. 4", "city": "Pierrefonds", "landlord_name": "Jean Proprio",
            "tenant_names": "Marie Locataire", "lease_start": "2026-11-01", "lease_end": "2027-10-31", "rent": "1650"}

    def test_window_and_followup(self):
        opens, closes = rentals.renewal_window(date(2026, 11, 1), date(2027, 10, 31))
        self.assertEqual((opens, closes), (date(2027, 4, 30), date(2027, 7, 31)))
        self.assertEqual(rentals.followup_date(self.FORM, today=date(2026, 10, 15)), date(2027, 4, 30))
        short = dict(self.FORM, lease_end="2027-04-30")  # 6-month lease: 1-2 month window
        self.assertEqual(rentals.followup_date(short, today=date(2026, 10, 15)), date(2027, 2, 28))

    def test_validate(self):
        self.assertEqual(rentals.validate(self.FORM), [])
        self.assertTrue(rentals.validate(dict(self.FORM, lease_end="2026-10-01")))
        self.assertTrue(rentals.validate({}))

    def test_save_email_ics_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "t.sqlite3")
            rid = rentals.save(conn, self.FORM)
            r = rentals.get(conn, rid)
            mail = rentals.tanya_email(r)
            self.assertIn("Marie Locataire", mail["body"])
            self.assertIn(r["followup_date"], mail["body"])
            ics = rentals.followup_ics(r)
            self.assertIn("BEGIN:VEVENT", ics)
            self.assertIn("DTSTART;VALUE=DATE:" + r["followup_date"].replace("-", ""), ics)
            titles = [t for t, _rows in rentals.lease_sheet(r)]
            self.assertTrue(titles[0].startswith("A."))
            self.assertEqual(len(rentals.export_rows(conn)), 1)
            conn.close()



class ReviewScreenTests(unittest.TestCase):
    """The review screen's API: To send queue, undo (including un-learning a Broker mis-click), bulk, counts."""

    def setUp(self):
        from desk import web
        self.web = web
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        self.settings = load_settings()
        feed.process(self.conn, kijiji.parse(FIXTURE, "test"), Checker(load_areas(), self.settings))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def decide(self, lid, **body):
        return self.web.api_decide(self.conn, lid, body)

    def status(self, lid):
        return self.conn.execute("SELECT status FROM leads WHERE id=?", (lid,)).fetchone()[0]

    def test_approved_waits_in_to_send(self):
        self.decide("kijiji:1", action="approve", message="Bonjour")
        review = [l["id"] for l in self.web.api_leads(self.conn, self.settings, "review")["leads"]]
        to_send = self.web.api_leads(self.conn, self.settings, "approved")
        self.assertNotIn("kijiji:1", review)
        self.assertEqual([l["id"] for l in to_send["leads"]], ["kijiji:1"])
        self.assertEqual(to_send["counts"]["approved"], 1)
        self.decide("kijiji:1", action="sent")
        self.decide("kijiji:1", action="unsend")  # undo of Mark sent
        self.assertEqual(self.status("kijiji:1"), "approved")

    def test_undo_broker_reject_forgets_poster(self):
        self.decide("kijiji:1", action="reject", reason="Broker")
        self.assertIn("p1", db.known_posters(self.conn))
        self.decide("kijiji:1", action="undo")
        self.assertNotIn("p1", db.known_posters(self.conn))
        self.assertEqual(self.status("kijiji:1"), "new")

    def test_outcome_undo_and_rebucket(self):
        self.decide("kijiji:1", action="sent")
        self.decide("kijiji:1", action="listing")
        self.decide("kijiji:1", action="set_outcome", outcome="")
        self.assertIsNone(self.conn.execute("SELECT outcome FROM leads WHERE id='kijiji:1'").fetchone()[0])
        self.decide("kijiji:8", action="rebucket", bucket="review")
        self.assertEqual(self.conn.execute("SELECT bucket FROM leads WHERE id='kijiji:8'").fetchone()[0], "review")
        self.assertEqual(self.decide("kijiji:8", action="rebucket", bucket="nope")[1], 400)

    def test_counts_match_cards(self):
        for tab in ("review", "broker", "hidden"):
            res = self.web.api_leads(self.conn, self.settings, tab)
            self.assertEqual(res["counts"].get(tab, 0), len(res["leads"]), tab)


if __name__ == "__main__":
    unittest.main()
