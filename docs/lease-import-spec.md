# Lease import spec (for the RAG process)

How leases extracted elsewhere (Nexone transaction reports, Centris/Matrix exports, the sales tracker, Prospects, lease PDFs) drop into the **lease renewal tracker** in `rentals-desk`. Spec version **1**.

## Delivery

| | |
|---|---|
| Format | **JSON Lines** (`.jsonl`), UTF-8, one lease per line. CSV with the same column names also works, but JSONL is preferred (no quoting problems with French text and commas). |
| Drop location | `rentals-desk/data/import/inbox/` on the desk's Mac. Any file name ending in `.jsonl` or `.csv`. |
| When it's read | Every morning run (`morning.command`, or the boot page's Boot button), or right away with `python3 -m desk import-leases`. |
| Alternative | While the desk is running: `POST http://127.0.0.1:8765/api/leases/import` with `{"records": [ ... ]}` (same record shape). It returns the import report as JSON. |
| After import | The file moves to `data/import/done/`. Rejected records go to `data/import/rejected/<time>-<file>.jsonl`, each as `{"record": ..., "errors": [...]}`, so you can fix and re-send them. |
| Re-sending | Safe. A record with an `external_id` that's already in the tracker updates it in place. Send full records, not partial patches: fields you leave out are blanked. |

## Record

One flat JSON object per lease. **Only send what the source actually says.** Leave a field out (or `null`) when the source doesn't have it; never fill a gap with a guess. Guessed values belong in `evidence` and lower `confidence`.

### Identity and provenance

| Field | Type | Req. | Notes |
|---|---|---|---|
| `spec_version` | integer | no | `1`. |
| `external_id` | string | **yes** | Stable ID for this lease in your system, the same on every run, e.g. `nexone:TR-2025-0412` or `sales_tracker:2024:row-88`. The dedupe key. One lease term = one ID; a new lease with a new tenant gets a new ID. |
| `source` | string | **yes** | One of `nexone`, `centris`, `sales_tracker`, `prospects`, `lease_pdf`, `email`, `manual`, `other`. |
| `source_ref` | string | no | Where a person can check it: document number, file and page, spreadsheet row. Shown on the tracker. |
| `confidence` | number 0–1 | no | Your confidence in the **lease dates** for this record. Default `1`. Below `0.9` the lease shows as **Check data** and stays out of "Call now" until a person confirms it. (The threshold is `import_min_confidence` in settings.) |
| `lease_end_basis` | string | no | `stated` (the end date is written in the source, the default), `computed_from_term` (start date plus a stated term), or `inferred` (guessed, e.g. from a possession date alone). `inferred` always shows as **Check data**. |
| `tenancy_status` | string | no | `active`, `ended` (the tenant left and the lease wasn't renewed) or `unknown` (the default). The tracker assumes a lease renewed itself for the same length after its end date (Civil Code art. 1941) unless this is `ended`. |
| `evidence` | object | no | `{"field": "short quote from the source"}` for the values that matter, especially `lease_start`, `lease_end` and `rent`. Shown to Stefan on the renewal page. |

### Lease fields

Same names as the desk's New lease form. Required for the tracker: **`address`, `city`, `landlord_name`, `lease_start`, `lease_end`**. Everything else is optional.

| Field | Type | Notes |
|---|---|---|
| `address` | string | Civic number, street, unit: `4005 boul. des Sources, app. 205`. The civic number matters: it's how duplicates of hand-entered leases are caught. |
| `city` | string | City or borough: `Dollard-des-Ormeaux`, `Saint-Laurent`, `Laval`. |
| `postal_code` | string | `H9B 2A6`. |
| `unit_size` | string | `4½`, `3 1/2`. |
| `dwelling_type` | enum | `Apartment`, `Condo`, `House`, `Duplex/triplex unit`, `Room`. |
| `furnished` | boolean | `true` / `false`. |
| `parking`, `storage` | string | Free text. |
| `centris_no` | string | Centris/MLS number. |
| `landlord_name` | string | The lessor as on the lease. Several owners: `"Jean Exemple, Anne Exemple"`. A company: its legal name. |
| `landlord_phone` | string | Any readable format, e.g. `514-555-0101`. |
| `landlord_email` | string | Where the renewal message goes. Leave it out rather than guess. |
| `landlord_address` | string | Mailing address. |
| `tenant_names` | string | As on the lease, comma-separated. |
| `tenant_phone`, `tenant_email` | string | |
| `tenant_current_address` | string | Only for a new lease; usually leave out. |
| `lease_start`, `lease_end` | date | **`YYYY-MM-DD`** only (`2025-07-01`). Not `2025/07/01`, not `1er juillet`. `lease_end` must be after `lease_start`. Give the term on the lease itself, even if it has since renewed; the desk rolls it forward. |
| `rent` | money | Monthly rent in dollars. A number (`1850` or `1850.5`) is best; `"1 850,00 $"` is accepted. |
| `services_cost` | money | Monthly charges on top of rent. |
| `first_payment_date` | date | `YYYY-MM-DD`. |
| `payment_method` | enum | `Cheque`, `E-transfer`, `Post-dated cheques`, `Other`. |
| `heating`, `hot_water`, `electricity` | enum | Who pays: `Tenant` or `Landlord`. |
| `appliances` | string | |
| `pets` | enum | `Not allowed`, `Allowed`, `Allowed with conditions`. |
| `smoking` | enum | `Not allowed`, `Allowed`. |
| `lowest_rent_12m` | money | TAL lease section G. |
| `new_building`, `building_rules` | boolean | TAL sections F and building rules. |
| `commission` | money | Our commission on the lease. |
| `double_ended` | boolean | We also found the tenant. |
| `co_broker` | string | Other agency, if any. |
| `photographer` | boolean | |
| `lead_source` | enum | `Kijiji`, `Facebook Marketplace`, `Ads`, `Repeat client`, `Referral`, `Centris`, `Other`. |
| `notes_for_office` | string | Free text. |

Enum values are matched without regard to case. A value outside the list, or an email that isn't an email, is dropped with a warning; the rest of the record still imports.

## What the desk does with it

- **Rejected** (whole record): a required field missing, a date not in `YYYY-MM-DD`, `lease_end` not after `lease_start`, an unknown `source`, `lease_end_basis` or `tenancy_status`, `confidence` outside 0–1, or a line that isn't valid JSON.
- **Skipped**: the same unit and start date was already entered by hand with New lease (the hand-entered one wins), or a person already confirmed this `external_id` on the renewal page (later imports don't overwrite a checked lease).
- **Added or updated** otherwise. The renewal call date is set 6 months before the end of the current term, the lease appears in Renewals with the landlord message, and `confidence`, `source_ref` and `evidence` are shown on its renewal page with a **The dates are right** button when it needs checking.

## Sample

Two records: a Nexone lease with a stated end date, and a sales-tracker row where only the possession date was known (so the end date is computed and confidence is low). Also in [examples/leases-sample.jsonl](examples/leases-sample.jsonl), which the desk's tests import.

```json
{"spec_version": 1, "external_id": "nexone:TR-2025-0412", "source": "nexone", "source_ref": "Transaction report TR-2025-0412, page 3", "confidence": 0.97, "lease_end_basis": "stated", "evidence": {"lease_end": "Bail du 1er juillet 2025 au 30 juin 2026", "rent": "Loyer mensuel : 1 850 $"}, "address": "4005 boul. des Sources, app. 205", "city": "Dollard-des-Ormeaux", "postal_code": "H9B 2A6", "unit_size": "4½", "dwelling_type": "Condo", "landlord_name": "Jean Exemple", "landlord_phone": "514-555-0101", "landlord_email": "jean.exemple@example.com", "tenant_names": "Marie Exemple", "tenant_email": "marie.exemple@example.com", "lease_start": "2025-07-01", "lease_end": "2026-06-30", "rent": 1850, "heating": "Landlord", "hot_water": "Landlord", "electricity": "Tenant", "furnished": false, "parking": "1 indoor", "centris_no": "12345678", "commission": 1850, "lead_source": "Repeat client"}
{"spec_version": 1, "external_id": "sales_tracker:2024:row-88", "source": "sales_tracker", "source_ref": "Ventes 2024.xlsx, Locations, row 88", "confidence": 0.6, "lease_end_basis": "computed_from_term", "evidence": {"lease_start": "Possession 2024-08-01", "lease_end": "No end date in source; 12-month term assumed"}, "address": "12 rue Exemple", "city": "Pierrefonds", "landlord_name": "Société Exemple inc.", "lease_start": "2024-08-01", "lease_end": "2025-07-31", "rent": "1 495,00 $"}
```

## Checking a file before sending

```bash
cd ~/Documents/GitHub/Zaarour/rentals-desk
python3 -m desk import-leases --dry-run path/to/file.jsonl
```

It prints added / updated / rejected / skipped counts, every warning and each rejection reason, saves nothing, and exits with an error code when any record was rejected. Drop `--dry-run` to import for real.
