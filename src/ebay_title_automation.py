from __future__ import annotations

import argparse
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

WAREHOUSE_ID = 128
LANG = "de"
EBAY_TITLE_LIMIT = 80
STATE_FILE = Path("state/last_processed.json")

# Terms that add no useful product information to an eBay title.
NOISE_WORDS = {
    "artikel", "beschreibung", "produkt", "produkte", "weitere", "information",
    "informationen", "details", "technische", "daten", "eigenschaften", "merkmale",
    "neu", "top", "angebot", "sale", "original", "qualität", "qualitaet",
}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " | ", value, flags=re.I)
    value = re.sub(r"</(?:p|li|div|tr|h[1-6])>", " | ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[\r\n\t]+", " | ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*\|\s*", " | ", value)
    return value.strip(" |")


def normalize_title_chars(value: str) -> str:
    replacements = {
        "®": "", "™": "", "©": "", "•": " ", "·": " ", "–": "-", "—": "-",
        "„": '"', "“": '"', "”": '"', "’": "'",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    # Keep common dimensions/model punctuation, remove decorative symbols.
    value = re.sub(r"[^\wÄÖÜäöüß0-9%+./,:()'\"xX\- ]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -|,;:")


def token_key(token: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", token.casefold())


def useful_tokens(text: str) -> list[str]:
    out: list[str] = []
    for token in text.split():
        key = token_key(token)
        if not key or key in NOISE_WORDS:
            continue
        out.append(token)
    return out


def candidate_segments(technical_data: str, description: str) -> Iterable[str]:
    for source in (technical_data, description):
        cleaned = clean_text(source)
        if not cleaned:
            continue
        # Split on common bullet/label boundaries while preserving measurements and model strings.
        for segment in re.split(r"\s*\|\s*|\s*[;]\s*", cleaned):
            segment = normalize_title_chars(segment)
            segment = re.sub(r"^[\-:,]+\s*", "", segment)
            if len(segment) < 2:
                continue
            # Long prose is less useful than concise feature phrases.
            words = useful_tokens(segment)
            if not words:
                continue
            if len(words) > 10:
                words = words[:10]
            candidate = " ".join(words)
            if candidate:
                yield candidate


def build_ebay_title(name1: str, description: str = "", technical_data: str = "") -> str:
    base = normalize_title_chars(clean_text(name1))
    if not base:
        return ""

    # Name 1 is authoritative and always comes first.
    if len(base) > EBAY_TITLE_LIMIT:
        clipped = base[:EBAY_TITLE_LIMIT].rstrip()
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        return clipped[:EBAY_TITLE_LIMIT].rstrip()

    title_words = base.split()
    seen = {token_key(t) for t in title_words if token_key(t)}

    for segment in candidate_segments(technical_data, description):
        new_words: list[str] = []
        local_seen: set[str] = set()
        for word in segment.split():
            key = token_key(word)
            if not key or key in seen or key in local_seen:
                continue
            local_seen.add(key)
            new_words.append(word)

        if not new_words:
            continue

        # Add as many novel words as fit, preserving their source order.
        for word in new_words:
            proposal = " ".join(title_words + [word])
            if len(proposal) > EBAY_TITLE_LIMIT:
                break
            title_words.append(word)
            seen.add(token_key(word))

        if len(" ".join(title_words)) >= EBAY_TITLE_LIMIT - 4:
            break

    return " ".join(title_words)[:EBAY_TITLE_LIMIT].rstrip(" -|,;:")


@dataclass(frozen=True)
class StockItem:
    item_id: int
    variation_id: int


class PlentyClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def login(self) -> None:
        response = self.session.post(
            self._url("/rest/login"),
            json={"username": self.username, "password": self.password},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("accessToken") or payload.get("access_token")
        if not token:
            raise RuntimeError("plentyONE login response did not contain an access token")
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response = self.session.request(method, self._url(path), timeout=self.timeout, **kwargs)
        if response.status_code == 401:
            self.login()
            response = self.session.request(method, self._url(path), timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response

    def list_positive_stock(self, warehouse_id: int = WAREHOUSE_ID) -> list[StockItem]:
        page = 1
        by_item: dict[int, int] = {}
        while True:
            response = self._request(
                "GET",
                f"/rest/stockmanagement/warehouses/{warehouse_id}/stock",
                params={"page": page, "itemsPerPage": 250},
            )
            payload = response.json()
            entries = payload.get("entries", [])
            for row in entries:
                try:
                    item_id = int(row["itemId"])
                    variation_id = int(row["variationId"])
                    physical = float(row.get("stockPhysical") or 0)
                except (KeyError, TypeError, ValueError):
                    continue
                if physical > 0:
                    # One article may have several stocked variations. Use a deterministic one
                    # to address the shared item text record.
                    by_item[item_id] = min(variation_id, by_item.get(item_id, variation_id))

            if payload.get("isLastPage") is True:
                break
            last_page = payload.get("lastPageNumber")
            if isinstance(last_page, int) and page >= last_page:
                break
            if not entries:
                break
            page += 1

        return [StockItem(item_id=k, variation_id=v) for k, v in sorted(by_item.items())]

    def get_description(self, item_id: int, variation_id: int, lang: str = LANG) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/rest/items/{item_id}/variations/{variation_id}/descriptions/{lang}",
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected description payload for item {item_id}")
        return payload

    def update_name2(self, item_id: int, variation_id: int, name2: str, lang: str = LANG) -> None:
        # plentyONE requires itemId and lang in the body for the text update route.
        self._request(
            "PUT",
            f"/rest/items/{item_id}/variations/{variation_id}/descriptions/{lang}",
            json={"itemId": item_id, "lang": lang, "name2": name2},
        )


def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {"last_article_id": 0, "updated_at": None}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(last_article_id: int, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_article_id": int(last_article_id),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(dry_run: bool = False, max_items: int = 0) -> int:
    required = ["PLENTY_BASE_URL", "PLENTY_USERNAME", "PLENTY_PASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    state = load_state()
    checkpoint = int(state.get("last_article_id") or 0)

    client = PlentyClient(
        os.environ["PLENTY_BASE_URL"],
        os.environ["PLENTY_USERNAME"],
        os.environ["PLENTY_PASSWORD"],
    )
    client.login()

    pending = [s for s in client.list_positive_stock() if s.item_id > checkpoint]
    if max_items > 0:
        pending = pending[:max_items]

    print(f"Warehouse {WAREHOUSE_ID}: {len(pending)} new article(s) after checkpoint {checkpoint}.")
    processed = 0

    for stock in pending:
        print(f"\nArtikelID {stock.item_id} / VariationID {stock.variation_id}")
        try:
            text = client.get_description(stock.item_id, stock.variation_id)
        except requests.HTTPError as exc:
            # Missing German text should not permanently block all later IDs.
            if exc.response is not None and exc.response.status_code == 404:
                print("SKIP: no German text record")
                if not dry_run:
                    save_state(stock.item_id)
                processed += 1
                continue
            raise

        name1 = str(text.get("name") or "").strip()
        if not name1:
            print("SKIP: Name 1 is empty")
            if not dry_run:
                save_state(stock.item_id)
            processed += 1
            continue

        title = build_ebay_title(
            name1=name1,
            description=str(text.get("description") or ""),
            technical_data=str(text.get("technicalData") or ""),
        )
        if not title:
            print("SKIP: could not build title")
            if not dry_run:
                save_state(stock.item_id)
            processed += 1
            continue

        current = str(text.get("name2") or "").strip()
        print(f"Name 1 : {name1}")
        print(f"Current: {current}")
        print(f"Proposed ({len(title)} chars): {title}")

        if dry_run:
            print("DRY RUN: no update, checkpoint unchanged")
        else:
            if current == title:
                print("UNCHANGED: Name 2 already matches")
            else:
                client.update_name2(stock.item_id, stock.variation_id, title)
                print("UPDATED: Name 2")
            save_state(stock.item_id)

        processed += 1

    return processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate eBay-style plentyONE Name 2 titles for NORD WEST stock")
    parser.add_argument("--dry-run", action="store_true", help="Do not update plentyONE or the checkpoint")
    parser.add_argument("--max-items", type=int, default=0, help="Limit number of articles; 0 means all")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    count = run(dry_run=args.dry_run, max_items=args.max_items)
    print(f"\nHandled {count} article(s).")
