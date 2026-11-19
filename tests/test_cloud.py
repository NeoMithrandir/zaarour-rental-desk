"""The Mac's side of the online desk (desk/cloud.py). Run with: python3 -m unittest discover -s tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desk import cloud, db  # noqa: E402


def lead(i, **kw):
    return {"id": f"kijiji:{i}", "source": "kijiji", "url": f"https://www.kijiji.ca/v/x/{i}", "title": f"Ad {i}",
            "snippet": "", "address": "Laval, QC H7N 1A1", "flags": [], "bucket": "review", **kw}


class FakeDesk:
    """Answers like the online desk, and keeps what the Mac sent."""

    def __init__(self, pull=None):
        self.sent, self.pulls = [], list(pull or [])

    def __call__(self, settings, method, path, body=None, timeout=60):
        self.sent.append((method, path.split("?")[0], body))
        if path.startswith("api/agent/ingest"):
            return {"inserted": len(body["leads"]), "updated": 0, "expired": 0}
        if path.startswith("api/agent/pull"):
            return self.pulls.pop(0) if self.pulls else {"cursor": "z", "leads": [], "events": [], "drafts": [], "rentals": []}
        return {}


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state, cloud.STATE = cloud.STATE, Path(self.tmp.name) / "cloud-state.json"
        self.request = cloud.request
        self.conn = db.connect(Path(self.tmp.name) / "desk.sqlite3")
        self.settings = {"cloud": {"url": "https://rentals.example", "token": "t"}, "sources": []}

    def tearDown(self):
        cloud.STATE, cloud.request = self.state, self.request
        self.conn.close()
        self.tmp.cleanup()

    def test_secrets_never_leave_the_mac(self):
        s = {"anthropic_api_key": "sk-1", "agent": {"name": "Stefan"}, "cloud": {"token": "t"},
             "phone_search": {"brave_api_key": "b", "provider": "brave"}, "alerts": {"imap": {"password": "p", "host": "h"}},
             "drafts": {"max_tokens": 1200}}
        out = cloud.strip_secrets(s)
        self.assertEqual(out, {"agent": {"name": "Stefan"}, "phone_search": {"provider": "brave"},
                               "alerts": {"imap": {"host": "h"}}, "drafts": {"max_tokens": 1200}})

    def test_off_until_url_and_token(self):
        self.assertFalse(cloud.enabled({}))
        self.assertFalse(cloud.enabled({"cloud": {"url": "https://x", "token": ""}}))
        self.assertTrue(cloud.enabled(self.settings))

    def test_scanned_facts_stamp_changed_at_but_decisions_do_not(self):
        db.upsert_lead(self.conn, lead(1))
        first = self.conn.execute("SELECT changed_at FROM leads").fetchone()[0]
        self.assertTrue(first)
        self.conn.execute("UPDATE leads SET changed_at=NULL")
        self.conn.execute("UPDATE leads SET status='approved', message='hi'")
        self.assertIsNone(self.conn.execute("SELECT changed_at FROM leads").fetchone()[0])
        db.upsert_lead(self.conn, lead(1, title="Ad 1, lower price"))
        self.assertTrue(self.conn.execute("SELECT changed_at FROM leads").fetchone()[0])

    def test_push_sends_only_what_changed(self):
        fake = cloud.request = FakeDesk()
        for i in range(3):
            db.upsert_lead(self.conn, lead(i))
        self.conn.commit()
        cloud.push(self.conn, self.settings, log=lambda *_: None)
        self.assertEqual(len(fake.sent[0][2]["leads"]), 3)
        self.assertNotIn("anthropic_api_key", fake.sent[0][2]["settings"])
        fake.sent.clear()
        cloud.push(self.conn, self.settings, log=lambda *_: None)
        self.assertEqual(fake.sent[0][2]["leads"], [])
        db.upsert_lead(self.conn, lead(1, title="edited"))
        self.conn.commit()
        fake.sent.clear()
        cloud.push(self.conn, self.settings, log=lambda *_: None)
        self.assertEqual([l["id"] for l in fake.sent[0][2]["leads"]], ["kijiji:1"])

    def test_pull_brings_decisions_back_without_touching_facts(self):
        db.upsert_lead(self.conn, lead(1))
        self.conn.commit()
        online = {"id": "kijiji:1", "title": "something else", "status": "sent", "message": "Bonjour", "sent_at": "2026-11-19T10:00:00",
                  "outcome": None, "decided_at": "2026-11-19T09:00:00", "template": "outreach_fr@1", "approach": "template"}
        added = {**lead(2, title="Captured on the desk"), "flags": "[]", "status": "new", "first_seen": "2026-11-19T09:00:00"}
        cloud.request = FakeDesk(pull=[{"cursor": "c1", "leads": [online, added], "drafts": [], "rentals": [],
                                        "events": [{"id": 7, "lead_id": "kijiji:1", "at": "x", "action": "sent"}],
                                        "known_posters": [{"source": "kijiji", "poster_id": "p9", "label": "Rejected as broker by Stefan", "added_at": "x"}]}])
        cloud.pull(self.conn, self.settings, log=lambda *_: None)
        r = self.conn.execute("SELECT title, status, message FROM leads WHERE id='kijiji:1'").fetchone()
        self.assertEqual(tuple(r), ("Ad 1", "sent", "Bonjour"))
        self.assertEqual(self.conn.execute("SELECT title FROM leads WHERE id='kijiji:2'").fetchone()[0], "Captured on the desk")
        self.assertIsNone(self.conn.execute("SELECT changed_at FROM leads WHERE id='kijiji:2'").fetchone()[0])
        self.assertEqual(db.known_posters(self.conn), {"p9": "Rejected as broker by Stefan"})
        self.assertEqual(cloud.load_state()["events_after"], 7)
        self.assertEqual(cloud.load_state()["pull_since"], "c1")


if __name__ == "__main__":
    unittest.main()
