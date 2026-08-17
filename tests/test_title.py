from src.ebay_title_automation import (
    PlentyClient,
    SHOPIFY_TITLE_LIMIT,
    build_shopify_title,
    clean_text,
)


def test_clean_text_removes_html():
    assert clean_text("<p>Stahlrohr</p><ul><li>570 g</li></ul>") == "Stahlrohr | 570 g"


def test_shopify_title_is_source_backed_and_different():
    source = "Gipserbeil Kopfgewicht 570 g Stahlrohr PROMAT"
    title = build_shopify_title(
        source,
        description="schwarz lackiert; geraute Bahn; geschliffene blanke Schneide; mit Stahlrohrstiel und Kunststoffgriff mit angerauter Oberfläche; mit Stiftsicherung",
        technical_data="Gewicht: 570 g; Material: Stahl; Farbe: Schwarz",
    )
    assert title
    assert title != source
    assert "Gipserbeil" in title
    assert "PROMAT" in title
    assert any(term in title for term in ("Schwarz", "geraute Bahn", "Stiftsicherung"))
    assert len(title) <= SHOPIFY_TITLE_LIMIT


def test_structured_data_makes_short_title_more_meaningful():
    title = build_shopify_title(
        "Klappbock",
        description="Der Klappbock vereint kompakte Bauweise und hohe Stabilität.",
        technical_data="Material: lackiertem Stahl; Norm: DIN EN 131; Gewicht: 15 kg",
    )
    assert title.startswith("Klappbock –")
    assert "Stahl lackiert" in title
    assert "DIN EN 131" in title
    assert "15 kg" in title


def test_dimensions_keep_their_context():
    title = build_shopify_title(
        "Malerrolle",
        technical_data="Breite: 180 mm; Florhöhe: 12 mm; Material: Polyacryl",
    )
    assert "Breite 180 mm" in title
    assert "Polyacryl" in title


def test_ambiguous_dimension_sequences_are_not_added():
    title = build_shopify_title(
        "Lackwanne ERGOLINE grau Polypropylen",
        description="Material: bruchfestem Polypropylen",
        technical_data="Höhe: 40 mm 35 mm; Breite: 160 mm 270 mm",
    )
    assert "bruchfest" in title
    assert "40 mm 35 mm" not in title
    assert "160 mm 270 mm" not in title


def test_no_rewrite_when_no_safe_enrichment_exists():
    assert build_shopify_title("Schraubendreher PH2", description="", technical_data="") == ""


def test_source_only_fallback_reorders_piece_count_without_inventing():
    title = build_shopify_title("Schraubendreher-Satz 7-teilig", description="", technical_data="")
    assert title == "7-teilig Schraubendreher-Satz"


def test_sentence_marketing_copy_is_not_used():
    title = build_shopify_title(
        "Klappbock",
        description="Der Klappbock ist die perfekte Lösung für jede Baustelle.",
        technical_data="Material: Stahl",
    )
    assert "perfekte Lösung" not in title
    assert "Stahl" in title


def test_long_title_stays_within_limit_without_dangling_word():
    title = build_shopify_title(
        "PICARD Betonschalmeister 650 Kopfgewicht 600 g geraut Stahlrohrstiel mit 2K-Griff für professionelle Anwendungen",
        technical_data="Material: Stahl; Farbe: Schwarz; Gewicht: 600 g",
    )
    assert title
    assert len(title) <= SHOPIFY_TITLE_LIMIT
    assert not title.casefold().endswith((" mit", " und", " für", " der", " die", " das"))


def test_duplicate_source_facts_do_not_prevent_new_fact():
    title = build_shopify_title(
        "Hammer Stahl 500 g",
        technical_data="Material: Stahl; Gewicht: 500 g; Farbe: Rot",
    )
    assert "Rot" in title
    assert title != "Hammer Stahl 500 g"


def test_update_titles_writes_identical_name1_and_name2(monkeypatch):
    client = PlentyClient("https://example.invalid", "user", "pass")
    captured = {}

    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = kwargs["json"]
        return object()

    monkeypatch.setattr(client, "_request", fake_request)
    client.update_titles(123, 456, "Neuer Shopify Titel")

    assert captured["method"] == "PUT"
    assert captured["json"]["name"] == "Neuer Shopify Titel"
    assert captured["json"]["name2"] == "Neuer Shopify Titel"


def test_empty_name_returns_empty_title():
    assert build_shopify_title("", "Beschreibung", "Daten") == ""
