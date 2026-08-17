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
SHOPIFY_TITLE_LIMIT = 120
BASE_TITLE_BUDGET = 88
STATE_FILE = Path("state/last_processed.json")

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
    "verfügt", "verfuegt", "sorgt", "besteht", "zeichnet", "ideal", "perfekt",
    "breite", "höhe", "hoehe", "länge", "laenge", "durchmesser", "maße", "masse",
    "abmessungen", "weite",
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
    "gewicht": 90,
    "kopfgewicht": 92,
    "material": 102,
    "werkstoff": 102,
    "norm": 98,
    "schutzklasse": 98,
    "sicherheitsklasse": 98,
    "farbe": 88,
    "oberfläche": 84,
    "oberflaeche": 84,
    "ausführung": 82,
    "ausfuehrung": 82,
    "inhalt": 65,
    "größe": 35,
    "groesse": 35,
    "maße": 35,
    "masse": 35,
    "abmessungen": 35,
    "länge": 35,
    "laenge": 35,
    "breite": 35,
    "höhe": 35,
    "hoehe": 35,
    "durchmesser": 35,
}

KNOWN_LABEL_PATTERN = re.compile(
    r"(?i)(?<!\w)("
    r"Geeignet\s+für|Geeignet\s+fuer|Einsatzbereich|Anwendung|Beschreibung|Hinweise?|"
    r"Lieferumfang|Verwendung|Weite|Material|Werkstoff|Farbe|Norm|Schutzklasse|Sicherheitsklasse|"
    r"Gewicht|Kopfgewicht|Größe|Groesse|Maße|Masse|Abmessungen|Länge|Laenge|Breite|Höhe|Hoehe|"
    r"Durchmesser|Oberfläche|Oberflaeche|Ausführung|Ausfuehrung|Inhalt"
    r")\s*:"
)

DISPLAY_LABELS = {
    "größe": "Größe",
    "groesse": "Größe",
    "maße": "Maße",
    "masse": "Maße",
    "abmessungen": "Maße",
    "länge": "Länge",
    "laenge": "Länge",
    "breite": "Breite",
    "höhe": "Höhe",
    "hoehe": "Höhe",
    "durchmesser": "Durchmesser",
    "inhalt": "Inhalt",
}

DIMENSION_LABELS = {
    "größe", "groesse", "maße", "masse", "abmessungen", "länge", "laenge",
    "breite", "höhe", "hoehe", "durchmesser",
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
        "®": "", "™": "", "©": "", "•": " ", "·": " ", "—": "-",
        "„": '"', "“": '"', "”": '"', "’": "'", "″": '"', "×": "x",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"[^\wÄÖÜäöüßØø0-9%+./,:()'\"xX\-– ]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r'(?<=\d)\s+"', '"', value)
    value = re.sub(r"\s+([,.:])", r"\1", value)
    return value.strip(" -–|,;:")


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
    return " ".join(words).rstrip(" -–|,;:")


def clip_words(value: str, limit: int) -> str:
    value = strip_dangling_end(value)
    if len(value) <= limit:
        return value
    selected: list[str] = []
    for word in value.split():
        proposal = " ".join(selected + [word])
        if len(proposal) > limit:
            break
        selected.append(word)
    return strip_dangling_end(" ".join(selected))


def compact_source_title(name1: str, limit: int = BASE_TITLE_BUDGET) -> str:
    base = normalize_title_chars(clean_text(name1))
    if not base:
        return ""

    replacements: list[tuple[str, str]] = [
        (r"\bRollenbreite\s+(?=\d)", ""),
        (r"\bFlorhöhe\s+(?=\d)", "Flor "),
        (r"\bKern-?\s*Ø\s*(?=\d)", "Kern Ø"),
        (r"\bBügellänge\s+(?=\d)", "Bügel "),
        (r"\bBügel-?\s*Ø\s*(?=\d)", "Bügel Ø"),
        (r"\bSchlüsselweiten\s+(?=\d)", "SW "),
        (r"\bAnzahl\s+Zähne\s+([\d/]+)", r"\1 Zähne"),
        (r"\bInhalt\s+(?=\d+-teilig\b)", ""),
    ]
    for pattern, replacement in replacements:
        base = re.sub(pattern, replacement, base, flags=re.I)
    base = re.sub(r"\s+", " ", base).strip()

    if len(base) <= limit:
        return strip_dangling_end(base)

    without_fillers = " ".join(
        word for word in base.replace(",", " ").split()
        if token_key(word) not in DANGLING_WORDS
    )
    return clip_words(without_fillers, limit)


def normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", normalize_title_chars(label).casefold()).strip()


def clean_candidate_value(value: str) -> str:
    value = normalize_title_chars(value).strip(" -–|,;:")
    words = value.split()
    while words and token_key(words[0]) in DANGLING_WORDS:
        words.pop(0)
    while words and token_key(words[-1]) in DANGLING_WORDS:
        words.pop()
    return " ".join(words).rstrip(" .,-–|;:")


def base_german_descriptor(word: str) -> str:
    lower = word.casefold()
    for suffix in ("em", "en", "er", "es", "e"):
        if len(word) >= 7 and lower.endswith(suffix):
            return word[: -len(suffix)]
    return word


def compact_material_value(value: str) -> str:
    words = useful_tokens(clean_candidate_value(value))
    for index, word in enumerate(words):
        if token_key(word) in MATERIAL_TERMS:
            if index > 0 and token_key(words[index - 1]) not in DANGLING_WORDS:
                descriptor = base_german_descriptor(words[index - 1])
                return f"{word} {descriptor}".strip()
            return word
    return ""


def source_segments(source: str) -> list[str]:
    cleaned = clean_text(source)
    if not cleaned:
        return []
    cleaned = KNOWN_LABEL_PATTERN.sub(lambda m: f" | {m.group(1)}:", cleaned)
    return [seg.strip() for seg in re.split(r"\s*\|\s*|\s*;\s*", cleaned) if seg.strip()]


def format_structured_candidate(label: str, value: str) -> str:
    if label in {"material", "werkstoff"}:
        return compact_material_value(value)

    value = clean_candidate_value(value)
    if not value:
        return ""

    if label in DISPLAY_LABELS:
        return f"{DISPLAY_LABELS[label]} {value}"
    return value


def candidate_segments(technical_data: str, description: str) -> Iterable[tuple[int, int, str]]:
    order = 0
    for source_kind, source_priority, source in (
        ("technical", 12, technical_data),
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
                continue

            if label:
                phrase = format_structured_candidate(label, value)
            else:
                phrase = clean_candidate_value(value)
            if not phrase:
                continue

            words = useful_tokens(phrase)
            if not words:
                continue

            if source_kind == "description" and not label:
                if len(words) < 2 or len(words) > 5:
                    continue
                if raw_segment.strip().endswith((".", "!", "?")):
                    continue
                if any(token_key(word) in DESCRIPTION_BAD_WORDS for word in words):
                    continue

            if label and len(words) > 7:
                continue
            if label == "norm" and not re.search(r"\b(?:DIN|EN|ISO|VDE|S[1-7]|SRC|ESD)\b", phrase, re.I):
                continue
            if label == "inhalt" and not re.search(r"\d", phrase):
                continue
            if re.fullmatch(r"\d+(?:[.,]\d+)?", phrase):
                continue

            numeric_tokens = re.findall(r"(?<![A-Za-zÄÖÜäöüß])\d+(?:[.,]\d+)?(?![A-Za-zÄÖÜäöüß])", phrase)
            if label in DIMENSION_LABELS:
                if not re.search(r"(?:\bmm\b|\bcm\b|\bm\b|\d\")", phrase, re.I):
                    continue
                has_dimension_pair = bool(re.search(r"\d+(?:[.,]\d+)?\s*[xX]\s*\d+(?:[.,]\d+)?", phrase))
                if len(numeric_tokens) > 1 and not has_dimension_pair:
                    continue
                if len(numeric_tokens) > 2:
                    continue
            elif label != "norm" and len(numeric_tokens) > 3:
                continue

            priority = source_priority + LABEL_PRIORITIES.get(label, 50)
            yield priority, order, " ".join(words)


def canonical_title(value: str) -> str:
    return " ".join(token_key(word) for word in normalize_title_chars(value).split() if token_key(word))


def fallback_rewrite(name1: str) -> str:
    """Create a source-only rewrite when no useful enrichment is available."""
    original = normalize_title_chars(clean_text(name1))
    compact = compact_source_title(original, limit=SHOPIFY_TITLE_LIMIT)
    if not compact:
        return ""

    words = compact.split()
    for index, word in enumerate(words[1:], start=1):
        if re.fullmatch(r"\d+(?:[/-]\d+)?-teilig", word, re.I):
            candidate = " ".join([word] + words[:index] + words[index + 1 :])
            candidate = clip_words(candidate, SHOPIFY_TITLE_LIMIT)
            if canonical_title(candidate) != canonical_title(original):
                return candidate

    if len(words) >= 3:
        first_letters = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", words[0])
        if len(first_letters) >= 3 and first_letters.upper() == first_letters:
            candidate = clip_words(f"{' '.join(words[1:])} – {words[0]}", SHOPIFY_TITLE_LIMIT)
            if canonical_title(candidate) != canonical_title(original):
                return candidate

        last = words[-1]
        last_letters = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", last)
        if len(last_letters) >= 3 and last_letters.upper() == last_letters:
            candidate = clip_words(f"{last} {' '.join(words[:-1])}", SHOPIFY_TITLE_LIMIT)
            if canonical_title(candidate) != canonical_title(original):
                return candidate

    if canonical_title(compact) != canonical_title(original):
        return compact

    if len(words) >= 2:
        return clip_words(f"{words[0]} – {' '.join(words[1:])}", SHOPIFY_TITLE_LIMIT)
    return ""


def build_shopify_title(name1: str, description: str = "", technical_data: str = "") -> str:
    original = normalize_title_chars(clean_text(name1))
    if not original:
        return ""

    candidates = sorted(candidate_segments(technical_data, description), key=lambda item: (-item[0], item[1]))
    base = compact_source_title(original)
    if not base:
        return ""
    if not candidates:
        return fallback_rewrite(original)

    base_keys = {token_key(word) for word in base.split() if token_key(word)}
    additions: list[str] = []
    addition_keys: set[str] = set()
    long_base = len(base) >= 70

    for priority, _order, phrase in candidates:
        if long_base and priority < 80:
            continue

        phrase_words = phrase.split()
        phrase_keys = {token_key(word) for word in phrase_words if token_key(word)}
        known_keys = base_keys | addition_keys
        if not phrase_keys or phrase_keys.issubset(known_keys):
            continue

        novel_words = [word for word in phrase_words if token_key(word) and token_key(word) not in known_keys]
        if priority <= 50 and len(novel_words) < 2:
            continue

        novel_phrase = strip_dangling_end(" ".join(novel_words))
        if not novel_phrase:
            continue

        proposal_additions = additions + [novel_phrase]
        proposal = f"{base} – {', '.join(proposal_additions)}"
        if len(proposal) > SHOPIFY_TITLE_LIMIT:
            continue

        additions.append(novel_phrase)
        addition_keys.update(token_key(word) for word in novel_words if token_key(word))
        if len(additions) >= 3:
            break

    if not additions:
        return fallback_rewrite(original)

    title = normalize_title_chars(f"{base} – {', '.join(additions)}")
    title = clip_words(title, SHOPIFY_TITLE_LIMIT)

    if canonical_title(title) == canonical_title(original):
        return fallback_rewrite(original)
    return title


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
                    by_item[item_id] = min(variation_id, by_item.get(item_id, variation_id))

            if payload.get("isLastPage") is True:
                break
            last_page = payload.get("lastPageNumber")
            if isinstance(last_page, int) and page >= last_page:
                break
            if not entries:
                break
            page += 1

        return [StockItem(item_id=item_id, variation_id=variation_id) for item_id, variation_id in sorted(by_item.items())]

    def get_description(self, item_id: int, variation_id: int, lang: str = LANG) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/rest/items/{item_id}/variations/{variation_id}/descriptions/{lang}",
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected description payload for item {item_id}")
        return payload

    def update_titles(self, item_id: int, variation_id: int, title: str, lang: str = LANG) -> None:
        self._request(
            "PUT",
            f"/rest/items/{item_id}/variations/{variation_id}/descriptions/{lang}",
            json={"itemId": item_id, "lang": lang, "name": title, "name2": title},
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

    pending = [stock for stock in client.list_positive_stock() if stock.item_id > checkpoint]
    if max_items > 0:
        pending = pending[:max_items]

    print(f"Warehouse {WAREHOUSE_ID}: {len(pending)} new article(s) after checkpoint {checkpoint}.")
    processed = 0

    for stock in pending:
        print(f"\nArtikelID {stock.item_id} / VariationID {stock.variation_id}")
        try:
            text = client.get_description(stock.item_id, stock.variation_id)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                print("SKIP: no German text record")
                if not dry_run:
                    save_state(stock.item_id)
                processed += 1
                continue
            raise

        source_name1 = str(text.get("name") or "").strip()
        current_name2 = str(text.get("name2") or "").strip()
        if not source_name1:
            print("SKIP: Name 1 is empty")
            if not dry_run:
                save_state(stock.item_id)
            processed += 1
            continue

        title = build_shopify_title(
            name1=source_name1,
            description=str(text.get("description") or ""),
            technical_data=str(text.get("technicalData") or ""),
        )
        if not title:
            title = normalize_title_chars(source_name1)
            print("FALLBACK: no safe rewrite available; Name 1 and Name 2 will be synchronized to the source title")

        print(f"Source Name 1: {source_name1}")
        print(f"Current Name 2: {current_name2}")
        print(f"Proposed Name 1 + Name 2 ({len(title)} chars): {title}")

        if dry_run:
            print("DRY RUN: no update, checkpoint unchanged")
        else:
            already_same = (
                canonical_title(source_name1) == canonical_title(title)
                and canonical_title(current_name2) == canonical_title(title)
            )
            if already_same:
                print("UNCHANGED: Name 1 and Name 2 already match the generated title")
            else:
                client.update_titles(stock.item_id, stock.variation_id, title)
                verified_text = client.get_description(stock.item_id, stock.variation_id)
                verified_name1 = str(verified_text.get("name") or "").strip()
                verified_name2 = str(verified_text.get("name2") or "").strip()
                if (
                    canonical_title(verified_name1) != canonical_title(title)
                    or canonical_title(verified_name2) != canonical_title(title)
                ):
                    raise RuntimeError(
                        f"Title verification failed for ArtikelID {stock.item_id}: "
                        f"expected both fields {title!r}, got Name 1={verified_name1!r}, Name 2={verified_name2!r}"
                    )
                print("UPDATED + VERIFIED: Name 1 = Name 2")
            save_state(stock.item_id)

        processed += 1

    return processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite NORD WEST plentyONE titles for Shopify and keep Name 1 and Name 2 identical"
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not update plentyONE or the checkpoint")
    parser.add_argument("--max-items", type=int, default=0, help="Limit number of articles; 0 means all")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    count = run(dry_run=args.dry_run, max_items=args.max_items)
    print(f"\nHandled {count} article(s).")
