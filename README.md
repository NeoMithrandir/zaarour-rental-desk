# Rentals desk

Stefan's daily tool for the 2027 rental plan ([plan](../docs/2027-growth-action-plan.md)). It covers:

| Plan item | What it does here |
|---|---|
| 1.1 Daily lead feed | Every morning, reads the newest Kijiji Long Term Rentals ads for Saint-Laurent, the West Island, Vaudreuil/Île-Perrot and Laval. New ads only: anything already seen keeps Stefan's decision. |
| 1.2 Sources | Kijiji is automatic. Facebook Marketplace, LesPAC, DuProprio, Craigslist and Louer.ca ads are pasted in by hand (box at the bottom of the screen) and go through the same checks. |
| 1.3 Auto-filtering | Drops tenant ads (lease transfers, sublets, rooms) and anything outside the target area. Flags likely brokers (broker wording, agency names, known broker phones/emails, posters Stefan rejected as brokers before). Checks the address against a Centris export once one is loaded; until then each card has a "check Centris / Realtor" link. |
| 1.4 Send | **Approve** copies the message and opens the ad; Stefan pastes it in Kijiji's contact box and sends, then presses **Mark sent**. Nothing is sent automatically. |
| 1.5 / 1.6 Message | French and English templates with the broker disclaimer, a character counter against the platform limit, and the disclaimer re-added if an edit drops it. |
| 1.8 Property managers | Kept, tagged, shown after owners. All ads from one poster collapse into one card, so a manager with 8 ads gets one message. |
| 1.10 Second touch | The **Sent** tab has a short follow-up message ready for non-responders. |
| E.1 Tanya form | One form per signed lease: opens the email to Tanya in your mail app, downloads a calendar event for the renewal call 6 months before the lease ends, and saves the lease end date (the data engine 3 needs). |
| E.2 Leases | A printable lease prep sheet laid out by the sections of the mandatory TAL lease form, filled from the same form. |
| Scorecard | Surfaced, sent, rejected, replied, presentations and signed listings per source per week. |

## How Stefan runs it (Mac)

Once:

1. Install Python 3 (already on this Mac).
2. Copy `config/settings.example.json` to `config/settings.json` and fill in Stefan's phone, email, Tanya's email and the rental page link.

Every morning: double-click **`morning.command`**. It fetches today's ads (about a minute), then opens the review screen in the browser. Then:

- `j` / `k` to move, **Approve** (`a`) to copy the message and open the ad, paste and send on Kijiji, then **Mark sent**.
- **Reject…** (`r`) with a reason. "Broker" teaches the tool: that poster is flagged next time.
- **Later** (`l`) parks an ad.
- On the **Sent** tab, record what happened: Replied, Presentation booked, Listing signed, No reply.

From a terminal instead:

```bash
cd rentals-desk
python3 -m desk morning
```

Other commands: `python3 -m desk fetch` (fetch only), `python3 -m desk serve` (screen only),
`python3 -m desk recheck` (re-run checks after editing brokers or loading a Centris export),
`python3 -m desk export-rentals` (rentals to CSV).

## Tuning

- **Area:** `config/areas.json` (postal-code prefixes and city names per area).
- **Kijiji searches:** `kijiji.searches` in settings (centre point and radius per search).
- **Known brokers:** `config/known_brokers.csv`. Rows are `keyword`, `phone`, `email` or `poster_id`.
- **Centris check:** put a Matrix export of active rentals at `data/centris_active_rentals.csv`, then `python3 -m desk recheck`.
- **Message:** `templates/outreach_fr.txt`, `outreach_en.txt`, `followup_*.txt`. Placeholders: `{city}`, `{agent_name}`, `{agent_phone}`, `{agency}`, `{rental_page_url}`.
- **Character limit:** `message_char_limit` (1000 by default; Stefan to confirm Kijiji's real limit).

## Notes

- Runs on one computer at `http://127.0.0.1:8765`; data is in `data/desk.sqlite3` (not committed). For Stefan to use it from his own machine, either run it there or host it (see `docs/next-steps.md`).
- The Kijiji reader loads about 8 search pages a day, a few seconds apart, and reads only what the search page shows. It does not open ads, collect poster contact details or post anything. Kijiji can change its page at any time; if a fetch reports 0 ads, the reader needs updating.
- Standard library only; tests: `python3 -m unittest discover -s tests`.
