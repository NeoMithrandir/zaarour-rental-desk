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
| Kijiji | Search pages allowed, but `sort=`, `address=`, `ll=` and `radius=` are disallowed. **The desk's old searches used all four**; it now reads plain location pages (newest first already). | Forbid automated collection without Kijiji's written permission ([secondary source](https://decisions.ct-tc.gc.ca/ct-tc/cdo/en/463028/1/document.do)). | **Email alerts** from saved searches + capture button. Scanning is off by default; adding `"kijiji"` to `sources` turns it back on, against the terms. |
| Craigslist | Search pages allowed. RSS (`format=rss`) answers 403 "blocked". | Forbid scripts, scrapers and any software except browsers and email clients ([terms](https://www.craigslist.org/about/terms.of.use/en)); enforced in court. | **Email alerts** from a saved search (free account) + capture button. Scanning off by default. |
| LogisQuébec | Region pages (`/a-louer/...`) allowed. | Not found online: **to confirm** in the site footer, or ask them. | Read by the desk (every 6 h at most). Their own email alerts also work with the alert inbox. |
| liv.rent | City pages allowed; publishes sitemaps. | Not found online: **to confirm**. | Read by the desk (once a day: managed buildings change slowly). |
| Rentals.ca | Most pages allowed; `*-feed.json/xml` disallowed. Pages sit behind a bot challenge. | Not checked. | Email alerts from a saved search (to confirm the board offers them) + capture button. |
| PadMapper / Zumper | `/api` and `/map` disallowed, Crawl-delay 5. Bot checks. | Not checked. | Their saved-search email alerts + capture button. |
| Facebook Marketplace and groups | n/a (login wall). | Automated collection forbidden. No public API to read listings: Meta's Marketplace partner APIs are for posting inventory, and the Groups API is retired. | **Capture button only.** |
| Appartogo | Most pages allowed; AI crawlers named and blocked. | Not checked. | Capture button (and its alerts, if offered). |
| LesPAC | Most pages allowed; ads rendered in the browser. | Not checked. | LesPAC alert emails + capture button. |
| Louer.ca | All allowed; publishes a sitemap. Ads rendered in the browser. | Not checked. | Capture button. Its sitemap could become a read-by-the-desk route if its terms allow it: **to confirm** first. |

"To confirm" items need a person to read the board's terms page (or ask the board); the desk keeps working without them.
