# How each board reaches the desk

Checked on 2026-10-15. The desk never gets around a bot check, CAPTCHA, Cloudflare-style challenge or login wall,
and never disguises itself: it identifies as `ZaarourRentalsDesk/1.1` with the agency's contact details, reads each
host's robots.txt before anything else, and leaves a host alone for a cool-off when it refuses or rate-limits.

There are three legitimate routes in:

- **Read by the desk**: public search pages its robots.txt allows, a few seconds apart, only when the last read is stale.
- **Email alerts**: Stefan saves a search on the board and turns its email alerts on. The desk reads those emails
  (from `data/inbox/` or one mailbox folder, read-only) and turns each ad link into a card. See README, Email alerts.
- **Capture button**: a bookmark Stefan clicks on an ad he is looking at in his own browser. It fills the
  add-by-hand form from that one page. Install it from **Capture button** at the top of the desk.

| Board | robots.txt (checked) | Terms of use | Route |
|---|---|---|---|
| Kijiji | Search pages allowed, but `sort=`, `address=`, `ll=` and `radius=` are disallowed. **The desk's old searches used all four**; it now reads plain location pages (newest first already). | Forbid automated collection without Kijiji's written permission ([secondary source](https://decisions.ct-tc.gc.ca/ct-tc/cdo/en/463028/1/document.do)). | **Email alerts** from saved searches + capture button. Scanner built, off until Kijiji's written permission is on file ([SCANNER-LAUNCH.md](SCANNER-LAUNCH.md)). |
| Craigslist | Search pages allowed. RSS (`format=rss`) answers 403 "blocked". | Forbid scripts, scrapers and any software except browsers and email clients ([terms](https://www.craigslist.org/about/terms.of.use/en)); enforced in court. | **Email alerts** from a saved search (free account) + capture button. Scanning off by default. |
| LogisQuébec | Region pages (`/a-louer/...`) allowed. | Not found online: **to confirm** in the site footer, or ask them. | Scanner built, off until written permission is on file; then every 6 h at most. Their own email alerts also work with the alert inbox. |
| liv.rent | City pages allowed; publishes sitemaps. | Not found online: **to confirm**. | Scanner built, off until written permission is on file; then once a day. |
| Rentals.ca | Most pages allowed; `*-feed.json/xml` disallowed. Pages sit behind a bot challenge. | Not checked. | Email alerts from a saved search (to confirm the board offers them) + capture button. |
| PadMapper / Zumper | `/api` and `/map` disallowed, Crawl-delay 5. Bot checks. | Not checked. | Their saved-search email alerts + capture button. |
| Facebook Marketplace and groups | n/a (login wall). | Automated collection forbidden. No public API to read listings: Meta's Marketplace partner APIs are for posting inventory, and the Groups API is retired. | **Capture button only.** |
| Appartogo | Most pages allowed; `/recherche`, `/en/search`, `/search/markers` and `/listing/card` disallowed; AI crawlers named and blocked (the desk is not one of them). Region pages carry every card in the HTML. | Not checked. | **Scanner built** (`desk/sources/appartogo.py`): region pages only, off until permission is on file. Capture button meanwhile. |
| LesPAC | Most pages allowed. The list is drawn in the browser, but the page carries its data (`var searchResponse`). | Not checked. | **Scanner built** (`desk/sources/lespac.py`): page 1 of rentals, newest first, off until permission is on file. Alert emails + capture button meanwhile. |
| Louer.ca | All allowed; publishes a sitemap. Run by Rentals.ca. Pages are an empty app shell: ads come only from its GraphQL API, which first hands out a sign-in token. | Not checked. | Capture button. A scanner needs Louer.ca to issue the desk its own API key or feed (see SCANNER-LAUNCH.md). |

"To confirm" items need a person to read the board's terms page (or ask the board); the desk keeps working without them.

## Sending

What each board asks before a message goes, as of 2026-11-14. Not yet checked by opening each board's message box
signed in: Stefan should confirm each line the first time he sends there ("to confirm" marks the least certain). The desk does not submit any board's form by
itself: every route below needs Stefan signed in as himself, or a CAPTCHA a person answers, and the desk never gets
around either. The **Fill from desk** bookmark puts the approved message in the box; Stefan clicks Send.

| Board | Where the message goes | What sending needs |
|---|---|---|
| Kijiji | Send message box on the ad, to the poster's Kijiji inbox | Kijiji account, signed in |
| Craigslist | Reply gives a relay email address; the message goes from Stefan's mailbox (Fill works in Gmail/Outlook web compose) | A CAPTCHA before the address shows; terms forbid scripts |
| LogisQuébec | Contact form on the ad (name, email, phone, message) | To confirm: possible CAPTCHA |
| liv.rent | In-app chat with the landlord | liv.rent account, signed in |
| Appartogo | Contact form on the ad | To confirm |
| LesPAC | Contacter l'annonceur | LesPAC account, signed in (to confirm) |
| Facebook Marketplace / groups | Messenger | Facebook account, signed in |
| Rentals.ca, PadMapper/Zumper, Louer.ca, DuProprio | Contact form on the ad | Bot checks (Rentals.ca, PadMapper); others to confirm |

A board could be sent to directly only through a messaging API or partner route the board itself offers, with its
written permission, like scanning. None offers one today. The per-board notes the desk shows under each message are
in `desk/send.py` (ROUTES): update them there when a board is checked.
