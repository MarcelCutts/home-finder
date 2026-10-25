"""Notes on the listing text."""

from home_finder.web.annotate import ADJECTIVE_NOTE, annotate_listing

TEXT = (
    "A stunning two bedroom apartment on the eighth floor of this sought-after development. "
    "The property boasts a spacious reception leading to a private balcony, a modern fitted "
    "kitchen, a generous master bedroom and a second bedroom. Immaculate throughout."
)
ANALYSIS = {
    "lowlights": ["Potential traffic noise"],
    "flooring_noise": {"noise_indicators": ["Railway line directly beside the building."]},
    "kitchen": {"has_dishwasher": "no", "has_washing_machine": "unknown"},
    "value": {"area_average": 2150, "difference": 350},
    "listing_red_flags": {"missing_room_photos": ["second bedroom"]},
}


def marked(result):
    return [(s["text"], s["mark"], s["note"]) for s in result["segments"] if s["mark"]]


def check_invariants(text, result):
    assert "".join(s["text"] for s in result["segments"]) == text
    referenced = {s["note"] for s in result["segments"] if s["note"]}
    assert referenced == set(range(1, len(result["notes"]) + 1)), "every note anchored, once"


def test_notes_are_numbered_in_text_order_and_name_their_source() -> None:
    result = annotate_listing(TEXT, ANALYSIS, outcode="E3", price_pcm=2500)
    check_invariants(TEXT, result)
    assert result["notes"] == [
        ADJECTIVE_NOTE,
        "£350 pcm above the stored E3 reference of £2,150 (unverified, historical).",
        "Photo assessment: Railway line directly beside the building. Check noise at a viewing.",
        "Photo assessment: no dishwasher seen.",
        "Second bedroom not seen in the supplied photos.",
    ]
    assert marked(result) == [
        ("stunning", "strike", 1),
        ("sought-after development", "wavy", 2),
        ("private balcony", "wavy", 3),
        ("modern fitted kitchen", "wavy", 4),
        ("second bedroom", "wavy", 5),
        ("Immaculate", "strike", 1),
    ]


def test_nothing_to_say_leaves_the_text_untouched() -> None:
    plain = "Two bedroom flat with a balcony and a fitted kitchen."
    result = annotate_listing(plain, {"kitchen": {"has_dishwasher": "yes"}})
    assert result["notes"] == []
    assert result["segments"] == [{"text": plain, "mark": None, "note": None}]


def test_noise_needs_the_typed_lowlight_and_never_claims_an_omission() -> None:
    # Prose alone, even negative, does not mark the balcony: it may be a reassurance.
    reassuring = {"flooring_noise": {"noise_indicators": ["Quiet courtyard; no traffic seen."]}}
    assert annotate_listing("A private balcony.", reassuring)["notes"] == []
    # With the typed lowlight, the note reports what was seen and says nothing about
    # the listing, which here already discloses the railway.
    disclosed = "A balcony overlooking the railway line directly beside the building."
    result = annotate_listing(disclosed, ANALYSIS)
    check_invariants(disclosed, result)
    assert result["notes"] == [
        "Photo assessment: Railway line directly beside the building. Check noise at a viewing."
    ]
    assert "mentioned" not in result["notes"][0]
    # The lowlight without any indicator still gets a neutral note.
    bare = annotate_listing("Flat with a balcony.", {"lowlights": ["Potential traffic noise"]})
    assert bare["notes"] == [
        "Photo assessment: possible road or rail noise. Check noise at a viewing."
    ]


def test_value_note_keeps_the_reference_qualified_and_uses_the_current_rent() -> None:
    analysis = {"value": {"area_average": 2350, "difference": 1150}}
    text = "A sought-after area."
    assert annotate_listing(text, analysis, outcode="E8")["notes"] == [
        "£1,150 pcm above the stored E8 reference of £2,350 (unverified, historical)."
    ]
    # The asking rent has since dropped to the reference: nothing to say.
    assert annotate_listing(text, analysis, outcode="E8", price_pcm=2350)["notes"] == []
    # No stored reference, no note, whatever the saved difference says.
    assert annotate_listing(text, {"value": {"difference": 400}})["notes"] == []


def test_overlapping_rules_keep_the_specific_finding_and_lose_no_note() -> None:
    kitchen = {
        "kitchen": {"has_dishwasher": "no"},
        "listing_red_flags": {"missing_room_photos": ["kitchen"]},
    }
    text = "A luxurious fitted kitchen."
    result = annotate_listing(text, kitchen)
    check_invariants(text, result)
    # Two findings compete for "kitchen"; the first by position wins, the adjective is
    # marked separately, and no note is left without an anchor.
    assert marked(result) == [("luxurious", "strike", 1), ("fitted kitchen", "wavy", 2)]
    assert result["notes"] == [ADJECTIVE_NOTE, "Photo assessment: no dishwasher seen."]


def test_works_with_the_analysis_model_as_well_as_a_dict(make_quality_analysis) -> None:
    analysis = make_quality_analysis(lowlights=["Potential traffic noise"])
    result = annotate_listing("A lovely flat with a private terrace.", analysis)
    assert result["notes"][0] == ADJECTIVE_NOTE
    assert result["notes"][1].startswith("Photo assessment:")
