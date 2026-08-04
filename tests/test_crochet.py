"""The crochet pattern engine: extraction, expansion, diagrams, layout, build."""

from __future__ import annotations

import json

import pytest

from artisan_forge.crochet import diagrams, etsy_data, expand, imagery
from artisan_forge.crochet.brand import BrandKit
from artisan_forge.crochet.extract import corpus_brief, extract_pattern, merge_sources
from artisan_forge.crochet.pdf import CrochetPDF
from artisan_forge.pdf.verify import extract_page_texts
from artisan_forge.products.crochet import (
    MODES,
    CrochetSpec,
    build_crochet,
    listing_from_pattern,
    validate,
    verify_pattern_pdf,
)

SOURCE_TEXT = """
COZY RIBBED CARDIGAN
A relaxed oversized cardigan worked in panels.

MATERIALS
- 1200 yards worsted weight cotton yarn
- 5.5 mm crochet hook
- 6 x 20 mm buttons
- Tapestry needle

GAUGE
16 sts x 12 rows = 4" x 4" in half double crochet with a 5.5 mm hook

SIZES
XS, S, M, L, XL, 2XL
Finished bust: 36 inches, 40 inches, 44 inches, 48 inches
Body length 24 inches. Sleeve length 18 inches.

ABBREVIATIONS
ch = chain
sc = single crochet
hdc = half double crochet
BLO = back loop only

NOTES
Read the whole pattern before you begin.

BACK PANEL
Row 1: Ch 73, hdc in 3rd ch from hook and each ch across. (71 hdc)
Row 2: Ch 2, turn, hdc BLO in each st across. (71 hdc)
Rnd 3: Join yarn, work 44 hdc evenly around. (44 hdc)

ASSEMBLY
Block all panels before seaming.
Seam the shoulders using mattress stitch.
Attach the sleeves with a whipstitch seam.
"""

ETSY_CSV = (
    "Title,Description,Price,Tags,Materials,SKU\n"
    "Chunky Cropped Cardigan,\"Oversized cardigan in worsted cotton, 5.5 mm hook, sizes XS-2XL\","
    "42.00,\"cardigan,crochet,oversized\",\"cotton,acrylic\",CARD-01\n"
    "Granny Square Tote,Sturdy market tote from granny squares in dk cotton.,28.50,"
    "\"tote,bag\",cotton,TOTE-02\n"
    "Ribbed Beanie,Bulky ribbed beanie worked in the round with an 8 mm hook.,22.00,"
    "\"beanie,hat\",merino,BEAN-03\n"
)


@pytest.fixture()
def source_file(tmp_path):
    path = tmp_path / "cardigan.txt"
    path.write_text(SOURCE_TEXT, encoding="utf-8")
    return path


@pytest.fixture()
def brand():
    return BrandKit(
        store_name="Loop & Thread Co",
        designer_name="Marie Dupont",
        email="hello@loopthread.com",
        website="https://www.loopthread.com/",
        instagram="https://instagram.com/loopthread/",
        licence="small_business",
        accent_hex="#b5643c",
    )


# ------------------------------------------------------------------ branding
def test_brand_kit_normalises_and_labels(brand):
    clean = brand.cleaned()
    assert clean.store_name == "Loop & Thread Co"
    assert clean.website == "loopthread.com"          # scheme and slash stripped
    assert clean.instagram == "loopthread"            # profile URL reduced to the handle
    assert clean.accent_hex == "#B5643C"
    assert clean.credit == "Marie Dupont for Loop & Thread Co"
    assert "loopthread.com" in clean.footer()
    assert any("Email: hello@" in line for line in clean.contact_lines())

    may, may_not = clean.licence_terms()
    assert any("Sell finished items" in line for line in may)
    assert not any("Sell finished items" in line for line in may_not)


def test_brand_kit_rejects_bad_values():
    clean = BrandKit(email="not-an-email", accent_hex="octarine", logo_path="/nope.png").cleaned()
    assert clean.email == ""
    assert clean.accent_hex is None
    assert clean.logo is None
    assert clean.shop == "Independent Pattern Design"

    personal = BrandKit(licence="nonsense").cleaned()
    assert personal.licence == "personal"
    _, may_not = personal.licence_terms()
    assert any("Sell finished items" in line for line in may_not)


# ---------------------------------------------------------------- extraction
def test_extract_reads_the_real_content(source_file):
    source = extract_pattern(source_file)
    assert source.ok
    assert source.title == "COZY RIBBED CARDIGAN"
    assert source.garment == "cardigan"
    assert source.yarn_weight == "worsted"
    assert source.gauge["stitches"] == 16.0
    assert source.gauge["rows"] == 12.0
    assert source.sizes == ["XS", "S", "M", "L", "XL", "2XL"]
    assert source.abbreviations["hdc"] == "half double crochet"
    assert [row["label"] for row in source.rows] == ["Row 1", "Row 2", "Rnd 3"]
    assert source.rows[0]["count"] == "71 hdc"
    assert source.stitch_counts[0] == {"row": "Row 1", "count": "71 hdc"}
    assert any("mattress stitch" in step for step in source.assembly_steps)
    assert "Read the whole pattern" in source.notes[0]


def test_extract_ignores_millimetre_measurements_that_are_not_hooks(source_file):
    """A materials list full of "20 mm buttons" must not become a hook size."""
    source = extract_pattern(source_file)
    assert source.hooks_mm == [5.5]


def test_extract_ranks_stitches_by_how_often_they_are_used(source_file):
    """The dominant stitch drives the turning chain, so order must be by count."""
    source = extract_pattern(source_file)
    assert source.stitch_frequency["hdc"] > source.stitch_frequency["sc"]
    assert source.stitches_used[0] == "hdc"
    assert list(source.stitch_frequency) == source.stitches_used

    corpus = merge_sources([source, source])
    assert corpus["stitches_used"][0] == "hdc"
    assert corpus["stitch_frequency"]["hdc"] == source.stitch_frequency["hdc"] * 2


def test_extract_reports_unreadable_files_instead_of_raising(tmp_path):
    missing = extract_pattern(tmp_path / "ghost.pdf")
    assert not missing.ok and missing.error

    unsupported = extract_pattern(tmp_path / "notes.docx")
    assert not unsupported.ok and unsupported.error


def test_merge_sources_builds_a_prompt_sized_brief(source_file):
    source = extract_pattern(source_file)
    corpus = merge_sources([source, source])
    assert corpus["sources"] == 2
    assert corpus["garment"] == "cardigan"
    assert corpus["yarn_weight"] == "worsted"
    assert corpus["gauge"]["stitches"] == 16.0
    assert corpus["total_rows"] == 6

    brief = corpus_brief(corpus, limit=6000)
    assert "cardigan" in brief
    assert "16 sts x 12 rows" in brief
    assert len(brief) <= 6000


def test_merge_sources_survives_all_failures(tmp_path):
    corpus = merge_sources([extract_pattern(tmp_path / "a.pdf")])
    assert corpus["sources"] == 0
    assert len(corpus["failed"]) == 1
    assert corpus_brief(corpus)  # still renders something


# ---------------------------------------------------------------- etsy input
@pytest.mark.parametrize(
    "payload",
    [
        ETSY_CSV,
        '{"results":[{"title":"Chunky Cropped Cardigan","description":"worsted cotton 5.5 mm",'
        '"tags":["cardigan"],"price":{"amount":4200,"divisor":100},"sku":"CARD-01"}]}',
        "Title: Chunky Cropped Cardigan\nPrice: 42\nDescription: worsted cotton, 5.5 mm hook",
    ],
    ids=["csv", "json", "blocks"],
)
def test_etsy_data_parses_every_supported_shape(payload):
    products, warnings = etsy_data.load_products(payload)
    assert products and not warnings
    first = products[0]
    assert first.number == 1
    assert "Cardigan" in first.title
    assert first.garment() == "cardigan"
    assert first.hook_mm() == 5.5
    assert "42" in first.price


def test_etsy_products_are_numbered_and_selectable():
    products, _ = etsy_data.load_products(ETSY_CSV)
    assert [p.number for p in products] == [1, 2, 3]
    assert etsy_data.pick(products, 2).title == "Granny Square Tote"
    assert etsy_data.pick(products, 3).yarn_weight() == "bulky"

    with pytest.raises(ValueError, match="out of range"):
        etsy_data.pick(products, 4)
    with pytest.raises(ValueError, match="No products"):
        etsy_data.pick([], 1)


def test_etsy_product_brief_and_catalogue_are_bounded():
    products, _ = etsy_data.load_products(ETSY_CSV)
    brief = products[0].brief(limit=2200)
    assert "Chunky Cropped Cardigan" in brief
    assert "Detected garment: cardigan" in brief
    assert len(brief) <= 2200

    catalogue = etsy_data.catalogue_brief(products)
    assert "3 products" in catalogue
    assert "2. Granny Square Tote" in catalogue


def test_etsy_images_attach_to_the_matching_product(tmp_path):
    from PIL import Image

    folder = tmp_path / "photos"
    folder.mkdir()
    Image.new("RGB", (32, 32), (200, 160, 120)).save(folder / "CARD-01-front.png")
    Image.new("RGB", (32, 32), (120, 160, 200)).save(folder / "TOTE-02.png")

    products, warnings = etsy_data.load_products(ETSY_CSV, image_dir=folder)
    assert not warnings
    assert len(products[0].local_images()) == 1
    assert products[0].local_images()[0].name.startswith("CARD-01")
    assert len(products[1].local_images()) == 1
    assert products[2].local_images() == []


# ---------------------------------------------------------------- expansion
def test_template_pattern_is_complete_and_self_consistent():
    pattern = expand.template_pattern(garment="cardigan")
    assert not [key for key in expand.SECTION_KEYS if key not in pattern]
    assert pattern["skill_level"] in expand.SKILL_LEVELS
    assert expand.total_steps(pattern) >= 20
    assert len(pattern["troubleshooting"]) >= 6
    assert len(pattern["abbreviations"]) >= 12
    assert len(pattern["sections"]) >= 4

    # every sizing row lines up with the header
    labels = pattern["sizes"]["labels"]
    assert len(pattern["sizes"]["rows"]) >= 8
    assert all(len(row["values"]) == len(labels) for row in pattern["sizes"]["rows"])
    # graded to the nearest half inch, not to 34.08
    for row in pattern["sizes"]["rows"]:
        for value in row["values"]:
            assert not value.split(" ")[0].endswith((".1", ".2", ".3", ".7", ".9"))

    assert [y["size"] for y in pattern["yarn_guide"]["yardage"]] == labels
    assert {len(row) for row in pattern["chart"]["grid"]} == {12}


def test_template_pattern_follows_the_measured_gauge(source_file):
    corpus = merge_sources([extract_pattern(source_file)])
    pattern = expand.template_pattern(corpus=corpus)
    assert pattern["gauge"]["stitches"] == 16.0
    assert pattern["gauge"]["rows"] == 12.0
    assert pattern["gauge"]["hook"] == "5.5 mm"
    assert pattern["yarn_guide"]["weight"] == "worsted"
    assert pattern["primary_stitch"] == "hdc"
    assert pattern["chart"]["turning_chain"] == 2  # hdc, not a dc's 3


def test_template_instructions_state_the_right_chain_and_ordinal():
    pattern = expand.template_pattern(garment="cardigan")
    first = pattern["sections"][0]["steps"]
    chain = int(first[0]["text"].split("Ch ")[1].rstrip("."))
    assert first[0]["count"] == f"{chain} ch"     # count matches the chain worked
    assert "3rd ch from the hook" in first[1]["text"]
    assert "3th" not in first[1]["text"]


def test_normalise_pattern_survives_a_hostile_model_response():
    fallback = expand.template_pattern(garment="cardigan")
    pattern = expand.normalise_pattern(
        {
            "title": "   Ribbed Raglan   ",
            "skill_level": "somewhere around INTERMEDIATE ish",
            "skill_requirements": "one string, not a list",
            "time_estimates": [{"size": "M"}, {"size": "L", "hours": "20-24"}],
            "materials": [{"detail": "no item key"}, {"item": "Hook", "detail": "5 mm"}],
            "gauge": {"stitches": 999, "rows": "twelve-ish 12", "hook": "4 mm"},
            "sizes": {
                "labels": ["S", "M", "L"],
                "rows": [
                    {"measure": "Bust", "values": ["34 in", "38 in"]},
                    {"values": ["no measure key"]},
                    {"measure": "Length", "values": ["22", "23", "24", "25", "26"]},
                ],
            },
            "abbreviations": [{"abbr": "sc"}, {"abbr": "dc", "meaning": "double crochet"}],
            "sections": [
                {"title": "Yoke", "steps": [
                    "a plain string step",
                    {"label": "Row 1", "text": "Ch 90.", "count": "90 ch"},
                    {"label": "Row 2"},
                ]},
                {"nonsense": True},
            ],
            "chart": {"grid": [["sc", "dc"], ["sc"], "ch ch ch ch"], "chain": 999},
            "schematic": {"pieces": [
                {"name": "Back", "width_in": "20 in", "height_in": 24},
                {"name": "Bad", "width_in": 0},
                {"name": "Huge", "width_in": 5000, "height_in": 5000},
            ]},
            "troubleshooting": [{"problem": "Too wide"}, {"cause": "no problem key"}],
        },
        fallback,
    )

    assert pattern["title"] == "Ribbed Raglan"
    assert pattern["skill_level"] == "intermediate"
    assert isinstance(pattern["skill_requirements"], list)
    assert pattern["time_estimates"] == [{"size": "L", "hours": "20-24"}]
    assert pattern["materials"] == [{"item": "Hook", "detail": "5 mm"}]
    assert pattern["gauge"]["stitches"] == fallback["gauge"]["stitches"]  # 999 rejected
    assert pattern["gauge"]["rows"] == 12.0                               # parsed out of prose
    assert pattern["sizes"]["labels"] == ["S", "M", "L"]
    # rows are padded or trimmed to the header width, and unusable rows dropped
    assert [r["measure"] for r in pattern["sizes"]["rows"]] == ["Bust", "Length"]
    assert all(len(r["values"]) == 3 for r in pattern["sizes"]["rows"])
    assert pattern["abbreviations"] == [{"abbr": "dc", "meaning": "double crochet"}]
    assert [(s["title"], len(s["steps"])) for s in pattern["sections"]] == [("Yoke", 2)]
    assert {len(row) for row in pattern["chart"]["grid"]} == {4}  # squared off
    assert pattern["chart"]["chain"] == fallback["chart"]["chain"]  # 999 rejected
    assert [p["name"] for p in pattern["schematic"]["pieces"]] == ["Back", "Huge"]
    assert pattern["schematic"]["pieces"][1]["width_in"] == 90  # clamped
    assert pattern["troubleshooting"] == [{"problem": "Too wide"}]


@pytest.mark.parametrize("garbage", [None, {}, [], "a string", {"sections": "nope", "sizes": 5}])
def test_normalise_pattern_always_returns_something_renderable(garbage):
    fallback = expand.template_pattern(garment="hat")
    pattern = expand.normalise_pattern(garbage, fallback)
    assert not [key for key in expand.SECTION_KEYS if key not in pattern]
    assert pattern["sections"] and pattern["sizes"]["rows"]


def test_ensure_stitch_counts_derives_the_table_from_the_steps():
    pattern = expand.template_pattern(garment="hat")
    pattern["stitch_counts"] = []
    pattern["sections"] = [
        {"title": "Crown", "steps": [{"label": "Rnd 1", "text": "8 dc", "count": "8 dc"}]}
    ]
    filled = expand.ensure_stitch_counts(pattern)
    assert filled["stitch_counts"] == [{"row": "Crown, Rnd 1", "count": "8 dc", "note": ""}]


def test_content_prompt_carries_the_schema_and_the_brief():
    prompt = expand.content_prompt(
        "some source material", garment="cardigan", sizes=["S", "M"], designer="Marie"
    )
    assert "some source material" in prompt
    assert "S, M" in prompt
    for key in ("stitch_counts", "troubleshooting", "yarn_guide", "care", "seaming",
                "blocking", "skill_requirements", "time_estimates", "sizes"):
        assert f'"{key}"' in prompt


# ----------------------------------------------------------------- diagrams
@pytest.mark.skipif(not diagrams.available(), reason="matplotlib is not installed")
def test_build_all_draws_every_plate_the_pattern_has_data_for(tmp_path):
    pattern = expand.template_pattern(garment="cardigan")
    plates, warnings = diagrams.build_all(pattern, tmp_path, "boho")
    assert not warnings
    assert {"schematic", "gauge", "chart", "foundation", "body", "yardage"} <= set(plates)
    assert len([k for k in plates if k.startswith("seam_")]) >= 3
    for path in plates.values():
        assert path.exists() and path.stat().st_size > 5_000


@pytest.mark.skipif(not diagrams.available(), reason="matplotlib is not installed")
def test_diagrams_return_none_for_empty_input_instead_of_raising(tmp_path):
    assert diagrams.construction_schematic([], tmp_path / "a.png") is None
    assert diagrams.stitch_chart([], tmp_path / "b.png") is None
    assert diagrams.yardage_chart([], tmp_path / "c.png") is None
    # junk values still produce a plate rather than an exception
    assert diagrams.gauge_swatch({"stitches": "abc", "rows": None}, tmp_path / "d.png")


@pytest.mark.parametrize(
    ("stitch", "expected"),
    [("sc", 1), ("hdc", 2), ("dc", 3), ("tr", 4), ("dtr", 5), ("sl st", 1), ("puff", 3)],
)
def test_turning_chain_matches_the_stitch_height(stitch, expected):
    assert diagrams.turning_chain_for(stitch) == expected


def test_build_all_reports_when_matplotlib_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(diagrams, "available", lambda: False)
    plates, warnings = diagrams.build_all(expand.template_pattern(), tmp_path)
    assert plates == {}
    assert warnings and "matplotlib" in warnings[0]


# ------------------------------------------------------------------ imagery
def test_normalise_briefs_drops_junk_and_keeps_a_cover():
    fallback = expand.default_image_briefs("cardigan", "worsted", ["cotton"])
    assert imagery.normalise_briefs({"image_briefs": [{"prompt": "no"}]}, fallback) == fallback

    briefs = imagery.normalise_briefs(
        {"image_briefs": [
            {"key": "materials", "prompt": "An overhead flat lay of yarn and a hook on linen"},
            {"key": "materials", "prompt": "A duplicate slot that should be dropped"},
            {"prompt": "short"},
        ]},
        fallback,
    )
    keys = [b["key"] for b in briefs]
    assert keys.count("materials") == 1
    assert "cover" in keys  # injected so the cover page always has art


def test_build_imagery_renders_plates_offline(tmp_path):
    pattern = expand.template_pattern(garment="cardigan")
    result = imagery.build_imagery(
        pattern, "cardigan", tmp_path, theme_key="boho",
        generate_art=False, use_canva=False, plate_limit=2,
    )
    assert result["source"] == "procedural"
    assert result["plates"] and all(p.exists() for p in result["plates"].values())
    assert result["canva"]["status"] == "disabled"
    assert not result["warnings"]


def test_build_imagery_reports_canva_without_a_token(tmp_path, monkeypatch):
    monkeypatch.delenv("CANVA_ACCESS_TOKEN", raising=False)
    pattern = expand.template_pattern(garment="hat")
    result = imagery.build_imagery(
        pattern, "hat", tmp_path, generate_art=False, use_canva=True, plate_limit=1,
    )
    assert result["canva"]["status"] == "skipped"
    assert "CANVA_ACCESS_TOKEN" in result["canva"]["reason"]
    assert result["plates"]  # the artwork survives a skipped Canva step


# --------------------------------------------------------------------- pdf
def test_pattern_pdf_contains_every_section(tmp_path, brand):
    pattern = expand.ensure_stitch_counts(expand.template_pattern(garment="cardigan"))
    pdf_path, pages = CrochetPDF(pattern, brand=brand, theme="boho").render(tmp_path / "p.pdf")

    assert pdf_path.exists() and pdf_path.stat().st_size > 20_000
    kinds = [page["kind"] for page in pages]
    assert kinds[0] == "cover" and kinds[1] == "credits" and kinds[-1] == "thanks"
    assert "contents" in kinds and "instructions" in kinds

    texts = extract_page_texts(pdf_path)
    assert len(texts) == len(pages)
    body = "\n".join(texts).upper()
    for needed in (
        "CROCHET PATTERN", "LOOP & THREAD CO", "MARIE DUPONT", "CONTENTS",
        "ABOUT THIS PATTERN", "MATERIALS", "YARN GUIDE", "GAUGE", "SIZES",
        "ABBREVIATIONS", "CONSTRUCTION", "STITCH COUNTS", "ASSEMBLY",
        "BLOCKING", "TROUBLESHOOTING", "CARE INSTRUCTIONS", "THANK YOU", "LICENCE",
    ):
        assert needed in body, f"{needed} missing from the PDF"


def test_pattern_pdf_numbers_every_content_page(tmp_path):
    pattern = expand.template_pattern(garment="hat")
    pdf_path, pages = CrochetPDF(pattern).render(tmp_path / "p.pdf")
    texts = extract_page_texts(pdf_path)
    for index, (page, text) in enumerate(zip(pages, texts), start=1):
        if page["kind"] in ("cover", "credits"):
            continue
        assert f"{index} / {len(pages)}" in text


def test_pattern_pdf_paginates_without_losing_content(tmp_path):
    """Long sections must flow onto extra pages, not silently truncate."""
    pattern = expand.template_pattern(garment="blanket")
    pattern["sections"] = [
        {
            "title": f"Panel {n}",
            "notes": "A long lead-in note. " * 6,
            "steps": [
                {"label": f"Row {r}", "count": f"{120 - r} dc",
                 "text": "Ch 3, turn, dc in each st across to the marker, then keep going "
                         "with a deliberately long instruction that has to wrap. " * 2}
                for r in range(1, 26)
            ],
        }
        for n in range(1, 4)
    ]
    pattern["stitch_counts"] = [
        {"row": f"Row {i}", "count": f"{120 - i} dc", "note": "steady"} for i in range(1, 61)
    ]
    pattern["troubleshooting"] = [
        {"problem": f"Problem {i} with a fairly long title to force a wrap",
         "cause": "A wordy cause that wraps across lines. " * 3,
         "fix": "An equally wordy fix that also wraps. " * 3}
        for i in range(1, 13)
    ]

    _, pages = CrochetPDF(pattern).render(tmp_path / "long.pdf")
    kinds = [page["kind"] for page in pages]
    assert kinds.count("instructions") > 3
    assert kinds.count("counts") > 1
    assert kinds.count("troubleshooting") > 1

    for kind, key, expected in (
        ("instructions", "steps", 75),
        ("counts", "rows", 60),
        ("troubleshooting", "rows", 12),
    ):
        emitted = sum(len(p.get(key, [])) for p in pages if p["kind"] == kind)
        assert emitted == expected, f"{kind} lost content: {emitted} of {expected}"


def test_pattern_pdf_keeps_text_inside_the_page(tmp_path):
    """Guards the drawkit tracking leak: letter-spacing must not push text off."""
    import pypdfium2 as pdfium

    pattern = expand.ensure_stitch_counts(expand.template_pattern(garment="cardigan"))
    document = CrochetPDF(pattern, theme="boho")
    pdf_path, _ = document.render(tmp_path / "p.pdf")

    right_limit = document.page_w - document.margin_x + 2
    overflow: list[str] = []
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(doc)):
            page = doc[index]
            textpage = page.get_textpage()
            for char in range(textpage.count_chars()):
                if textpage.get_text_range(char, 1).strip():
                    if textpage.get_charbox(char, loose=False)[2] > right_limit:
                        overflow.append(f"page {index + 1}")
                        break
            textpage.close()
            page.close()
    finally:
        doc.close()
    assert not overflow, f"text runs past the right margin on {sorted(set(overflow))}"


def test_pattern_pdf_skips_pages_it_has_no_assets_for(tmp_path):
    pattern = expand.template_pattern(garment="hat")
    _, pages = CrochetPDF(pattern, diagrams={}, plates={}).render(tmp_path / "bare.pdf")
    kinds = {page["kind"] for page in pages}
    assert not {"chart", "foundation", "gallery"} & kinds
    assert "instructions" in kinds and "thanks" in kinds


# --------------------------------------------------------------- validation
def test_spec_validation_rejects_incomplete_input():
    with pytest.raises(ValueError, match="Unknown mode"):
        validate(CrochetSpec(mode="nope"))
    with pytest.raises(ValueError, match="at least one crochet pattern"):
        validate(CrochetSpec(mode="from_pdfs"))
    with pytest.raises(ValueError, match="at least one photo"):
        validate(CrochetSpec(mode="from_photos"))
    with pytest.raises(ValueError, match="Etsy product data"):
        validate(CrochetSpec(mode="from_etsy_data"))
    with pytest.raises(ValueError, match="at least 3 characters"):
        validate(CrochetSpec(mode="from_brief", brief="x"))
    with pytest.raises(ValueError, match="Unknown paper"):
        validate(CrochetSpec(mode="from_brief", brief="a cardigan", paper="papyrus"))
    with pytest.raises(ValueError, match="orientation"):
        validate(CrochetSpec(mode="from_brief", brief="a cardigan", orientation="sideways"))
    with pytest.raises(ValueError, match="1 or higher"):
        validate(CrochetSpec(mode="from_etsy_data", etsy_data_text="Title: X", product_number=0))


def test_spec_validation_clamps_out_of_range_values():
    spec = validate(CrochetSpec(
        mode="from_brief", brief="a cardigan", cost_mode="wild", listing_image_count=99,
        bleed_in=9.0, sizes=[" s ", "m", ""], theme="not-a-theme",
    ))
    assert spec.cost_mode == "standard"
    assert spec.listing_image_count == 10
    assert spec.bleed_in == 0.25
    assert spec.sizes == ["S", "M"]
    assert spec.theme == "minimalist"


def test_tech_pack_mode_forces_everything_offline():
    spec = validate(CrochetSpec(
        mode="tech_pack", brief="a blanket",
        generate_ai_copy=True, generate_ai_art=True, use_canva=True,
    ))
    assert spec.offline_only
    assert not spec.generate_ai_copy and not spec.generate_ai_art and not spec.use_canva


def test_product_slug_does_not_repeat_itself():
    assert CrochetSpec(title="Crochet Pattern").product_slug() == "crochet-pattern"
    assert CrochetSpec(garment="cardigan").product_slug() == "cardigan-crochet-pattern"
    assert CrochetSpec(title="Cozy Ribbed Cardigan").product_slug() == (
        "cozy-ribbed-cardigan-crochet-pattern"
    )


# ------------------------------------------------------------------ listing
def test_listing_copy_respects_etsy_limits():
    pattern = expand.template_pattern(garment="cardigan")
    listing = listing_from_pattern(CrochetSpec(garment="cardigan"), pattern)
    assert len(listing["title"]) <= 140
    assert 0 < len(listing["tags"]) <= 13
    assert all(len(tag) <= 20 for tag in listing["tags"])
    assert "WHAT YOU GET" in listing["description"]
    assert "SKILL LEVEL" in listing["description"]
    assert listing["is_digital"] is True


def test_listing_copy_prefers_the_model_when_it_supplied_one():
    pattern = expand.template_pattern(garment="hat")
    pattern["listing"] = {
        "title": "Model written title",
        "tags": ["a" * 40, "clean tag"],
        "description": "Model written description",
    }
    listing = listing_from_pattern(CrochetSpec(garment="hat"), pattern)
    assert listing["title"] == "Model written title"
    assert listing["description"] == "Model written description"
    assert listing["tags"][0] == "a" * 20  # over-long tag truncated, not dropped


# -------------------------------------------------------------- verification
def test_verify_pattern_pdf_flags_missing_content(tmp_path):
    pattern = expand.ensure_stitch_counts(expand.template_pattern(garment="cardigan"))
    pdf_path, pages = CrochetPDF(pattern).render(tmp_path / "p.pdf")

    report = verify_pattern_pdf(pdf_path, pattern, pages)
    assert report["ok"] and report["text_check"] == "passed"
    assert report["checks"] > 20

    # a section that was never rendered has to be reported
    tampered = dict(pattern)
    tampered["sections"] = pattern["sections"] + [
        {"title": "Phantom Panel", "notes": "", "steps": [
            {"label": "Row 999", "text": "never rendered", "count": ""}
        ]}
    ]
    bad = verify_pattern_pdf(pdf_path, tampered, pages)
    assert not bad["ok"]
    assert any("Phantom Panel" in error for error in bad["errors"])


# -------------------------------------------------------------- full builds
@pytest.fixture()
def offline(monkeypatch):
    """Force the whole engine offline so builds make no network calls."""
    monkeypatch.setenv("AF_OFFLINE", "1")
    monkeypatch.delenv("CANVA_ACCESS_TOKEN", raising=False)
    from artisan_forge.config import get_settings

    settings = get_settings()
    assert not settings.ai_available
    return settings


def _mode_kwargs(mode, tmp_path):
    if mode == "from_pdfs":
        path = tmp_path / "src.txt"
        path.write_text(SOURCE_TEXT, encoding="utf-8")
        return {"source_files": [str(path)], "brief": "An oversized cardigan"}
    if mode == "from_etsy_data":
        return {"etsy_data_text": ETSY_CSV, "product_number": 1}
    if mode == "from_photos":
        from PIL import Image

        photo = tmp_path / "finished.png"
        Image.new("RGB", (64, 64), (190, 150, 120)).save(photo)
        return {"source_files": [str(photo)], "garment": "beanie", "brief": "A ribbed beanie"}
    return {"brief": "An oversized ribbed cardigan", "garment": "cardigan"}


@pytest.mark.parametrize("mode", list(MODES))
def test_every_mode_builds_a_complete_product(mode, tmp_path, offline, brand):
    # "lean" renders 2 plates rather than 5: the same pipeline runs end to end,
    # without spending most of the test budget painting procedural artwork.
    spec = CrochetSpec(
        mode=mode, brand=brand, theme="boho", listing_image_count=1, cost_mode="lean",
        **_mode_kwargs(mode, tmp_path),
    )
    run_dir = tmp_path / f"run_{mode}"
    steps: list[tuple[float, str]] = []
    result = build_crochet(
        spec, out_dir=run_dir, settings=offline,
        progress=lambda message, fraction: steps.append((fraction, message)),
    )

    assert result.product_type == "crochet"
    assert result.pdf_path and result.pdf_path.exists()
    assert result.zip_path and result.zip_path.exists()
    assert result.verification["ok"], result.verification.get("errors")
    assert steps and steps[-1][0] == 1.0

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["product_type"] == "crochet"
    assert manifest["mode"] == mode
    assert manifest["pages"] > 15
    assert manifest["steps"] > 10
    assert manifest["content_source"] == "template"   # offline
    assert manifest["art_source"] == "procedural"
    assert manifest["files"]["pdfs"]["letter"]
    assert manifest["listing"]["tags"]
    assert (run_dir / "etsy_listing.json").exists()
    assert (run_dir / "etsy_listing.txt").exists()


def test_etsy_mode_builds_the_product_you_asked_for(tmp_path, offline):
    """The number typed in the form has to select that product."""
    for number, expected in ((1, "Chunky Cropped Cardigan"), (2, "Granny Square Tote"),
                             (3, "Ribbed Beanie")):
        run_dir = tmp_path / f"etsy_{number}"
        build_crochet(
            CrochetSpec(
                mode="from_etsy_data", etsy_data_text=ETSY_CSV, product_number=number,
                listing_image_count=1, cost_mode="lean",
            ),
            out_dir=run_dir, settings=offline,
        )
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["etsy_products"] == 3
        assert manifest["selected_product"]["title"] == expected
        assert manifest["title"] == expected


def test_build_rejects_an_out_of_range_product_number(tmp_path, offline):
    with pytest.raises(ValueError, match="out of range"):
        build_crochet(
            CrochetSpec(mode="from_etsy_data", etsy_data_text=ETSY_CSV, product_number=99),
            out_dir=tmp_path / "oor", settings=offline,
        )


def test_build_rejects_sources_it_cannot_read(tmp_path, offline):
    empty = tmp_path / "blank.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="could be read as text"):
        build_crochet(
            CrochetSpec(mode="from_pdfs", source_files=[str(empty)]),
            out_dir=tmp_path / "bad", settings=offline,
        )


def test_lean_cost_mode_uses_the_cheap_model_tier(tmp_path, offline):
    from artisan_forge.config import get_settings

    settings = get_settings()
    lean = settings.lean()
    assert lean.text_model == settings.cheap_text_model
    assert lean.image_model == settings.cheap_image_model
    assert lean.image_quality == "low"
    # and the spec asks for fewer plates
    assert validate(CrochetSpec(mode="from_brief", brief="a hat", cost_mode="lean")).plate_limit == 2
    assert validate(CrochetSpec(mode="from_brief", brief="a hat", cost_mode="max")).plate_limit == 5
