Local data, not committed: `desk.sqlite3` (leads and rentals).

Optional: drop a Matrix/Centris export of **active rental listings** here as
`centris_active_rentals.csv` (any CSV with an address column), then run
`python3 -m desk recheck`. Ads whose address matches are moved to "Likely broker"
as "already listed on Centris".
