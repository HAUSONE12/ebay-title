from src.ebay_title_automation import build_ebay_title, clean_text


def test_clean_text_removes_html():
    assert clean_text("<p>Stahlrohr</p><ul><li>570 g</li></ul>") == "Stahlrohr | 570 g"


def test_title_max_80_chars():
    title = build_ebay_title(
        "Gipserbeil Kopfgewicht 570 g Stahlrohr PROMAT",
        description="schwarz lackiert; geraute Bahn; geschliffene blanke Schneide; mit Stahlrohrstiel und Kunststoffgriff mit angerauter Oberfläche; mit Stiftsicherung",
        technical_data="Gewicht: 570 g; Material: Stahl; Farbe: Schwarz",
    )
    assert len(title) <= 80
    assert title.startswith("Gipserbeil Kopfgewicht 570 g Stahlrohr PROMAT")


def test_no_duplicate_words_are_added():
    title = build_ebay_title(
        "Hammer Stahl 500 g",
        description="Hammer aus Stahl; rutschfester Griff",
        technical_data="Material: Stahl; Gewicht: 500 g; Griff: Kunststoff",
    )
    words = [w.casefold() for w in title.split()]
    assert words.count("stahl") == 1
    assert words.count("hammer") == 1


def test_structured_labels_are_not_copied_into_title():
    title = build_ebay_title(
        "Klappbock",
        description="Der Klappbock vereint kompakte Bauweise und hohe Stabilität.",
        technical_data="Material: lackiertem Stahl; Norm: DIN EN 131; Einsatzbereich: den mobilen Baustelleneinsatz",
    )
    assert "Material:" not in title
    assert "Norm:" not in title
    assert "Einsatzbereich" not in title
    assert "Stahl lackiert" in title
    assert "DIN EN 131" in title


def test_long_name_is_compacted_without_dangling_connector():
    title = build_ebay_title(
        "PICARD Betonschalmeister 650, Kopfgewicht 600 g, geraut, Stahlrohrstiel mit 2K-Griff"
    )
    assert len(title) <= 80
    assert "2K-Griff" in title
    assert not title.casefold().endswith((" mit", " und", " für", " der", " die", " das"))


def test_dimension_heavy_name_is_compacted_for_search():
    title = build_ebay_title(
        'Steckschlüssel-Satz 27-teilig 1/2 ″ Schlüsselweiten 10-32 mm Anzahl Zähne 36 6-Kant'
    )
    assert len(title) <= 80
    assert '1/2"' in title
    assert "SW 10-32 mm" in title
    assert "36 Zähne" in title
    assert "6-Kant" in title


def test_multi_ratchet_tooth_counts_are_preserved():
    title = build_ebay_title(
        'Steckschlüssel-Satz 55-teilig 1/4 + 1/2 ″ Schlüsselweiten 4-32 mm Anzahl Zähne 20/36 6-Kant'
    )
    assert "20/36 Zähne" in title
    assert len(title) <= 80


def test_duplicate_material_does_not_leave_orphan_adjective():
    title = build_ebay_title(
        "Lackwanne ERGOLINE grau Polypropylen",
        description="aus bruchfestem Polypropylen",
        technical_data="",
    )
    assert title == "Lackwanne ERGOLINE grau Polypropylen"


def test_empty_name_returns_empty_title():
    assert build_ebay_title("", "Beschreibung", "Daten") == ""
