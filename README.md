# NORD WEST Shopify Title Automation

This repository rewrites plentyONE **Name 1 and Name 2 with the same generated title** for products with positive physical stock in the **NORD WEST** warehouse (`warehouseId=128`). The goal is to avoid copying the NORD WEST source title verbatim into Shopify while keeping every added fact grounded in the product data.

The new German title is built from:

- current Name 1 (`name`) as the NORD WEST source title
- Artikeltext (`description`)
- Technische Daten (`technicalData`)

The generator removes HTML and marketing noise, prefers structured facts such as material, weight, norm, color and dimensions, avoids unsupported claims, and uses a 120-character internal readability cap. If no safe source-backed differentiation is possible, the item is skipped instead of inventing wording.

## Flow

1. Login to the plentyONE REST API with the dedicated API-only backend user.
2. Read all stock rows from warehouse `128`.
3. Keep rows where `stockPhysical > 0` and group by Artikel-ID.
4. Sort Artikel-IDs ascending and skip IDs already covered by `state/last_processed.json`.
5. Read German Name 1, article text and technical data from the first stocked variation of each article.
6. Build a differentiated Shopify title from those source fields.
7. Write that exact same title to both `name` (**Name 1**) and `name2` (**Name 2**).
8. Read the item text back from plentyONE and verify that Name 1 and Name 2 both equal the generated title before advancing the checkpoint.
9. Save the last handled Artikel-ID. GitHub Actions commits that checkpoint after each live run.

> The checkpoint remains Artikel-ID based. Older IDs are not revisited unless the checkpoint is intentionally reset.

## Required GitHub Actions secrets

In **Settings -> Secrets and variables -> Actions**:

- `PLENTY_BASE_URL`
- `PLENTY_USERNAME`
- `PLENTY_PASSWORD`

Never commit credentials, tokens, or passwords.

## Manual preview

Go to **Actions -> Daily Shopify Name 1 + Name 2 -> Run workflow** and use:

- `dry_run = true`
- `max_items = 20`

Dry-run mode prints the NORD WEST source Name 1 and the proposed title that would be written identically to Name 1 and Name 2, without changing plentyONE or the checkpoint. After reviewing the proposals, run again with `dry_run = false`.

The scheduled run executes daily at **03:30 UTC**.

## State

`state/last_processed.json` stores the last Artikel-ID successfully handled, including intentional skips. API or verification failures do not advance the failing item.

## Local test

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```
