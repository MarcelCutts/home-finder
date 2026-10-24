"""Red-pen notes on the listing text."""

from home_finder.web.annotate import annotate_listing

TEXT = (
    "A stunning two bedroom apartment on the eighth floor of this sought-after development. "
    "The property boasts a spacious reception leading to a private balcony, a modern fitted "
    "kitchen, a generous master bedroom and a second bedroom. Immaculate throughout."
)


def marked(result):
    return [(s["text"], s["mark"], s["note"]) for s in result["segments"] if s["mark"]]


def test_every_rule_fires_once_and_the_notes_are_numbered_in_text_order() -> None:
    analysis = {
        "lowlights": ["Potential traffic noise"],
        "flooring_noise": {"noise_indicators": ["Railway line directly beside the building."]},
        "kitchen": {"has_dishwasher": "no", "has_washing_machine": "unknown"},
        "value": {"difference": 350},
        "listing_red_flags": {"missing_room_photos": ["second bedroom"]},
    }
    result = annotate_listing(TEXT, analysis, outcode="E3")
    assert result["notes"] == [
        "Adjectives, not facts.",
        "Railway line directly beside the building. Not mentioned in the listing.",
        "£350 pcm above the E3 average.",
        "No dishwasher in the photos.",
        "Second bedroom not photographed.",
    ]
    assert marked(result) == [
        ("stunning", "strike", 1),
        ("sought-after development", "wavy", 3),
        ("private balcony", "wavy", 2),
        ("modern fitted kitchen", "wavy", 4),
        ("second bedroom", "wavy", 5),
        ("Immaculate", "strike", 1),
    ]
    assert "".join(s["text"] for s in result["segments"]) == TEXT


def test_nothing_to_say_leaves_the_text_untouched() -> None:
    plain = "Two bedroom flat with a balcony and a fitted kitchen."
    result = annotate_listing(
        plain, {"kitchen": {"has_dishwasher": "yes"}, "value": {"difference": -50}}
    )
    assert result["notes"] == []
    assert result["segments"] == [{"text": plain, "mark": None, "note": None}]


def test_a_claim_is_only_marked_when_the_analysis_answers_it() -> None:
    # A balcony with no noise finding, and prestige wording at the area average: no notes.
    result = annotate_listing("Sought-after flat with a balcony.", {"value": {"difference": 0}})
    assert result["notes"] == []
    # A generic traffic lowlight without an indicator still gets a note.
    noisy = annotate_listing("Flat with a balcony.", {"lowlights": ["Potential traffic noise"]})
    assert noisy["notes"] == ["Potential road or rail noise in the photos."]


def test_works_with_the_analysis_model_as_well_as_a_dict(make_quality_analysis) -> None:
    analysis = make_quality_analysis(lowlights=["Potential traffic noise"])
    result = annotate_listing("A lovely flat with a private terrace.", analysis)
    assert result["notes"][0] == "An adjective, not a fact."
    assert any(note.startswith("Potential road or rail noise") for note in result["notes"])
