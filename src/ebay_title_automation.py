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

DANGLING_WORDS = {
    "mit", "und", "oder", "für", "fuer", "aus", "von", "im", "in", "am", "an",
    "auf", "zu", "zur", "zum", "der", "die", "das", "den", "dem", "des", "ein",
    "eine", "einer", "einem", "einen",
}

DESCRIPTION_BAD_WORDS = {
    "ist", "sind", "war", "wird", "werden", "hat", "haben", "kann", "können", "koennen",
    "bietet", "bieten", "vereint", "ermöglicht", "ermoeglicht", "eignet", "geeignet",
    "verfügt", "verfuegt", "sorgt", "besteht", "zeichnet",
}

MATERIAL_TERMS = {
    "stahl", "edelstahl", "aluminium", "alu", "kunststoff", "polypropylen", "polyacryl",
    "polyester", "leder", "holz", "gummi", "metall", "messing", "kupfer", "zink",
    "schaumstoff", "nylon", "textil", "baumwolle", "glasfaser", "carbon",
}

SKIP_LABELS = {
    "geeignet für", "geeignet fuer", "einsatzbereich", "anwendung", "beschreibung",
    "hinweis", "hinweise", "lieferumfang", "verwendung", "weite",
}

LABEL_PRIORITIES = {
    "gewicht": 100,
    "kopfgewicht": 100,
    "größe": 95,
    "groesse": 95,
    "maße": 95,
    "masse": 95,
    "abmessungen": 95,
    "länge": 95,
    "laenge": 95,
    "breite": 95,
    "höhe": 95,
    "hoehe": 95,
    "durchmesser": 95,
    "material": 90,
    "werkstoff": 90,
    "norm": 88,
    "schutzklasse": 88,
    "sicherheitsklasse": 88,
    "farbe": 80,
    "oberfläche": 75,
    "oberflaeche": 75,
    "ausführung": 70,
    "ausfuehrung": 70,
    "inhalt": 55,
}

KNOWN_LABEL_PATTERN = re.compile(
    r"(?i)(?<!\w)("
    r"Geeignet\s+für|Geeignet\s+fuer|Einsatzbereich|Anwendung|Beschreibung|Hinweise?|"
    r"Lieferumfang|Verwendung|Weite|Material|Werkstoff|Farbe|Norm|Schutzklasse|Sicherheitsklasse|"
    r"Gewicht|Kopfgewicht|Größe|Groesse|Maße|Masse|Abmessungen|Länge|Laenge|Breite|Höhe|Hoehe|"
    r"Durchmesser|Oberfläche|Oberflaeche|Ausführung|Ausfuehrung|Inhalt"
    r")\s*:"
)


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
        "„": '"', "“": '"', "”": '"', "’": "'", "″": '"', "×": "x",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    # Keep common model/dimension punctuation, remove decorative symbols.
    value = re.sub(r"[^\wÄÖÜäöüßØø0-9%+./,:()'\"xX\- ]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r'(?<=\d)\s+"', '"', value)
    value = re.sub(r"\s+([,.:])", r"\1", value)
    return value.strip(" -|,;:")


def token_key(token: str) -> str:
    return re.sub(r"[^a-z0-9äöüßø]", "", token.casefold())


def useful_tokens(text: str) -> list[str]:
    out: list[str] = []
    for token in text.split():
        key = token_key(token)
        if not key or key in NOISE_WORDS:
            continue
        out.append(token)
    return out


def strip_dangling_end(value: str) -> str:
    words = value.split()
    while words and token_key(words[-1]) in DANGLING_WORDS:
        words.pop()
    return " ".join(words).rstrip(" -|,;:")


def compact_name1(name1: str) -> str:
    """Keep Name 1 authoritative, but shorten verbose field labels before clipping."""
    base = normalize_title_chars(clean_text(name1))
    if not base:
        return ""

    replacements: list[tuple[str, str]] = [
        (r"\bRollenbreite\s+(?=\d)", ""),
        (r"\bBreite\s+(?=\d)", ""),
        (r"\bFlorhöhe\s+(?=\d)", "Flor "),
        (r"\bKern-?\s*Ø\s*(?=\d)", "Kern Ø"),
        (r"\bBügellänge\s+(?=\d)", "Bügel "),
        (r"\bBügel-?\s*Ø\s*(?=\d)", "Bügel Ø"),
        (r"\bSchlüsselweiten\s+(?=\d)", "SW "),
        (r"\bAnzahl\s+Zähne\s+(\d+)", r"\1 Zähne"),
    ]
    for pattern, replacement in replacements:
        base = re.sub(pattern, replacement, base, flags=re.I)
    base = re.sub(r"\s+", " ", base).strip()

    if len(base) <= EBAY_TITLE_LIMIT:
        return strip_dangling_end(base)

    # eBay titles are search-oriented; connector words can be dropped when space is tight.
    base = base.replace(",", " ")
    words = [w for w in base.split() if token_key(w) not in DANGLING_WORDS]
    compact = " ".join(words)
    if len(compact) <= EBAY_TITLE_LIMIT:
        return strip_dangling_end(compact)

    selected: list[str] = []
    for word in words:
        proposal = " ".join(selected + [word])
        if len(proposal) > EBAY_TITLE_LIMIT:
            break
        selected.append(word)
    return strip_dangling_end(" ".join(selected))[:EBAY_TITLE_LIMIT].rstrip(" -|,;:")


def normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", normalize_title_chars(label).casefold()).strip()


def clean_candidate_value(value: str) -> str:
    value = normalize_title_chars(value).strip(" -|,;:")
    words = value.split()
    while words and token_key(words[0]) in DANGLING_WORDS:
        words.pop(0)
    while words and token_key(words[-1]) in DANGLING_WORDS:
        words.pop()
    return " ".join(words).rstrip(" .,-|;:")


def source_segments(source: str) -> list[str]:
    cleaned = clean_text(source)
    if not cleaned:
        return []
    # Some plentyONE HTML tables flatten adjacent labels into one line. Insert a boundary
    # before known labels so "Material: ... Norm: ..." becomes two candidates.
    cleaned = KNOWN_LABEL_PATTERN.sub(lambda m: f" | {m.group(1)}:", cleaned)
    return [seg.strip() for seg in re.split(r"\s*\|\s*|\s*;\s*", cleaned) if seg.strip()]


def candidate_segments(technical_data: str, description: str) -> Iterable[tuple[int, int, str]]:
    """Yield (priority, source_order, phrase) while rejecting prose and ambiguous fragments."""
    order = 0
    for source_kind, source_priority, source in (
        ("technical", 10, technical_data),
        ("description", 0, description),
    ):
        for raw_segment in source_segments(source):
            order += 1
            segment = normalize_title_chars(raw_segment)
            if not segment:
                continue

            label = ""
            value = segment
            if ":" in segment:
                maybe_label, maybe_value = segment.split(":", 1)
                label = normalize_label(maybe_label)
                value = maybe_value

            if label in SKIP_LABELS:
                continue
            if label and label not in LABEL_PRIORITIES:
                continue
            if source_kind == "technical" and not label:
                # Unlabelled table values caused naked numbers such as "75 70 293 320".
                continue

            value = clean_candidate_value(value)
            if not value:
                continue

            words = useful_tokens(value)
            if not words:
                continue

            if source_kind == "description" and not label:
                # Only short bullet-like feature phrases, never sentence fragments.
                if len(words) < 2 or len(words) > 4:
                    continue
                if raw_segment.strip().endswith((".", "!", "?")):
                    continue
                if any(token_key(word) in DESCRIPTION_BAD_WORDS for word in words):
                    continue

            # Structured fields can be a little longer, but never paste a sentence into Name 2.
            if label and len(words) > 6:
                continue

            if label == "norm" and not re.search(r"\b(?:DIN|EN|ISO|VDE|S[1-7]|SRC|ESD)\b", value, re.I):
                continue

            if label == "inhalt" and not re.search(r"\d", value):
                continue

            # A naked number is not useful without its unit or context.
            if re.fullmatch(r"\d+(?:[.,]\d+)?", value):
                continue

            if label in {"breite", "höhe", "hoehe", "länge", "laenge", "durchmesser", "maße", "masse", "abmessungen", "größe", "groesse"}:
                if re.fullmatch(r"[\d\s.,xX+/-]+", value):
                    continue

            if label in {"material", "werkstoff"} and len(words) == 1:
                if token_key(words[0]) not in MATERIAL_TERMS:
                    continue

            phrase = " ".join(words)
            priority = source_priority + LABEL_PRIORITIES.get(label, 45)
            yield priority, order, phrase


def build_ebay_title(name1: str, description: str = "", technical_data: str = "") -> str:
    base = compact_name1(name1)
    if not base:
        return ""

    # If Name 1 needed compaction, it already contains the highest-value information we can
    # safely preserve under the 80-character limit. Do not append extra data.
    raw_base = normalize_title_chars(clean_text(name1))
    if len(raw_base) > EBAY_TITLE_LIMIT:
        return base

    title_words = base.split()
    seen = {token_key(t) for t in title_words if token_key(t)}

    candidates = sorted(candidate_segments(technical_data, description), key=lambda x: (-x[0], x[1]))
    added = 0
    for priority, _order, phrase in candidates:
        # Unlabelled description phrases are low priority and only enrich genuinely short names.
        if priority <= 45 and len(base) >= 55:
            continue

        phrase_words = phrase.split()
        novel_words = [w for w in phrase_words if token_key(w) and token_key(w) not in seen]
        if not novel_words:
            continue

        novel_phrase = strip_dangling_end(" ".join(novel_words))
        if not novel_phrase:
            continue
        if len(novel_phrase.split()) > 6:
            continue

        proposal = f"{' '.join(title_words)} {novel_phrase}".strip()
        if len(proposal) > EBAY_TITLE_LIMIT:
            continue

        title_words.extend(novel_phrase.split())
        for word in novel_phrase.split():
            key = token_key(word)
            if key:
                seen.add(key)
        added += 1
        if added >= 3 or len(" ".join(title_words)) >= EBAY_TITLE_LIMIT - 8:
            break

    return strip_dangling_end(" ".join(title_words))[:EBAY_TITLE_LIMIT].rstrip(" -|,;:")


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
