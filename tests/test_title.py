from src.ebay_title_automation import build_ebay_title, clean_text


def test_clean_text_removes_html():
    assert clean_text("<p>Stahlrohr</p><ul><li>570 g</li></ul>") == "Stahlrohr | 570 g"


def test_title_max_80_chars():
    title = build_ebay_title(
        "Gipserbeil Kopfgewicht 570 g Stahlrohr PROMAT",
        description="schwarz lackiert; geraute Bahn; geschliffene blanke Schneide; mit Stahlrohrstiel und Kunststoffgriff mit angerauter Oberfläche; mit Stiftsicherung",
        technical_data="Gewicht 570 g; Material Stahl; Farbe Schwarz",
    )
    assert len(title) <= 80
    assert title.startswith("Gipserbeil Kopfgewicht 570 g Stahlrohr PROMAT")


def test_no_duplicate_words_are_added():
    title = build_ebay_title(
        "Hammer Stahl 500 g",
        description="Hammer aus Stahl Gewicht 500 g mit rutschfestem Griff",
        technical_data="Stahl; 500 g; Griff Kunststoff",
    )
    words = [w.casefold() for w in title.split()]
    assert words.count("stahl") == 1
    assert words.count("hammer") == 1


def test_empty_name_returns_empty_title():
    assert build_ebay_title("", "Beschreibung", "Daten") == ""
