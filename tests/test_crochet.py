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
    DEFAULT_COST_MODE,
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


def test_a_one_image_budget_buys_the_cover(tmp_path):
    """The cover is the PDF's first page and the Etsy hero: it must win the budget."""
    pattern = expand.template_pattern(garment="cardigan")
    result = imagery.build_imagery(
        pattern, "cardigan", tmp_path, generate_art=False, use_canva=False, plate_limit=1,
    )
    assert list(result["plates"]) == ["cover"]

    # and it actually reaches the cover page
    document = CrochetPDF(pattern, plates=result["plates"])
    assert document.plates.get("cover")


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (1, ["cover"]),
        (2, ["cover", "finished"]),
        (3, ["cover", "finished", "materials"]),
    ],
)
def test_image_budget_is_spent_in_priority_order(tmp_path, limit, expected):
    pattern = expand.template_pattern(garment="cardigan")
    result = imagery.build_imagery(
        pattern, "cardigan", tmp_path / str(limit),
        generate_art=False, use_canva=False, plate_limit=limit,
    )
    assert list(result["plates"]) == expected


def test_priority_order_survives_briefs_arriving_shuffled():
    """ChatGPT returns briefs in any order; the cover still has to come first."""
    fallback = expand.default_image_briefs("cardigan", "worsted", ["cotton"])
    shuffled = {
        "image_briefs": [
            {"key": "texture", "prompt": "a macro shot of crochet fabric stitches close up"},
            {"key": "materials", "prompt": "a flat lay of yarn balls and a hook on pale linen"},
            {"key": "cover", "prompt": "a folded cardigan on pale linen with room for a title"},
        ]
    }
    ordered = imagery._by_priority(imagery.normalise_briefs(shuffled, fallback))
    assert ordered[0]["key"] == "cover"
    assert [b["key"] for b in ordered] == ["cover", "materials", "texture"]


def test_default_briefs_lead_with_the_cover():
    briefs = expand.default_image_briefs("cardigan", "worsted", ["cotton"])
    assert briefs[0]["key"] == "cover"
    # on a one-image budget the cover must show the item, not just set a mood
    assert "cardigan" in briefs[0]["prompt"]
    assert "space" in briefs[0]["prompt"].lower()   # room for the title block


def test_a_one_image_budget_buys_the_cover(tmp_path):
    """The cover is the PDF's first page and the Etsy hero: it must win the budget."""
    pattern = expand.template_pattern(garment="cardigan")
    result = imagery.build_imagery(
        pattern, "cardigan", tmp_path, generate_art=False, use_canva=False, plate_limit=1,
    )
    assert list(result["plates"]) == ["cover"]
    # and it actually reaches the cover page
    assert CrochetPDF(pattern, plates=result["plates"]).plates.get("cover")


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (1, ["cover"]),
        (2, ["cover", "finished"]),
        (3, ["cover", "finished", "materials"]),
    ],
)
def test_image_budget_is_spent_in_priority_order(tmp_path, limit, expected):
    pattern = expand.template_pattern(garment="cardigan")
    result = imagery.build_imagery(
        pattern, "cardigan", tmp_path / str(limit),
        generate_art=False, use_canva=False, plate_limit=limit,
    )
    assert list(result["plates"]) == expected


def test_priority_order_survives_briefs_arriving_shuffled():
    """ChatGPT returns briefs in any order; the cover still has to come first."""
    fallback = expand.default_image_briefs("cardigan", "worsted", ["cotton"])
    shuffled = {
        "image_briefs": [
            {"key": "texture", "prompt": "a macro shot of crochet fabric stitches close up"},
            {"key": "materials", "prompt": "a flat lay of yarn balls and a hook on pale linen"},
            {"key": "cover", "prompt": "a folded cardigan on pale linen with room for a title"},
        ]
    }
    ordered = imagery._by_priority(imagery.normalise_briefs(shuffled, fallback))
    assert ordered[0]["key"] == "cover"


def test_default_briefs_lead_with_the_cover():
    briefs = expand.default_image_briefs("cardigan", "worsted", ["cotton"])
    assert briefs[0]["key"] == "cover"
    # on a one-image budget the cover must show the item, not just set a mood
    assert "cardigan" in briefs[0]["prompt"]
    assert "space" in briefs[0]["prompt"].lower()   # room for the title block


def test_lean_run_puts_exactly_one_photo_in_the_pdf(tmp_path, offline):
    source = tmp_path / "src.txt"
    source.write_text(SOURCE_TEXT, encoding="utf-8")
    result = build_crochet(
        CrochetSpec(
            mode="from_pdfs", source_files=[str(source)], garment="cardigan",
            cost_mode="lean", listing_image_count=0,
        ),
        out_dir=tmp_path / "run", settings=offline,
    )
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert list(manifest["files"]["art"]) == ["cover"]
    # the technical diagrams are free and stay
    assert len(manifest["files"]["diagrams"]) >= 6
    assert manifest["verification"]["ok"]


# ---------------------------------------------------------------- mockups off
def test_listing_images_can_be_switched_off(tmp_path, offline):
    source = tmp_path / "src.txt"
    source.write_text(SOURCE_TEXT, encoding="utf-8")
    result = build_crochet(
        CrochetSpec(
            mode="from_pdfs", source_files=[str(source)], garment="cardigan",
            cost_mode="lean", listing_image_count=0,
        ),
        out_dir=tmp_path / "run", settings=offline,
    )
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["mockups_enabled"] is False
    assert manifest["files"]["listing_images"] == []
    assert result.listing_images == []
    assert not list((result.run_dir / "mockups").glob("*.jpg"))
    # the product is still complete without them
    assert result.pdf_path.exists() and result.zip_path.exists()
    assert manifest["verification"]["ok"]
    assert manifest["listing"]["tags"]


def test_listing_images_are_still_produced_when_asked_for(tmp_path, offline):
    source = tmp_path / "src.txt"
    source.write_text(SOURCE_TEXT, encoding="utf-8")
    result = build_crochet(
        CrochetSpec(
            mode="from_pdfs", source_files=[str(source)], garment="cardigan",
            cost_mode="lean", listing_image_count=2,
        ),
        out_dir=tmp_path / "run", settings=offline,
    )
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mockups_enabled"] is True
    assert len(manifest["files"]["listing_images"]) == 2


@pytest.mark.parametrize(("given", "clamped"), [(-5, 0), (0, 0), (3, 3), (99, 10)])
def test_listing_image_count_is_clamped_but_zero_is_allowed(given, clamped):
    spec = validate(CrochetSpec(mode="from_brief", brief="a hat", listing_image_count=given))
    assert spec.listing_image_count == clamped


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
    assert spec.cost_mode == DEFAULT_COST_MODE
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


# --------------------------------------------------------------------- costing
def test_cost_profiles_are_ordered_cheapest_first():
    from artisan_forge.products.crochet import COST_PROFILES

    estimates = [profile.estimate() for profile in COST_PROFILES.values()]
    assert estimates == sorted(estimates), "cost modes should ascend in price"
    # the default must be one of the cheap ones, not the $1 tier
    assert COST_PROFILES[DEFAULT_COST_MODE].estimate() < 0.05


def test_cost_profile_estimates_track_images_not_text():
    """Images are ~95% of a run, so the estimate must be image-driven."""
    from artisan_forge.products.crochet import COST_PROFILES

    premium = COST_PROFILES["premium"]
    assert premium.image_cost() > premium.text_cost() * 20
    free = COST_PROFILES["free"]
    assert free.image_cost() == 0.0
    assert free.estimate() == free.text_cost()


@pytest.mark.parametrize(
    ("mode", "plates", "ai_art"),
    [("free", 0, False), ("lean", 1, True), ("standard", 3, True), ("premium", 5, True)],
)
def test_cost_mode_controls_how_many_images_are_generated(mode, plates, ai_art):
    spec = validate(CrochetSpec(mode="from_brief", brief="a cardigan", cost_mode=mode))
    assert spec.plate_limit == plates
    assert spec.generate_ai_art is ai_art


def test_cost_estimate_scales_with_the_batch_and_is_free_offline():
    one = validate(CrochetSpec(mode="from_brief", brief="a hat", cost_mode="standard"))
    four = validate(
        CrochetSpec(mode="from_brief", brief="a hat", cost_mode="standard", pattern_count=4)
    )
    assert four.estimated_cost_usd() == pytest.approx(one.estimated_cost_usd() * 4, rel=0.01)

    # the diagrams-only mode never calls an API
    free = validate(CrochetSpec(mode="tech_pack", brief="a blanket", cost_mode="premium"))
    assert free.estimated_cost_usd() == 0.0


def test_unknown_cost_mode_falls_back_to_the_cheap_default():
    spec = validate(CrochetSpec(mode="from_brief", brief="a hat", cost_mode="max"))
    assert spec.cost_mode == DEFAULT_COST_MODE
    assert spec.estimated_cost_usd() < 0.05


def test_settings_tuned_pins_the_model_the_profile_asks_for():
    from artisan_forge.config import get_settings
    from artisan_forge.products.crochet import COST_PROFILES

    base = get_settings()
    for profile in COST_PROFILES.values():
        tuned = base.tuned(profile.image_model, profile.image_quality, profile.cheap_text)
        if profile.image_model:
            assert tuned.image_model == profile.image_model
        assert tuned.image_quality == profile.image_quality
        if profile.cheap_text:
            assert tuned.text_model == base.cheap_text_model


def test_image_cache_avoids_paying_twice_for_the_same_prompt(tmp_path, monkeypatch):
    """A repeat prompt must not reach the API. Images are the whole cost."""
    from artisan_forge.ai.image_client import SQUARE, ImageStudio
    from artisan_forge.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("AF_IMAGE_CACHE", "1")
    monkeypatch.setenv("AF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("AF_OFFLINE", raising=False)

    settings = get_settings()
    assert settings.image_cache

    calls: list[str] = []
    png = (
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096  # plausible size, never decoded
    )

    def fake_request(self, prompt, size):
        calls.append(prompt)
        return png

    monkeypatch.setattr(ImageStudio, "_openai_request", fake_request)

    studio = ImageStudio(settings, offline=False)
    first = studio.generate("a flat lay of yarn", tmp_path / "a.png", size=SQUARE)
    assert first.exists() and len(calls) == 1
    assert studio.generated == 1 and studio.cache_hits == 0

    # same prompt, new destination -> served from cache, no API call
    second = studio.generate("a flat lay of yarn", tmp_path / "b.png", size=SQUARE)
    assert second.exists() and len(calls) == 1
    assert studio.generated == 1 and studio.cache_hits == 1
    assert second.read_bytes() == first.read_bytes()

    # a different prompt is billed
    studio.generate("a close up of stitches", tmp_path / "c.png", size=SQUARE)
    assert len(calls) == 2 and studio.generated == 2

    # a fresh studio still benefits: the cache is on disk, not in memory
    reused = ImageStudio(settings, offline=False)
    reused.generate("a flat lay of yarn", tmp_path / "d.png", size=SQUARE)
    assert len(calls) == 2
    assert reused.cache_hits == 1 and reused.generated == 0


def test_image_cache_can_be_turned_off(tmp_path, monkeypatch):
    from artisan_forge.ai.image_client import SQUARE, ImageStudio
    from artisan_forge.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("AF_IMAGE_CACHE", "0")
    monkeypatch.setenv("AF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("AF_OFFLINE", raising=False)

    calls: list[str] = []
    monkeypatch.setattr(
        ImageStudio, "_openai_request",
        lambda self, prompt, size: calls.append(prompt) or (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096),
    )
    studio = ImageStudio(get_settings(), offline=False)
    studio.generate("same prompt", tmp_path / "a.png", size=SQUARE)
    studio.generate("same prompt", tmp_path / "b.png", size=SQUARE)
    assert len(calls) == 2, "cache should be disabled"
    assert studio.cache_hits == 0


def test_build_reports_what_it_billed(tmp_path, offline):
    source = tmp_path / "src.txt"
    source.write_text(SOURCE_TEXT, encoding="utf-8")
    result = build_crochet(
        CrochetSpec(
            mode="from_pdfs", source_files=[str(source)], cost_mode="lean",
            listing_image_count=1,
        ),
        out_dir=tmp_path / "run", settings=offline,
    )
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    cost = manifest["cost"]
    assert cost["mode"] == "lean"
    assert cost["images_billed"] == 0        # offline: procedural art, nothing billed
    assert cost["images_from_cache"] == 0
    assert cost["estimated_usd"] >= 0


# ---------------------------------------------------------------------- batch
@pytest.mark.parametrize(
    ("files", "count", "per", "expected"),
    [
        (4, 1, 0, [[0, 1, 2, 3]]),
        (4, 2, 0, [[0, 1], [2, 3]]),
        (4, 4, 0, [[0], [1], [2], [3]]),
        (4, 2, 2, [[0, 1], [2, 3]]),
        (4, 4, 1, [[0], [1], [2], [3]]),
        (3, 2, 0, [[0, 1], [2]]),
    ],
)
def test_group_sources_splits_uploads_as_asked(files, count, per, expected):
    from artisan_forge.products.crochet import group_sources

    paths = [f"f{i}.pdf" for i in range(files)]
    groups = group_sources(paths, count, per)
    assert groups == [[paths[i] for i in group] for group in expected]


def test_group_sources_always_returns_one_group_per_pattern():
    from artisan_forge.products.crochet import group_sources

    # more patterns than files: every pattern still gets something to work from
    groups = group_sources(["a.pdf", "b.pdf", "c.pdf"], 5, 0)
    assert len(groups) == 5
    assert all(group for group in groups)

    # asking for more files per pattern than exist wraps rather than truncating
    groups = group_sources(["a.pdf", "b.pdf", "c.pdf", "d.pdf"], 2, 3)
    assert len(groups) == 2
    assert all(len(group) == 3 for group in groups)

    # no uploads at all still yields the right shape
    assert group_sources([], 3, 0) == [[], [], []]


def test_batch_builds_one_distinct_pattern_per_source_file(tmp_path, offline, brand):
    """Four uploads with pattern_count=4 must give four different patterns."""
    from artisan_forge.products.crochet import build_crochet_batch

    titles = {
        "cardigan.txt": "COZY RIBBED CARDIGAN",
        "beanie.txt": "CHUNKY RIBBED BEANIE",
        "blanket.txt": "WAFFLE STITCH BLANKET",
    }
    files = []
    for name, title in titles.items():
        path = tmp_path / name
        path.write_text(
            f"{title}\nGauge: 14 sts x 12 rows = 4 x 4 in\n5 mm hook, worsted cotton\n"
            "Row 1: Ch 40, hdc in each ch. (38 hdc)\n",
            encoding="utf-8",
        )
        files.append(str(path))

    results = build_crochet_batch(
        CrochetSpec(
            mode="from_pdfs", source_files=files, brand=brand, cost_mode="lean",
            listing_image_count=1, pattern_count=3, sources_per_pattern=1,
        ),
        out_dir=tmp_path / "batch", settings=offline,
    )

    assert len(results) == 3
    # separate run folders, separate PDFs, separate filenames
    assert len({str(r.run_dir) for r in results}) == 3
    assert len({r.pdf_path.name for r in results}) == 3
    built = set()
    for result in results:
        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["verification"]["ok"], manifest["verification"]["errors"]
        assert len(manifest["sources"]) == 1  # one source file each
        built.add(manifest["title"])
    assert built == set(titles.values())


def test_batch_of_one_matches_a_single_build(tmp_path, offline):
    from artisan_forge.products.crochet import build_crochet_batch

    path = tmp_path / "src.txt"
    path.write_text(SOURCE_TEXT, encoding="utf-8")
    results = build_crochet_batch(
        CrochetSpec(mode="from_pdfs", source_files=[str(path)], cost_mode="lean",
                    listing_image_count=1, pattern_count=1),
        out_dir=tmp_path / "single", settings=offline,
    )
    assert len(results) == 1
    assert results[0].pdf_path.exists()


def test_pattern_count_is_clamped():
    spec = validate(CrochetSpec(mode="from_brief", brief="a hat", pattern_count=999))
    assert spec.pattern_count == 10
    spec = validate(CrochetSpec(mode="from_brief", brief="a hat", pattern_count=0))
    assert spec.pattern_count == 1


def test_pdf_filename_follows_the_pattern_title():
    from artisan_forge.products.crochet import _slug_from

    assert _slug_from("Cozy Ribbed Cardigan") == "cozy-ribbed-cardigan-crochet-pattern"
    assert _slug_from("Crochet Pattern") == "crochet-pattern"
    assert _slug_from("") == ""
    assert _slug_from(None) == ""


# -------------------------------------------------------------------- mockups
def test_showcase_pages_leads_with_the_visual_pages():
    """Listing grids must not open with pages of grey prose."""
    from artisan_forge.products.crochet import showcase_pages

    pages = [{"kind": k} for k in (
        "cover", "credits", "contents", "about", "materials", "yarn_guide", "gauge",
        "sizing", "abbreviations", "construction", "foundation", "chart",
        "instructions", "counts", "assembly", "seaming", "blocking",
        "troubleshooting", "care", "gallery", "thanks",
    )]
    chosen = showcase_pages(pages, 12)
    kinds = [pages[i]["kind"] for i in chosen]

    assert len(chosen) == 12
    assert len(set(chosen)) == 12                       # no repeats
    assert kinds[:4] == ["chart", "construction", "sizing", "gauge"]
    assert "gallery" in kinds
    # front matter and back matter never appear in the grid
    assert not ({"cover", "credits", "contents", "thanks"} & set(kinds))


def test_showcase_pages_handles_short_documents():
    from artisan_forge.products.crochet import showcase_pages

    pages = [{"kind": k} for k in ("cover", "credits", "instructions", "thanks")]
    chosen = showcase_pages(pages, 12)
    assert chosen == [2]  # only the one usable page, no padding with front matter

    assert showcase_pages([{"kind": "cover"}], 12) == []


def test_mockup_context_advertises_the_whole_product():
    pattern = expand.ensure_stitch_counts(expand.template_pattern(garment="cardigan"))
    pages = [{"kind": k} for k in (
        "cover", "credits", "contents", "about", "gauge", "sizing", "construction",
        "chart", "instructions", "counts", "seaming", "care", "thanks",
    )]
    from artisan_forge.products.crochet import mockup_context

    context = mockup_context(CrochetSpec(garment="cardigan"), pattern, pages)
    assert context.grid_cols * context.grid_rows == 12
    assert str(len(pages)) in context.grid_headline
    assert len(context.bullets) >= 5
    assert any("stitch count" in b.lower() for b in context.bullets)
    assert any("diagram" in b.lower() for b in context.bullets)
    assert context.scenes[1] == "bundle_grid"   # the "what you get" grid comes early
    assert context.page_indexes


def test_letter_spaced_text_shares_one_baseline():
    """Guards the mockup wobble: tracked glyphs must not each use their own bbox."""
    from PIL import Image, ImageDraw

    from artisan_forge.mockups.draw_utils import draw_text, get_font

    font = get_font(64, "bold")
    text = "Included"

    def ink_top(tracking: float) -> list[int]:
        image = Image.new("L", (900, 200), 0)
        draw = ImageDraw.Draw(image)
        draw_text(draw, (20, 60), text, font, 255, tracking=tracking)
        pixels = image.load()
        # topmost inked row for each column that has ink
        tops: list[int] = []
        for x in range(image.width):
            for y in range(image.height):
                if pixels[x, y] > 40:
                    tops.append(y)
                    break
        return tops

    plain = ink_top(0.0)
    tracked = ink_top(6.0)
    assert plain and tracked
    # The tallest glyphs must start at the same height whether tracked or not.
    assert abs(min(plain) - min(tracked)) <= 2, "tracked text shifted vertically"
    # And the run of glyph tops must not be wildly more scattered than untracked.
    assert max(tracked) - min(tracked) <= (max(plain) - min(plain)) + 3


# ------------------------------------------------------------ market research
MARKET_SAMPLE = [
    {
        "query": "crochet cardigan pattern",
        "title": "Crochet Cardigan Pattern PDF | Sizes XS-5XL | Worsted Weight | Instant Download",
        "price": 8.5, "currency": "USD", "originalPrice": 8.5, "onSale": False,
        "tags": ["crochet cardigan", "cardigan pattern", "size inclusive",
                 "plus size crochet", "worsted weight", "crochet sweater",
                 "pdf pattern", "written pattern"],
        "tagVolumes": {"crochet cardigan": 9200000, "cardigan pattern": 3100000,
                       "size inclusive": 740000, "plus size crochet": 1100000,
                       "worsted weight": 2400000, "crochet sweater": 11000000,
                       "pdf pattern": 7900000, "written pattern": 480000},
        "ehuntEstimatedSales": 1420, "favoritesCount": 1840, "reviewCount": 212,
        "imageCount": 10, "demandScore": 82, "opportunityScore": 61,
        "ehuntBestSeller": True, "isDigital": True,
    },
    {
        "query": "crochet cardigan pattern",
        "title": "Oversized Crochet Cardigan Pattern - Beginner Friendly PDF, 6 Sizes",
        "price": 6.0, "currency": "USD", "originalPrice": 12.0, "onSale": True,
        "ehuntDiscountPercent": 50,
        "tags": ["oversized cardigan", "beginner crochet", "crochet pattern",
                 "chunky cardigan", "24in1pokemon crochet"],
        "tagVolumes": {"oversized cardigan": 2100000, "beginner crochet": 18300000,
                       "crochet pattern": 46200000, "chunky cardigan": 890000,
                       "24in1pokemon crochet": 55300000},
        "ehuntEstimatedSales": 640, "favoritesCount": 720, "reviewCount": 88,
        "imageCount": 8, "demandScore": 71, "opportunityScore": 55,
    },
    {
        "query": "crochet cardigan pattern",
        "title": "200+ Amigurumi Keychain Bundle PDF Instant Download",
        "price": 1.68, "currency": "USD", "originalPrice": 6.7, "onSale": True,
        "ehuntDiscountPercent": 75,
        "tags": ["amigurumi keychain", "crochet keychain", "crochet bundle"],
        "tagVolumes": {"amigurumi keychain": 4300000, "crochet keychain": 5600000,
                       "crochet bundle": 4400000},
        "ehuntEstimatedSales": 0, "favoritesCount": 110, "imageCount": 8,
        "demandScore": 55, "opportunityScore": 38,
    },
]


@pytest.mark.parametrize("fmt", ["json", "results", "jsonl", "csv"])
def test_market_data_reads_every_text_format(fmt):
    from artisan_forge.crochet import market

    if fmt == "json":
        payload = json.dumps(MARKET_SAMPLE)
    elif fmt == "results":
        payload = json.dumps({"results": MARKET_SAMPLE})
    elif fmt == "jsonl":
        payload = "\n".join(json.dumps(row) for row in MARKET_SAMPLE)
    else:
        payload = (
            "title,price,currency,tags,favoritesCount,ehuntEstimatedSales,demandScore\n"
            '"Crochet Cardigan Pattern PDF",8.50,USD,"crochet cardigan,pdf pattern",1840,1420,82\n'
        )

    listings, warnings = market.load_market_data(payload)
    assert listings and not warnings
    assert listings[0].title.startswith("Crochet Cardigan") or listings[0].title
    assert listings[0].price


def test_market_data_reads_uploaded_files(tmp_path):
    from artisan_forge.crochet import market

    (tmp_path / "a.json").write_text(json.dumps(MARKET_SAMPLE), encoding="utf-8")
    (tmp_path / "b.jsonl").write_text(
        "\n".join(json.dumps(r) for r in MARKET_SAMPLE), encoding="utf-8"
    )
    listings, warnings = market.load_market_data(
        "", [tmp_path / "a.json", tmp_path / "b.jsonl"]
    )
    assert len(listings) == len(MARKET_SAMPLE) * 2
    assert not warnings


def test_market_data_reads_excel(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from artisan_forge.crochet import market

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["title", "price", "tags", "favoritesCount", "ehuntEstimatedSales"])
    sheet.append(["Crochet Tote Pattern PDF", 6.0, "tote pattern,crochet bag", 210, 300])
    path = tmp_path / "scrape.xlsx"
    workbook.save(path)

    listings, warnings = market.load_market_data("", [path])
    assert not warnings
    assert len(listings) == 1
    assert listings[0].title == "Crochet Tote Pattern PDF"
    assert listings[0].price == 6.0
    assert "tote pattern" in listings[0].tags


def test_market_data_reports_unreadable_input():
    from artisan_forge.crochet import market

    listings, warnings = market.load_market_data("not json at all {{{")
    assert not listings
    assert warnings and "could not be parsed" in warnings[0]

    # a missing file is reported, not raised
    listings, warnings = market.load_market_data("", ["/nope/missing.json"])
    assert not listings and warnings


def test_market_analysis_extracts_pricing_strategy():
    from artisan_forge.crochet import market

    listings, _ = market.load_market_data(json.dumps(MARKET_SAMPLE))
    report = market.analyse(listings, relevance="cardigan worsted")

    assert report.listings == 3
    assert report.price_min == 1.68 and report.price_max == 8.5
    assert report.currency == "USD"
    assert report.sale_share == pytest.approx(0.67, abs=0.01)
    assert report.discount_median == 62.5
    # priced off the winners, not the whole field
    assert report.suggested_price and 5.0 <= report.suggested_price <= 8.5
    # the market discounts heavily, so a list price is proposed to match
    assert report.suggested_list_price and report.suggested_list_price > report.suggested_price
    assert report.image_count_median == 8
    assert report.demand_mean and report.opportunity_mean
    assert report.top_performers[0].estimated_sales == 1420


def test_market_tags_are_ranked_by_volume_and_performance():
    from artisan_forge.crochet import market

    listings, _ = market.load_market_data(json.dumps(MARKET_SAMPLE))
    report = market.analyse(listings, relevance="cardigan worsted cotton")
    tags = report.best_tags()

    assert 0 < len(tags) <= 13
    assert all(len(tag) <= 20 for tag in tags)
    assert all(tag == tag.lower() for tag in tags)
    # the highest-volume on-niche tag leads
    assert "crochet cardigan" in tags[:4]
    # volume is recorded alongside the tag
    cardigan = next(t for t in report.tags if t.tag == "crochet cardigan")
    assert cardigan.volume == 9200000


@pytest.mark.parametrize(
    ("tag", "spam"),
    [
        ("24in1pokemon crochet", True),
        ("12in1bundle pack", True),
        ("3xl cardigan", False),
        ("8mm hook", False),
        ("4ply yarn", False),
        ("crochet pattern", False),
        ("size 3xl", False),
    ],
)
def test_keyword_stuffed_tags_are_detected(tag, spam):
    from artisan_forge.crochet import market

    assert market.looks_like_spam(tag) is spam


def test_keyword_stuffed_tags_never_reach_the_listing():
    from artisan_forge.crochet import market

    listings, _ = market.load_market_data(json.dumps(MARKET_SAMPLE))
    report = market.analyse(listings, relevance="cardigan")
    assert not any("24in1" in tag for tag in report.best_tags())
    assert not any("24in1" in insight.tag for insight in report.tags)


def test_tag_relevance_keeps_the_listing_on_niche():
    from artisan_forge.crochet import market

    cardigan = market._expand_families(market._relevance_tokens("cardigan worsted"))
    # names the product, or a synonym of it
    assert market.tag_relevance("crochet cardigan", cardigan) == 1.0
    assert market.tag_relevance("crochet sweater", cardigan) == 1.0
    # generic craft language is always fine
    assert market.tag_relevance("pdf pattern", cardigan) == 1.0
    assert market.tag_relevance("instant download", cardigan) == 1.0
    # same category, still plausible
    assert market.tag_relevance("crochet top", cardigan) == 0.7
    # a different product category entirely
    assert market.tag_relevance("crochet keychain", cardigan) < 0.5
    assert market.tag_relevance("amigurumi pokemon", cardigan) < 0.5
    # with no product context nothing is filtered
    assert market.tag_relevance("crochet keychain", set()) == 1.0


def test_off_niche_tags_are_excluded_from_the_tag_set():
    from artisan_forge.crochet import market

    listings, _ = market.load_market_data(json.dumps(MARKET_SAMPLE))
    report = market.analyse(listings, relevance="cardigan worsted cotton")
    tags = report.best_tags()

    assert not any("keychain" in tag for tag in tags), tags
    assert any("cardigan" in tag or "sweater" in tag for tag in tags)
    # they are still recorded, just not selected
    assert any("keychain" in insight.tag for insight in report.tags)
    assert "crochet keychain" in report.best_tags(limit=30, on_niche_only=False)


def test_market_brief_is_bounded_and_informative():
    from artisan_forge.crochet import market

    listings, _ = market.load_market_data(json.dumps(MARKET_SAMPLE))
    report = market.analyse(listings, relevance="cardigan")
    brief = market.market_brief(report, limit=3000)

    assert len(brief) <= 3000
    assert "MARKET RESEARCH" in brief
    assert "HIGHEST VALUE TAGS" in brief
    assert "BEST PERFORMING COMPETITORS" in brief
    assert "crochet cardigan" in brief
    # an empty report produces nothing rather than a stub
    assert market.market_brief(market.analyse([])) == ""


def test_market_listing_normaliser_enforces_etsy_limits():
    from artisan_forge.crochet import market

    listings, _ = market.load_market_data(json.dumps(MARKET_SAMPLE))
    report = market.analyse(listings, relevance="cardigan")
    fallback = {
        "title": "fallback title", "tags": ["crochet cardigan"],
        "description": "d" * 300, "suggested_price_usd": 7.5, "materials": ["PDF"],
    }
    listing = market.normalise_listing(
        {
            "title": "X" * 300,
            "tags": ["Crochet Cardigan!!", "A" * 40, "dupe", "dupe", ""],
            "description": "too short",
            "suggested_price_usd": "about 9.99 dollars",
            "list_price_usd": 24.0,
            "reasoning": "long tail keywords",
        },
        report, fallback,
    )

    assert len(listing["title"]) <= 140
    assert len(listing["tags"]) <= 13
    assert all(len(tag) <= 20 for tag in listing["tags"])
    assert len(set(listing["tags"])) == len(listing["tags"])   # no duplicates
    assert listing["description"] == fallback["description"]   # model's was too short
    assert listing["suggested_price_usd"] == 9.99
    assert listing["list_price_usd"] == 24.0
    assert listing["keyword_reasoning"] == "long tail keywords"

    # garbage in, fallback out
    assert market.normalise_listing(None, report, fallback) == fallback


def test_market_research_drives_the_built_listing(tmp_path, offline):
    """The whole point: research must change the tags and the price."""
    source = tmp_path / "cardigan.txt"
    source.write_text(SOURCE_TEXT, encoding="utf-8")
    research = tmp_path / "market.json"
    research.write_text(json.dumps(MARKET_SAMPLE), encoding="utf-8")

    result = build_crochet(
        CrochetSpec(
            mode="from_pdfs", source_files=[str(source)],
            market_files=[str(research)], garment="cardigan",
            listing_image_count=1, cost_mode="lean",
        ),
        out_dir=tmp_path / "run", settings=offline,
    )
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    listing = manifest["listing"]
    report = manifest["market"]

    assert report and report["listings"] == 3
    # price comes from the competitor analysis, not the cost-mode default
    assert listing["suggested_price_usd"] == report["suggested_price"]
    assert listing["list_price_usd"] == report["suggested_list_price"]
    # tags are research-led, on-niche and within Etsy's limits
    assert len(listing["tags"]) <= 13
    assert all(len(tag) <= 20 for tag in listing["tags"])
    assert any("cardigan" in tag or "sweater" in tag for tag in listing["tags"])
    assert not any("keychain" in tag or "pokemon" in tag for tag in listing["tags"])
    assert manifest["verification"]["ok"]


def test_build_without_market_data_is_unchanged(tmp_path, offline):
    source = tmp_path / "cardigan.txt"
    source.write_text(SOURCE_TEXT, encoding="utf-8")

    result = build_crochet(
        CrochetSpec(
            mode="from_pdfs", source_files=[str(source)], garment="cardigan",
            listing_image_count=1, cost_mode="lean",
        ),
        out_dir=tmp_path / "run", settings=offline,
    )
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["market"] is None
    assert manifest["listing"]["tags"]           # still produces a full listing
    assert manifest["listing"]["suggested_price_usd"] > 0
    assert "list_price_usd" not in manifest["listing"]
