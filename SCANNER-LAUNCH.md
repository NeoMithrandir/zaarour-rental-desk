# Relaunching the board scanners

Audit of 14 Nov 2026, branch `rental-desk-v1`. Goal: the desk reads every board it can by itself, within a week,
board by board, as each company's written permission arrives.

## How the switch works

Every scanner is off until its board's permission is on file. In `config/settings.json`:

```json
"permissions": {
  "logisquebec": {"scan_enabled": true, "letter": "data/permissions/logisquebec-2026-11-18.pdf",
                  "granted_by": "Jane Doe, LogisQuébec", "granted_on": "2026-11-18", "expires": "",
                  "max_requests_per_hour": 20, "min_interval_seconds": 10}
}
```

- A board is read only when `scan_enabled` is true **and** `letter` names the written permission **and** `expires`
  (optional) has not passed. Otherwise `python3 -m desk fetch` prints "Not read until written permission is on file"
  and makes no request to that board at all.
- `max_requests_per_hour` and `min_interval_seconds` hold the desk to the limits the letter sets (per board, on top of
  the defaults: 6 s between requests, robots.txt Crawl-delay, back-off on 429/503, a day's cool-off after a 403).
- The desk still reads robots.txt and still stops at any bot check, CAPTCHA or login, permission or not. If a letter
  allows something robots.txt disallows, or comes with an API key, a user-agent token or an allowlisted IP, that needs
  the small code change listed under the board (not built yet: the details depend on what each company sends).
- Keep the letters in `data/permissions/` (git-ignored, stays on the computer). Shipped default: all four off.

When a letter arrives: save it, fill that board's entry, set `scan_enabled` to true, then run one supervised read and
check the ads look right:

```bash
cd rentals-desk && python3 -m desk fetch --force
```

## Status, 17 Nov 2026

All six built scanners (Kijiji, Craigslist, LogisQuébec, liv.rent, Appartogo, LesPAC) are on in `config/settings.json`,
on Nesi's statement of 17 Nov that Zaarour holds written permission from each board; the letters are to be uploaded.
Each entry has `letter_pending: true`, so the desk shows "Permission letter pending" for those boards under the review
screen. When a letter arrives, save it in `data/permissions/`, put its path in `letter`, set `letter_pending` to
false, and copy any limits it sets.
The safeguards are unchanged: honest user agent, robots.txt, the spacing and limits above, back-off, and a stop at any
CAPTCHA, bot check or sign-in.

## Landlord phone numbers (off until the permission covers contact details)

The permission requests said no contact details would be collected (point 4), so phone numbers are a separate switch
per board: `permissions.<board>.allow_contact_capture` (default false) plus `contact_basis`, which says where the
board's permission covers contact details. The desk ignores the switch while `contact_basis` is empty, and whenever
the board itself may not be read (`scan_enabled` off, or the permission expired). With it on:

- numbers the poster wrote in the ad's title or text are kept on the lead as the ad is read, with no extra request;
- on boards listed under `contacts.reveal`, right after that board is read, the desk opens ads waiting in review that
  have no number, in a local browser (Playwright: `python3 -m pip install playwright`, then
  `python3 -m playwright install chromium`), and clicks the board's show-number button: at most 10 ads per board per
  read, at least 90 s apart across runs (more if the permission or robots.txt asks), counted against the permission's
  `max_requests_per_hour`, honest user agent, robots.txt obeyed, never signed in, one reveal at a time. If the ad page
  or the click meets a CAPTCHA, a bot check (including an invisible reCAPTCHA or Cloudflare check fired by the click),
  a refusal or a sign-in, it keeps nothing from that page and leaves the reveal alone on that board for 30 days; a
  429/503 pauses it, and the scanner, for the time the board asks. It reads the number only next to the button, spaces
  its first page from the scanner's last request, gives up on an ad after 3 failed tries (at once if it was removed),
  and runs under the same lock as the scanner, so the two never talk to a board at once. Every page it opens, and
  every stop, is in the `reveal_log` table. Reading the board's search pages carries on. Run it supervised with
  `python3 -m desk phones`.

Whatever the switch, phone numbers and emails are blanked out of every scanned ad's stored title and text (they used
to be blanked only on Craigslist and LogisQuébec); the checks still see the original once, and a recheck keeps a
broker match found in it. Ads Stefan captures or pastes himself, and alert emails, keep their text. Ads stored before
17 Nov keep their text as it was until the board lists them again. Numbers stay in `data/desk.sqlite3` on this computer. The review card shows them with a call link and where each came
from ("from the ad text" or "shown on the board").

What each board allows, checked on one live ad page per board on 17 Nov:

| Board | Number printed in the ad | Show-number button | Reveal by the desk |
|---|---|---|---|
| Kijiji | Often (title or description) | "Reveal phone number" on ad pages | Listed in `contacts.reveal`. Not tried yet: if Kijiji puts a sign-in or a bot check behind it, the desk stops on its first try. |
| LogisQuébec | Sometimes | Yes, but the click runs a reCAPTCHA before the number is sent | No: that is a bot check. Only with LogisQuébec's own access (a key or an allowlist). |
| Craigslist | Rarely: Craigslist hides numbers behind "show contact info" | Goes through its reply page, which has a CAPTCHA | No. |
| LesPAC | Sometimes | None seen; contact is a form with a reCAPTCHA | Text only. |
| Appartogo | Rarely (managed buildings) | None | Text only. |
| liv.rent | No | No: contact is in-app messaging behind a sign-in | No. |

## What every permission request should ask for in writing

Send the same five points to each company. Their answer needs to cover each one, in writing (an email from someone
at the company who can speak for it is enough to start; keep it).

1. **Automated access.** Zaarour's desk may read your public rental search pages automatically (name the pages, below),
   notwithstanding the clause in your terms that forbids automated collection.
2. **Identity.** It identifies as `ZaarourRentalsDesk/1.1 (+<rental page URL>; <Stefan's email>)` (the real values
   from `settings.json`). Ask whether they want a different user-agent token, a key, or a fixed IP address to allowlist.
3. **Rate.** The ceiling below per board (requests per hour and per day, seconds between requests). Ask them to state
   their own if lower.
4. **Data use.** The desk stores each ad's title, price, location, bedrooms, the poster's board id and the first
   photo's link, to show Stefan new owner ads. Stefan then contacts the advertiser **by hand through the board's own
   reply form** to offer Zaarour's rental brokerage services. Nothing is resold or republished, and no contact details
   are collected. Ask explicitly that contacting advertisers this way is allowed: several boards forbid commercial
   solicitation of their users separately from scraping.
5. **Retention and term.** How long ads may be kept (we propose 12 months, then delete), and whether the permission
   has an end date or can be withdrawn by email.

## Per board

| Board | Scanner today | Pages and rate to ask for | Code still needed | Blockers and odds |
|---|---|---|---|---|
| **Kijiji** | Built (`desk/sources/kijiji.py`): location search pages, newest first, stops when caught up. 582 ads on file from before it was switched off (15 Oct). | `/b-apartments-condos/<location>/c37l<id>` and its `page-N` pages for 3 locations (Montreal island, Laval/North Shore, Longueuil/South Shore). Every 3 h, at most ~30 pages a run, 6 s apart: about 240 requests a day, 30 an hour. | None to start. After the first supervised read, refresh the test fixture if the page data has changed. If they issue a key or allowlist an IP: a per-board header or a fixed-IP host (see Where it runs). | Biggest source. Terms forbid automated collection without written permission, so the letter must name that clause. Message limit and commercial-contact rules (point 4) matter most here. |
| **Craigslist** | Built (`desk/sources/craigslist.py`): one owners-only search page. 363 ads on file from before. RSS answers 403. | `montreal.craigslist.org/search/apa?purveyor=owner&sort=date`, one page every 3 h: 8 requests a day. | None to start. | Terms forbid any software except browsers and email clients, and Craigslist enforces it in court. Low odds of a yes. Until then: its saved-search email alerts. |
| **LogisQuébec** | Built (`desk/sources/logisquebec.py`): 5 region pages, newest first. It ran automatically 15 to 19 Oct (57 new ads) and is now off until a letter arrives. | `/a-louer/<region>` and `/<region>/<n>` for ile-de-montreal, laval, monteregie, laurentides, lanaudiere, up to 3 pages each, every 6 h: at most 60 requests a day. | None to start. | Terms not found online. Likely the easiest yes: ask first. |
| **liv.rent** | Built (`desk/sources/livrent.py`): 4 city pages. Ran 4 times in October, now off. | `/rental-listings/city/<city>` for montreal, laval, longueuil, brossard, once a day: 4 requests a day. | None to start. | Terms not found online. Mostly managed buildings, so lower value; quick ask. |
| **Facebook Marketplace and groups** | None. | n/a | A new reader only if Meta grants an official API, which it does not offer for reading listings (Marketplace partner APIs are for posting; the Groups API is retired). | Meta's terms forbid automated collection and pages sit behind a login. Don't spend the week here: the capture button stays the route. |
| **Rentals.ca** | None. | Ask for a listings feed or API for Greater Montreal owner rentals, or allowlisting of the desk's user agent past their bot challenge. | New reader once the format is known (about a day). Key or allowlisted-IP support as it applies. | Every page sits behind a bot challenge; robots.txt disallows its JSON/XML feeds. Without their allowlisting or a feed, nothing can be built. |
| **PadMapper / Zumper** | None. One company: one request covers both. | Same ask: a feed or API, or allowlisting. robots.txt disallows `/api` and `/map` and sets Crawl-delay 5. | New reader (about a day) once access is defined. | Bot checks on pages. Their saved-search emails work in the alert inbox meanwhile. |
| **Appartogo** | Built (`desk/sources/appartogo.py`): region pages `/fr/<appartement-louer or condo-louer>/canada/quebec/<region>?page=N` for ile-de-montreal, laval, monteregie, newest first, stops when caught up. Never touches the paths robots.txt disallows. | Those region pages, up to 3 each, every 6 h, one page every 30 s: at most 18 pages a run. | None to start. | Mostly large managed buildings, so fewer owner leads. |
| **LesPAC** | Built (`desk/sources/lespac.py`): the rentals page carries its results as data; the desk asks for newest first with LesPAC's own `searchSortOrder` cookie. Newest first covers all of Quebec (30 to 40 ads a day); the area check sets aside the rest. Marks owner vs company ads. | `/montreal/immobilier-location-logements_b457g17567k1R2.jsa`, page 1 only, every 3 h: 8 requests a day. | Pages 2+ come only from LesPAC's search API (a POST); not needed at this volume. | If the page stops carrying `searchResponse`, the read fails loudly instead of returning nothing. |
| **Louer.ca** | None. Checked 14 Nov: city and ad pages are an empty app shell, and ads come only from `louer.ca/graphql`, which first issues a sign-in token from a key in the page. Its listing query also returns landlords' names, phones and emails, which the desk must not collect. | Ask Louer.ca (Rentals.ca) for an API key or feed issued to the desk, with the fields to leave out. | New reader for their API (about a day) once they issue access. | The desk does not take the site's own browser token. Capture button meanwhile. |
| **Centris** | Not a scanner. The broker cross-check reads a Matrix export (`data/centris_active_rentals.csv`, then `python3 -m desk recheck`). | n/a | None. | Needs Nesi's Matrix account through Ali, and only its own export, used under the board's rules. |

Realistic for this week: Kijiji, LogisQuébec, liv.rent, Appartogo, LesPAC and (if they say yes) Craigslist can go live the day
their letter arrives, since their scanners are built. The other four need a reader written after the company says how it will
give access, so they follow the week after their answer.

## Schedule

Every scanner runs at **2:30 am and 12:00 every day** (Mac local time, Eastern), whether or not the desk is open.
Each run is `python3 -m desk fetch`, so the cache still decides: a board whose last good read is inside its freshness
window (Kijiji and Craigslist 3 h, LogisQuébec 6 h, liv.rent 24 h, alert emails 30 min) is reused, not read again.
Any new scanner listed in `sources` with its permission on is picked up by the same job.

- **Install, once:** double-click `launcher/schedule.command` (it adds `~/Library/LaunchAgents/ca.zaarour.desk.scan.plist`).
  Remove it with `launcher/schedule.command remove`. Log: `data/logs/scan.log`.
- **Wake for the 2:30 run:** a sleeping Mac runs a missed job only when it next wakes. To have it awake on time,
  plugged in, once: `sudo pmset repeat wakeorpoweron MTWRFSU 02:25:00` (check with `pmset -g sched`; undo with
  `sudo pmset repeat cancel`). The Mac must stay logged in, and the lid open or on power, for the job to run.

## Other launch gaps

| Gap | State today | What to do |
|---|---|---|
| **Contact details** | `config/settings.json` does not exist, so the desk uses the placeholders (514-000-0000, `stefan@zaarour.ca`, `zaarour.ca/location`). Those go into the user agent every board sees and into Stefan's messages. | Copy `settings.example.json` to `settings.json` and fill `agent` and `office_email` **before** sending the permission requests, so each letter can name the real user agent. |
| **Merge state** | `rental-desk-scan` and `rental-desk-ui` are fully merged into `rental-desk-v1`. `rental-desk-v1` is not merged into `main`. The repo has **no remote**: the code exists only on this Mac. | Add a private GitHub remote and push, then merge `rental-desk-v1` into `main`. The `Zaarour-scan` and `Zaarour-ui` worktrees can be removed after that. |
| **Where it runs** | Only on Nesi's Mac. Reads while the desk is open (`morning.command`, then a check every 60 min), plus the nightly schedule once installed (see Schedule). | Decide: Stefan's computer, or a small always-on server. A server is required if any board allowlists an IP, and lets boards be read on schedule even when nobody has the desk open. |
| **Alert inbox** | Not set up: no `data/inbox/`, `alerts.imap` blank. It needs no permission. | Set it up this week anyway (README, Email alerts): saved searches with email alerts on Kijiji, Craigslist, Rentals.ca, PadMapper, LesPAC, LogisQuébec. It covers every board until its scanner is approved. |
| **Lease photo scan key** | `anthropic` package not installed, no API key. Unrelated to board permissions. | `python3 -m pip install anthropic`, then `anthropic_api_key` in `settings.json`. |
| **Tests** | 44 unit tests pass (`python3 -m unittest discover -s tests`), including the permission switch, its limits and the polite fetcher. Parsers are tested against saved pages only; Kijiji and Craigslist have not been read live since mid-October. | After each board's first supervised read, compare a few ads against the site and refresh that board's fixture if the page changed. |
| **Ads already on file** | 582 Kijiji, 363 Craigslist, 209 LogisQuébec and 22 liv.rent ads were collected before this permission process. | If a letter sets retention or data-use limits, apply them to these too (ask the company whether earlier ads are covered). |
