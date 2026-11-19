# The desk online (Cloudflare Worker + D1)

The review screen, the lease tracker, the TAL calculator, renewals, the Scorecard, My details, the lease photo scan and
custom messages run on Cloudflare at **rentals.zaarour.ca**, with the data in a D1 database. The six board scanners stay
on the office Mac: after each read it sends the new ads up, and it brings the team's decisions back for the learnings
and the broker list. Cloudflare Access still guards the address (the "Rentals desk" Access app, the five zaarour.ca
addresses), and the desk now knows who is signed in: each person keeps their own contact details on **My details**,
and every decision records who made it.

Nothing is sent to landlords from here. The desk opens the ad with the message ready to fill; the person sends it with
the board's own Send button, as before.

```
 browsers ── Cloudflare Access ── Worker (cloud/src) ── D1 "rentals-desk"
                                     │   ▲
              new ads, scan status   │   │  decisions, drafts, leases, broker list
                                     ▼   │
                         office Mac: python3 -m desk cloud agent
                         (the six scanners, phone search, learnings)
```

| Where | What |
|---|---|
| `cloud/src/` | The Worker: a JavaScript port of `desk/web.py` and the modules it uses. The review screen's files (`desk/ui/`) are the same ones the Mac desk uses. |
| `cloud/migrations/` | The D1 tables: the Mac's tables plus who decided (`decided_by`, `by_user`) and sync stamps. |
| `desk/cloud.py` | The Mac's side: push after each read, pull the decisions, the one-time move, the background agent. |
| `launcher/agent.command` | Installs the Mac's background job (`ca.zaarour.desk.agent`). |

Who owns what: the ad's facts (title, text, price, area, flags, phone) come from the Mac; the decisions (status,
message, outcome, notes) and the leases belong to the online desk. A push from the Mac never overwrites a decision.
Settings stay in `config/settings.json` on the Mac and are copied up on every push **without** API keys, passwords or
tokens. `config/areas.json`, `approaches.json`, `tal_rates.json` and `known_brokers.csv` are copied up too, so editing
them on the Mac still works. The Claude API key lives in a Worker secret.

## Deploy, once (Nesi)

These steps change DNS and the live address, so they are yours to run. Pick a quiet moment: the desk is unreachable
for a few minutes between steps 7 and 8. Run the commands from the `cloud` folder.

1. **Plan.** In the Cloudflare dashboard, Workers & Pages, Plans: **Workers Paid** ($5 a month). The free plan's
   10 ms CPU limit is too tight for the review list.

2. **Install and sign in to Cloudflare** (opens the browser):

   ```bash
   cd ~/Documents/GitHub/zaarour-rental-desk/cloud
   ```
   ```bash
   npm install
   ```
   ```bash
   npx wrangler login
   ```

3. **Create the database**, then paste the `database_id` it prints into `wrangler.toml` (replacing
   `REPLACE_WITH_THE_ID_FROM_wrangler_d1_create`):

   ```bash
   npx wrangler d1 create rentals-desk
   ```
   ```bash
   npm run db:remote
   ```

4. **Access details.** In Zero Trust: Settings, Custom Pages gives the team domain (`<team>.cloudflareaccess.com`);
   Access, Applications, **Rentals desk**, Overview gives the **Application Audience (AUD) tag**. Put both in
   `wrangler.toml` (`ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`). The Worker refuses every request until both are set.

5. **Secrets.** The Claude API key (the same one as `anthropic_api_key` in `config/settings.json`), and a new random
   token the Mac proves itself with:

   ```bash
   npx wrangler secret put ANTHROPIC_API_KEY
   ```
   ```bash
   openssl rand -hex 32
   ```
   ```bash
   npx wrangler secret put AGENT_TOKEN
   ```

   Paste the `openssl` output when `AGENT_TOKEN` asks, and keep it for step 9.

6. **A service token for the Mac.** Zero Trust, Access, Service Auth, Service Tokens, **Create Service Token**, name
   it `Office Mac desk agent`, and copy the Client ID and Client Secret (the secret is shown once). Then Access,
   Applications, **Rentals desk**, Policies, **Add a policy**: name `Office Mac`, Action **Service Auth**, Include
   **Service Token** `Office Mac desk agent`. Keep the existing email policy as it is.

7. **Free the address.** `rentals.zaarour.ca` points at the tunnel today. In the dashboard, zaarour.ca, DNS, Records,
   delete the `rentals` CNAME (it points at `9db8be0e-….cfargotunnel.com`). The Access app stays: it protects the
   hostname, whatever serves it.

8. **Deploy.** This builds the files, uploads the Worker and attaches it to `rentals.zaarour.ca`:

   ```bash
   npm run deploy
   ```

   Open https://rentals.zaarour.ca: after the Access sign-in, the desk opens empty, with your email at the top right.
   The deploy takes the message templates in `templates/` as they are on disk, Stefan's unsaved edits included (the
   same wording the Mac desk shows today); after that, wording is edited on My details online.

9. **Tell the Mac where the desk is.** In `config/settings.json`, fill in the `cloud` block:

   ```json
   "cloud": {"url": "https://rentals.zaarour.ca", "access_client_id": "<step 6 Client ID>",
             "access_client_secret": "<step 6 Client Secret>", "token": "<step 5 token>"}
   ```

10. **Move the Mac's desk up, once.** Stop using the Mac desk first (close its window). From the repository folder:

    ```bash
    cd ~/Documents/GitHub/zaarour-rental-desk && python3 -m desk cloud migrate
    ```

    It copies every ad with its decision, the events, drafts, leases, the broker list, the scan log, the style
    samples and the suggested wording, and prints the counts. A second run refuses (so it can't overwrite what people
    decided online); `--force` replaces the online desk with the Mac's copy.

11. **Start the Mac's background job** (it also replaces the 2:30/12:00 schedule): double-click
    `launcher/agent.command`. Its log is `data/logs/agent.log`. On the desk, the line under the tabs shows the boards'
    last reads, and **Check boards** reaches the Mac within a minute.

12. **Stop the tunnel** (the Mac no longer serves the desk). Keep `~/.cloudflared/` for a rollback.

    ```bash
    launchctl bootout gui/$(id -u)/ca.zaarour.desk.tunnel
    ```
    ```bash
    mv ~/Library/LaunchAgents/ca.zaarour.desk.tunnel.plist ~/.cloudflared/
    ```

13. **Each person, once:** open rentals.zaarour.ca, fill in **My details** (until they do, messages carry the
    shared details from `settings.json`, which are Stefan's), and drag the two buttons on **Browser buttons** onto the
    bookmarks bar again (the old ones point at the Mac). The Chrome extension needs no change; whoever doesn't have it
    downloads it from the same page.

After this, `morning.command` and `python3 -m desk serve` on the Mac open the online desk. `python3 -m desk serve
--local` still runs the Mac's own copy, with a warning that decisions made there stay on the Mac.

## Every day

Nothing to do: the agent reads stale boards every `scan.auto_refresh_minutes` and on **Check boards**, pushes the new
ads, and pulls the decisions back every five minutes. The Mac has to be on and awake. Useful commands:

```bash
python3 -m desk cloud status
```
```bash
python3 -m desk cloud sync
```

`python3 -m desk import-leases` sends leases to the online tracker once the `cloud` block is filled in.

## Rolling back

Remove the Worker's domain (dashboard, Workers & Pages, zaarour-rentals-desk, Settings, Domains & Routes), put the
tunnel back (`mv ~/.cloudflared/ca.zaarour.desk.tunnel.plist ~/Library/LaunchAgents/`, then
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ca.zaarour.desk.tunnel.plist`, then
`cloudflared tunnel route dns zaarour-desk rentals.zaarour.ca`), empty the `cloud` block in `settings.json` and
double-click `launcher/agent.command remove` from a terminal (`bash launcher/agent.command remove`). The Mac's copy has
every decision up to its last pull.

## On this computer

```bash
cd ~/Documents/GitHub/zaarour-rental-desk/cloud && cp .dev.vars.example .dev.vars && npm install && npm run db:local
```
```bash
npm run dev
```

The desk runs at http://localhost:8790 with a local database; `DEV_USER_EMAIL` in `.dev.vars` stands in for the
Access sign-in (only on localhost). Tests: `npm test` here, `python3 -m unittest discover -s tests` for the Mac side.

## Notes

- **Lease photos** go to the Claude API to be read and are not kept, unless an R2 bucket is bound as `SCANS`
  (commented out in `wrangler.toml`), in which case a private copy of each scan is kept there.
- **Custom messages** need `drafts.provider` set to `"claude"` in `settings.json`: LM Studio runs only on the Mac.
- **Recheck** (after editing brokers or areas) runs online on the undecided ads; the Centris export and the numbers the
  phone search tied to brokers come from the Mac with each push.
- Old ads are set aside online after each push and once a day (the cron in `wrangler.toml`), with the Mac's
  `expire_after_days`.
