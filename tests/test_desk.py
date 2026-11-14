"""Run with: python3 -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import capture, db, feed, lease_import, lease_scan, leases, outreach, tal  # noqa: E402
from desk.checks import Checker, bucket  # noqa: E402
from desk.config import load_areas, load_settings  # noqa: E402
from desk.sources import appartogo, craigslist, kijiji, lespac, logisquebec  # noqa: E402
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

    APPARTOGO = """<div itemscope itemtype="https://schema.org/Product" class="listing listing-card" data-listing-id="3385138">
        <a class="tile-link" href="/fr/appartement-louer/canada/quebec/ile-de-montreal/lachine/3385138">
        <img fetchpriority="high" itemprop="image" class="img-fluid" src="https://cdn.appartogo.com/listings/t/1.jpg" />
        <span itemprop="price" content="1400.0" class="h4">1&#xA0;400$</span>
        <span class="pieces bedrooms" title="2 Chambres" aria-label="2 Chambres">2<i class="fa-solid fa-bed"></i></span>
        <p class="listing-city">Lachine</p><h3 itemprop="name" class="card-title mt-2">Le Provost | 2710 Rue Provost, Montreal &#xB7; 2 ch.</h3></a></div>
      <div itemscope itemtype="https://schema.org/Product" class="listing listing-card" data-listing-id="7">
        <a class="tile-link" href="/fr/condo-louer/canada/quebec/laval/chomedey/7"><span itemprop="price" content="1650.0">
        <p class="listing-city">Chomedey</p><h3 itemprop="name" class="card-title mt-2">4 1/2 au rez-de-chauss&#xE9;e</h3></a></div>"""
    LESPAC = """<script>if (SearchApp) { var searchResponse = {"searchResults": [{"listingPublicId": "228047031",
      "title": "4\u00bd lumineux \u2013 parc La Fontaine", "description": "**Chauff\u00e9** pr\u00e8s du parc", "price": 1650.0,
      "publicReleaseTimestamp": 1794600000000, "mainImageUrl": "https://cdn.lespac.com/binary/zoomedGallery/1.jpg",
      "listingDisplayUrl": "https://www.lespac.com/montreal/immobilier-location-logements/4-lumineux_228047031D0.jsa?searchCriteria=x",
      "cityLabel": "Montr\u00e9al / Plateau Mont-Royal", "searchPageTrackingInfo": {"listing-advertiser-type": {"value": "INDIVIDUAL"},
      "listing-dealerid": {"value": "884573488"}}}], "totalResults": 1};
      SearchApp.initApp('searchAppContainer', {}, searchResponse, {}); }</script>"""

    def test_appartogo(self):
        a, b = appartogo.parse(self.APPARTOGO, "ile-de-montreal", "appartement-louer")
        self.assertEqual((a["id"], a["price"], a["bedrooms"], a["address"], a["image"]),
                         ("appartogo:3385138", 1400, "2", "2710 Rue Provost, Lachine, QC", "https://cdn.appartogo.com/listings/t/1.jpg"))
        self.assertEqual(b["address"], "Chomedey, QC")  # no street number in the title: the area only
        self.assertEqual(capture.canonical_id(a["url"]), a["id"])

    def test_lespac(self):
        (l,) = lespac.parse(self.LESPAC)
        self.assertEqual((l["id"], l["price"], l["address"], l["poster_type"], l["poster_id"]),
                         ("lespac:228047031", 1650, "Plateau Mont-Royal, Montréal, QC", "owner", "884573488"))
        self.assertEqual(l["url"], "https://www.lespac.com/montreal/immobilier-location-logements/4-lumineux_228047031D0.jsa")
        self.assertEqual(capture.canonical_id(l["url"]), l["id"])
        self.assertNotIn("*", l["snippet"])
        with self.assertRaises(ValueError):
            lespac.parse("<html>no data</html>")

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
        opens, closes = leases.renewal_window(date(2026, 11, 1), date(2027, 10, 31))
        self.assertEqual((opens, closes), (date(2027, 4, 30), date(2027, 7, 31)))
        self.assertEqual(leases.followup_date(self.FORM, today=date(2026, 10, 15)), date(2027, 4, 30))
        short = dict(self.FORM, lease_end="2027-04-30")  # 6-month lease: 1-2 month window
        self.assertEqual(leases.followup_date(short, today=date(2026, 10, 15)), date(2027, 2, 28))

    def test_validate(self):
        self.assertEqual(leases.validate(self.FORM), [])
        self.assertTrue(leases.validate(dict(self.FORM, lease_end="2026-10-01")))
        self.assertTrue(leases.validate({}))

    def test_save_email_ics_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "t.sqlite3")
            rid = leases.save(conn, self.FORM)
            r = leases.get(conn, rid)
            mail = leases.office_email(r)
            self.assertIn("Marie Locataire", mail["body"])
            self.assertIn(r["followup_date"], mail["body"])
            ics = leases.followup_ics(r)
            self.assertIn("BEGIN:VEVENT", ics)
            self.assertIn("DTSTART;VALUE=DATE:" + r["followup_date"].replace("-", ""), ics)
            titles = [t for t, _rows in leases.lease_sheet(r)]
            self.assertTrue(titles[0].startswith("A."))
            self.assertEqual(len(leases.export_rows(conn)), 1)
            msg = leases.renewal_message(r, lang="fr")
            self.assertIn("2027-04-30", msg["body"])          # notice window opens
            self.assertIn("Jean Proprio", msg["body"])
            self.assertIn("Jean", leases.renewal_message(r, lang="en")["body"])
            self.assertEqual(leases.renewal_status(r, today=date(2026, 10, 15)), "upcoming")
            self.assertEqual(leases.renewal_status(r, today=date(2027, 4, 20)), "due")
            # Past the end date the lease renews itself for the same length (art. 1941)...
            self.assertEqual(leases.current_term(r, date(2027, 11, 1))[1:], (date(2028, 10, 31), True))
            self.assertEqual(leases.next_call(r, today=date(2027, 11, 1)), date(2028, 4, 30))
            self.assertEqual(leases.renewal_status(r, today=date(2027, 11, 1)), "upcoming")
            # ...unless the tenancy is marked ended.
            self.assertEqual(leases.renewal_status(dict(r, tenancy_status="ended"), today=date(2027, 11, 1)), "ended")
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


SAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "leases-sample.jsonl"


class LeaseImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_sample_file_imports(self):
        rep = lease_import.import_records(self.conn, lease_import.read_file(SAMPLE))
        self.assertEqual((rep["added"], len(rep["rejected"]), rep["needs_review"]), (2, 0, 1))
        got = {json_row["_import"]["external_id"]: json_row for json_row in leases.export_rows(self.conn)}
        a = got["nexone:TR-2025-0412"]
        self.assertEqual((a["rent"], a["heating"], a["furnished"], a["tenancy_status"]), ("1850", "Landlord", "", "unknown"))
        b = got["sales_tracker:2024:row-88"]
        self.assertEqual(b["rent"], "1495")                       # '1 495,00 $' normalised
        self.assertEqual(leases.renewal_status(b, today=date(2026, 10, 15)), "check")  # confidence 0.6
        # Same file again: updates in place, no duplicates.
        rep2 = lease_import.import_records(self.conn, lease_import.read_file(SAMPLE))
        self.assertEqual((rep2["added"], rep2["updated"]), (0, 2))
        # A person confirms the dates: later imports leave it alone.
        self.assertTrue(lease_import.mark_verified(self.conn, b["id"]))
        rep3 = lease_import.import_records(self.conn, lease_import.read_file(SAMPLE))
        self.assertEqual((rep3["updated"], len(rep3["skipped"])), (1, 1))

    def test_rejects_and_hand_entered_duplicates(self):
        bad = [{"external_id": "x1", "source": "nexone", "address": "1 Rue A", "city": "Laval", "landlord_name": "A",
                "lease_start": "2026/07/01", "lease_end": "2027-06-30"},
               {"external_id": "x2", "source": "fax", "address": "1 Rue A", "city": "Laval", "landlord_name": "A",
                "lease_start": "2026-07-01", "lease_end": "2026-06-30"},
               {"source": "nexone"}]
        rep = lease_import.import_records(self.conn, bad)
        self.assertEqual(len(rep["rejected"]), 3)
        self.assertTrue(any("YYYY-MM-DD" in e for e in rep["rejected"][0]["errors"]))
        leases.save(self.conn, {"address": "1 Rue A", "city": "Laval", "landlord_name": "A", "tenant_names": "B",
                                "lease_start": "2026-07-01", "lease_end": "2027-06-30", "rent": "1500"})
        dup = dict(bad[0], lease_start="2026-07-01")
        rep = lease_import.import_records(self.conn, [dup])
        self.assertEqual((rep["added"], len(rep["skipped"])), (0, 1))


class TalTests(unittest.TestCase):
    def test_base_only(self):
        c = tal.compute(1500, date(2026, 7, 1))
        self.assertEqual((c["increase"], c["new_rent"], c["provisional"]), (46.5, 1546, False))

    def test_building_figures(self):
        # 1 of 8 units' worth of rent; municipal taxes +4 % (0.9 % above the base); $10,000 of work.
        c = tal.compute(1000, "2026-07-01", {"building_rents": 8000, "municipal_tax_ref": 24000,
                                             "municipal_tax_next": 24960, "capex": 10000})
        self.assertEqual([l["amount"] for l in c["lines"]], [31.0, 2.25, 5.21])
        self.assertEqual((c["increase"], c["new_rent"]), (38.46, 1038))

    def test_rules(self):
        # Tax rise below the base adds nothing; a tax drop lowers the rent.
        c = tal.compute(1000, "2026-07-01", {"school_tax_prev": 1200, "school_tax_ref": 1220})
        self.assertEqual(c["lines"][1]["amount"], 0.0)
        c = tal.compute(1000, "2026-07-01", {"insurance_prev": 1200, "insurance_ref": 1080})
        self.assertEqual(c["lines"][1]["amount"], -10.0)
        # A loan-financed job can't add more than the year's repayment.
        c = tal.compute(1000, "2026-07-01", {"capex": 100000, "capex_loan_repayment": 1200})
        self.assertEqual(c["lines"][1]["amount"], 100.0)
        # No published rate yet for that renewal: latest rate, marked provisional.
        self.assertTrue(tal.compute(1000, "2027-07-01")["provisional"])
        self.assertIsNone(tal.compute("", "2026-07-01"))

    def test_message_has_the_numbers(self):
        r = {k: "" for k in leases.FIELD_KEYS}
        r.update(address="1 Rue A", city="Laval", landlord_name="Jean", lease_start="2026-07-01",
                 lease_end="2027-06-30", followup_date="2026-12-30", rent="1500")
        self.assertIn("1 546 $", leases.renewal_message(r, lang="fr")["body"])
        self.assertIn("$1,546", leases.renewal_message(r, lang="en")["body"])
        r["tal_inputs"] = {"current_rent": "1600"}
        self.assertIn("1 650 $", leases.renewal_message(r, lang="fr")["body"])


class FakeStream:
    """Stands in for client.beta.messages.stream(): yields text in awkward chunks, like the API does."""
    def __init__(self, text, stop="end_turn"):
        self.text, self.stop, self.kwargs = text, stop, None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        return (self.text[i:i + 7] for i in range(0, len(self.text), 7))

    def get_final_message(self):
        class M:
            pass
        m = M()
        m.stop_reason = self.stop
        return m


def fake_client(stream):
    class Beta:
        pass
    c = Beta()
    c.beta = Beta()
    c.beta.messages = Beta()
    c.beta.messages.stream = stream
    return c


MODEL_OUTPUT = "\n".join([
    '{"field": "landlord_name", "value": "Jean Exemple", "confidence": 0.98, "evidence": "Locateur : Jean Exemple", "page": 1}',
    '{"field": "lease_start", "value": "2026-07-01", "confidence": 0.97, "evidence": "du 1er juillet 2026", "page": 1}',
    '{"field": "rent", "value": "1 850 $", "confidence": 0.7, "evidence": "1 850 (handwritten)", "page": 2}',
    '{"field": "heating", "value": "landlord", "confidence": 0.95, "evidence": "Chauffage : locateur", "page": 2}',
    '{"field": "lease_end", "value": "30 juin 2027", "confidence": 0.9}',
    '{"field": "not_a_field", "value": "x"}',
    'Some stray prose',
    '{"done": true, "document": "TAL lease, 2 pages", "notes": "Page 2 is blurry"}',
])


class LeaseScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._dir = lease_scan.SCAN_DIR
        lease_scan.SCAN_DIR = Path(self.tmp.name) / "scans"
        self.files = [{"name": "p1.jpg", "type": "image/jpeg", "data": "data:image/jpeg;base64,/9j/AAAA"},
                      {"name": "p2.pdf", "type": "application/pdf", "data": "JVBERi0xLjQK"}]

    def tearDown(self):
        lease_scan.SCAN_DIR = self._dir
        self.tmp.cleanup()

    def test_stream_fills_fields_in_the_import_shape(self):
        stream = FakeStream(MODEL_OUTPUT)
        events = list(lease_scan.extract(self.files, client=fake_client(stream)))
        self.assertEqual(events[0]["type"], "start")
        fields = {e["field"]: e for e in events if e["type"] == "field"}
        self.assertEqual(fields["rent"]["value"], "1850")             # normalised like the import
        self.assertTrue(fields["rent"]["check"])                       # 0.7 < 0.9: highlighted to check
        self.assertEqual(fields["heating"]["value"], "Landlord")
        self.assertNotIn("lease_end", fields)                          # not YYYY-MM-DD: skipped, not guessed
        self.assertEqual(events[-1], {"type": "done", "document": "TAL lease, 2 pages", "notes": "Page 2 is blurry"})
        # Pages stay on this computer.
        saved = sorted(p.name for p in (lease_scan.SCAN_DIR / events[0]["scan_id"]).iterdir())
        self.assertEqual(saved, ["page-01.jpg", "page-02.pdf"])
        # What went to the API: one image and one PDF block, the fallback opt-in, the configured model.
        kinds = [b["type"] for b in stream.kwargs["messages"][0]["content"]]
        self.assertEqual(kinds.count("image") + kinds.count("document"), 2)
        self.assertEqual(stream.kwargs["fallbacks"], "default")

    def test_refusal_and_bad_files(self):
        events = list(lease_scan.extract(self.files[:1], client=fake_client(FakeStream("", stop="refusal"))))
        self.assertEqual(events[-1]["type"], "error")
        with self.assertRaises(lease_scan.ScanError):
            list(lease_scan.extract([{"name": "x.docx", "type": "application/msword", "data": "AAAA"}]))
        with self.assertRaises(lease_scan.ScanError):
            list(lease_scan.extract([]))

    def test_saved_lease_keeps_scan_provenance(self):
        conn = db.connect(Path(self.tmp.name) / "t.sqlite3")
        meta = {"scan_id": "20261015-120000", "fields": {"rent": {"confidence": 0.7, "check": True}}}
        rid = leases.save(conn, {"address": "1 Rue A", "city": "Laval", "landlord_name": "A", "tenant_names": "B",
                                 "lease_start": "2026-07-01", "lease_end": "2027-06-30", "rent": "1850",
                                 "scan_meta": json.dumps(meta)})
        self.assertEqual(leases.get(conn, rid)["_scan"], meta)
        conn.close()


if __name__ == "__main__":
    unittest.main()
