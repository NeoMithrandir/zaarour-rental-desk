# Rentals desk

Stefan's daily tool for the 2027 rental plan ([plan](../docs/2027-growth-action-plan.md)). It covers:

| Plan item | What it does here |
|---|---|
| 1.1 Daily lead feed | Opens at once on what it already has, then reads any board whose last read is out of date in the background (LogisQuébec, liv.rent, and the boards' own email alerts; see [BOARDS.md](BOARDS.md)) across every market in the research: the phase 1 corridor (Saint-Laurent to the West Island, Vaudreuil/Île-Perrot) and Laval first, then Montreal island (downtown and the student areas, Griffintown/Saint-Henri, Plateau, Rosemont, Villeray, NDG/Côte-des-Neiges, the rest of the island), the South Shore (Brossard REM corridor, Longueuil) and the North Shore. New ads only: anything already seen keeps Stefan's decision. |
| 1.2 Sources | Three routes, chosen per board in [BOARDS.md](BOARDS.md): read by the desk (LogisQuébec, liv.rent), saved-search **email alerts** (Kijiji, Craigslist, and any board that sends them), and the **capture button** Stefan clicks on an ad while browsing (Facebook Marketplace and groups, Rentals.ca, PadMapper/Zumper, Appartogo, LesPAC, Louer.ca). Kijiji's and Craigslist's terms forbid automated reading, so their scanners are off by default. Every route goes through the same checks. |
| Learning | Every Approve, Reject (with reason), Later and outcome is logged with the ad's signals and the message template used. `learnings/` holds plain-markdown findings per board and per signal, written so an agent can follow them; small samples are marked tentative. See [learnings/README.md](learnings/README.md). |
| 1.3 Auto-filtering | Drops tenant ads (lease transfers, sublets, rooms) and anything outside the target area. Flags likely brokers (broker wording, agency names, known broker phones/emails, posters Stefan rejected as brokers before). Checks the address against a Centris export once one is loaded; until then each card has a "check Centris / Realtor" link. |
| 1.4 Send | **Approve** copies the message and opens the ad; Stefan pastes it in the board's contact box and sends, then confirms **Mark sent** (approved ads wait in **To send** until then). Nothing is sent automatically. |
| 1.5 / 1.6 Message | French and English templates with the broker disclaimer, a character counter against the platform limit, and the disclaimer re-added if an edit drops it. |
| 1.8 Property managers | Kept, tagged, shown after owners. All ads from one poster collapse into one card (on Craigslist, which has no poster ids, identical titles count as one poster), so a manager with 8 ads gets one message. |
| 1.9 Geography | Phase 1 areas sort to the top; phase 2 areas carry a "Phase 2" tag, and a filter at the top shows one phase at a time. |
| 1.10 Second touch | The **Sent** tab has a short follow-up message ready for non-responders. |
| E.1 Lease renewal tracker | **New lease** (one form per signed lease) opens the lease-details email to the office in your mail app, downloads a calendar event for the renewal call 6 months before the lease ends, and saves the lease. **Renewals** lists every saved lease by renewal call date ("Call now" within two weeks) with a ready FR/EN message to the landlord: the notice window dates and an offer to handle the increase, the notice and a re-listing. |
| TAL calculator | Each lease has a **TAL calculator** that follows the Tribunal administratif du logement's method for notices given since 1 January 2026 (Règlement sur les critères de fixation de loyer, décret 1455-2025): the base percentage (3.1 % for renewals starting 2 April 2026 to 1 April 2027), municipal and school taxes and insurance above the base, and 5 % of major work, each by the unit's share of the building. With only the rent it applies the base rate; add the building's figures for the full calculation. The result goes into the landlord message. Add each January's new rates to `config/tal_rates.json`; until then the next year's renewals are marked provisional. |
| E.2 Leases | A printable lease prep sheet laid out by the sections of the mandatory TAL lease form, filled from the same form. |
| Scorecard | Surfaced, sent, rejected, replied, presentations and signed listings per source per week. |

## How Stefan runs it (Mac)

Once:

1. Install Python 3 (already on this Mac).
2. Copy `config/settings.example.json` to `config/settings.json` and fill in Stefan's phone and email, the office email for lease paperwork (`office_email`) and the rental page link.

Every morning: double-click **`morning.command`** (or use the boot loader page). The review screen opens straight away
on what the desk already has; boards read in the last few hours are reused, and stale ones are read in the background
while Stefan works (a line under the tabs shows progress, and new ads join the Review count). Then:

- The review screen shows one ad at a time. **Approve** (`A`) copies the message and opens the ad: paste it in the board's contact box and send. When you come back to the desk, it asks whether you sent it; **Yes, mark sent** (`S`). Anything approved but not marked sent waits in the **To send** tab.
- **Reject** (`R`) shows the reasons; pick one with a click or `1`–`7`. "Broker" teaches the tool: that poster is flagged next time.
- **Later** (`L`) parks an ad. **Undo** (`Z`) reverses the last action, and keeps going back.
- `V` switches to a list where you can tick several ads and reject or park them together. `?` lists every shortcut.
- On the **Sent** tab, record what happened: Replied, Presentation booked, Listing signed, No reply. Follow-up messages are there too.

From a terminal instead:

```bash
cd rentals-desk
python3 -m desk morning
```

Other commands: `python3 -m desk fetch` (read stale boards and stop; `--force` reads every board),
`python3 -m desk serve` (screen, with stale boards read in the background; `--no-scan` to skip that),
`python3 -m desk learn` (rewrite `learnings/` now),
`python3 -m desk recheck` (re-run checks after editing brokers or loading a Centris export),
`python3 -m desk export-leases` (the renewal tracker to CSV).
`python3 -m desk import-leases [--dry-run] [file]` loads leases from outside the desk (default: everything in `data/import/inbox/`, which the morning run also picks up). The data shape is in [docs/lease-import-spec.md](../docs/lease-import-spec.md).

## Tuning

- **Areas:** `config/areas.json` (phase, postal-code prefixes, city names and a map centre per area).
- **Boards:** `sources` in settings turns each board on or off. Kijiji searches are under `kijiji.searches`, LogisQuébec regions under `logisquebec.regions`, liv.rent cities under `livrent.cities`.
- **Known brokers:** `config/known_brokers.csv`. Rows are `keyword`, `phone`, `email` or `poster_id`.
- **Centris check:** put a Matrix export of active rentals at `data/centris_active_rentals.csv`, then `python3 -m desk recheck`.
- **Message:** `templates/outreach_fr.txt`, `outreach_en.txt`, `followup_*.txt`. Placeholders: `{city}`, `{agent_name}`, `{agent_phone}`, `{agency}`, `{rental_page_url}`.
- **Character limit:** `message_char_limit` (1000 by default; Stefan to confirm Kijiji's real limit).

## Email alerts

On each board, save the search (owners only where the board allows it, newest first, the target areas) and turn on
email alerts. Then either:

- **Folder:** drag the alert emails from Mail into `data/inbox/` (they land as `.eml` files); or
- **Mailbox:** make a mail rule that files alert emails into a folder (default name `Desk alerts`), fill in
  `alerts.imap` in `config/settings.json` (host, user, folder), and store the mailbox's app password in the keychain
  once:

  ```bash
  security add-generic-password -s zaarour-desk-alerts -a stefan@zaarour.ca -w
  ```

The desk reads that one folder read-only every 30 minutes while it is open: emails stay unread and nothing is moved
or deleted. It reads only the emails, not the ad pages they link to.

## Capture button

Click **Capture button** at the top of the desk and drag **Send to desk** onto the browser's bookmarks bar. On any ad,
select the description if you like, click the bookmark, check the small form that opens, and click **Add to desk**.
It reads only the page you are on, only when you click. An ad already in the desk (for example from Kijiji) is
matched, not doubled.

## Notes

- Runs on one computer at `http://127.0.0.1:8765`; data is in `data/desk.sqlite3` (not committed). For Stefan to use it from his own machine, either run it there or host it (see `docs/next-steps.md`).
- Reading is polite: an honest user agent with the agency's contact details, robots.txt checked first, at least 6
  seconds between requests to one board (more if it asks), unchanged pages skipped (ETag / Last-Modified), backing
  off on 429/503 and leaving a board alone for a while after a refusal. Pages are read newest first and the desk
  stops once it reaches ads it has already seen. Every read is logged in the `scan_runs` table.
- It reads only what the search pages show. It does not open ads, collect poster contact details or post anything.
  Any board can change its page; if a board reports 0 ads, its reader needs updating, and the other boards still run.
- The first read of the day takes a couple of minutes in the background; a second launch the same morning reuses it.
- Standard library only; tests: `python3 -m unittest discover -s tests`.
