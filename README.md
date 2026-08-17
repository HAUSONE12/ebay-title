# NORD WEST eBay Name 2 Automation

This repository updates plentyONE **Name 2** for products with positive physical stock in the **NORD WEST** warehouse (`warehouseId=128`).

The title is generated from the German item texts:

- Name 1 (`name`)
- Artikeltext (`description`)
- Technische Daten (`technicalData`)

The generator is conservative: it never invents product facts, removes HTML/marketing noise, prioritizes useful product terms, avoids duplicate words, and limits Name 2 to **80 characters** for eBay.

## Flow

1. Login to the plentyONE REST API with the dedicated API-only backend user.
2. Read all stock rows from warehouse `128`.
3. Keep rows where `stockPhysical > 0` and group by Artikel-ID.
4. Sort Artikel-IDs ascending and skip IDs already covered by `state/last_processed.json`.
5. Read German texts from the first stocked variation of each article.
6. Build the eBay-oriented title from Name 1 + technical data + article text.
7. Update only `name2` in the German item text record.
8. Save the last handled Artikel-ID. GitHub Actions commits that checkpoint after each run.

> Note: The checkpoint strategy is intentionally based on the last Artikel-ID, as requested. An older Artikel-ID that is added to NORD WEST only later will not be revisited automatically. Reset the checkpoint manually if such an item must be reprocessed.

## Required GitHub Actions secrets

In **Settings -> Secrets and variables -> Actions**, create:

- `PLENTY_BASE_URL` - your plentyONE system base URL, without `/rest` at the end
- `PLENTY_USERNAME` - the dedicated API backend username
- `PLENTY_PASSWORD` - the password of that API user

Because this repository is public, never commit credentials, tokens, or passwords.

## Safe first test

Go to **Actions -> Daily eBay Name 2 -> Run workflow** and use:

- `dry_run = true`
- `max_items = 20`

Dry-run mode prints the proposed Name 2 values but does **not** write to plentyONE and does **not** advance the checkpoint.

After reviewing the proposals, run again with `dry_run = false`. The scheduled run executes daily at **03:30 UTC**.

## State

`state/last_processed.json` stores the last Artikel-ID successfully handled (including intentional skips for missing source text). API failures do not advance the failing article, so the next run retries it.

## Local test

```bash
python -m pip install -r requirements.txt
pytest -q
```

Dry run against plentyONE:

```bash
export PLENTY_BASE_URL='https://your-plenty-domain.example'
export PLENTY_USERNAME='your-api-user'
export PLENTY_PASSWORD='...'
python -m src.ebay_title_automation --dry-run --max-items 20
```
