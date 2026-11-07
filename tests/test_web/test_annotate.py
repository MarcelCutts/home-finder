"""The listing's words: which advert is shown, cleaned, and the notes on it."""

import pytest
from pydantic import HttpUrl

from home_finder.analysis.listing_text import select_listing_text
from home_finder.models import ListingTextEvidence, MergedProperty, Property, PropertySource
from home_finder.web.annotate import annotate_listing, choose_advert_text, clean_advert_text

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
RIGHTMOVE, ZOOPLA, OPENRENT = (
    PropertySource.RIGHTMOVE,
    PropertySource.ZOOPLA,
    PropertySource.OPENRENT,
)


def marked(result):
    return [(s["text"], s["note"]) for s in result["segments"] if s["note"]]


def check_invariants(text, result):
    assert "".join(s["text"] for s in result["segments"]) == text
    referenced = [s["note"] for s in result["segments"] if s["note"]]
    assert referenced == list(range(1, len(result["notes"]) + 1)), "each note anchored once"


# Cleaning the portal's markup


@pytest.mark.parametrize("tag", ["<br>", "<br/>", "<br />", "<BR>", "<br\n/>", "</br>"])
def test_every_spelling_of_a_line_break_is_one_newline(tag) -> None:
    assert clean_advert_text(f"Gas hob{tag}Garden") == "Gas hob\nGarden"


def test_paragraphs_and_lists_keep_their_shape_and_other_tags_go() -> None:
    advert = (
        '<p class="intro">A <b>bright</b> flat.</p><p>Close to the <i>station</i>.</p>'
        "<br /><br /><br /><u>Features</u>:<ul><li>Gas hob</li><li>Garden</li></ul>"
    )
    assert clean_advert_text(advert) == (
        "A bright flat.\n\nClose to the station.\n\nFeatures:\nGas hob\nGarden"
    )


def test_table_cells_and_rules_separate_their_words() -> None:
    advert = "<table><tr><td>Gas hob</td><td>Garden</td></tr></table>Bedrooms: 2<hr>Baths: 1"
    assert clean_advert_text(advert) == "Gas hob\nGarden\nBedrooms: 2\nBaths: 1"


def test_newlines_beside_break_tags_do_not_double_the_break() -> None:
    # Portals send "\r" beside their "<br/>"; on the portal it is only whitespace.
    assert clean_advert_text("Lovely flat.\r\n<br/>Close   to the park.") == (
        "Lovely flat.\nClose to the park."
    )


def test_an_advert_without_break_tags_keeps_its_own_newlines() -> None:
    assert clean_advert_text("First paragraph.\r\n\r\nSecond, <b>bold</b>.") == (
        "First paragraph.\n\nSecond, bold."
    )


def test_plain_paragraphs_survive_a_stray_break_tag() -> None:
    advert = "Para one.\n\nPara two.\n\nFeatures:<br>Gas hob"
    assert clean_advert_text(advert) == "Para one.\n\nPara two.\n\nFeatures:\nGas hob"


def test_spaces_either_side_of_an_inline_tag_become_one() -> None:
    text = clean_advert_text("A private </b> <b>balcony.")
    assert text == "A private balcony."
    # The single space lets the whole phrase carry its note, not just "balcony".
    noisy = {"lowlights": ["Potential traffic noise"]}
    assert marked(annotate_listing(text, noisy)) == [("private balcony", 1)]


def test_an_empty_script_or_style_tag_hides_nothing() -> None:
    assert clean_advert_text("Flat <script/>with a <style/>garden") == "Flat with a garden"


def test_lines_holding_only_non_breaking_spaces_count_as_blank() -> None:
    assert clean_advert_text("Flat.\r\n&nbsp;\r\n&nbsp;\r\nGarden.") == "Flat.\n\nGarden."


def test_entities_are_decoded_once_and_never_become_tags() -> None:
    advert = "Bills &amp; council tax&nbsp;extra. Shown as &lt;br /&gt;, rent &lt; £2,000"
    assert clean_advert_text(advert) == (
        "Bills & council tax\xa0extra. Shown as <br />, rent < £2,000"
    )


def test_a_lone_angle_bracket_in_prose_is_kept() -> None:
    assert clean_advert_text("Rent < £2,000 & 2<3 bedrooms") == "Rent < £2,000 & 2<3 bedrooms"


def test_scripts_styles_and_comments_leave_nothing_behind() -> None:
    advert = '<style>p{}</style>Flat<script>alert("xss")</script> <!-- note -->to let'
    assert clean_advert_text(advert) == "Flat to let"


# Choosing the advert


def evidence(**descriptions: str) -> dict[PropertySource, ListingTextEvidence]:
    return {
        PropertySource(source): ListingTextEvidence(description=text)
        for source, text in descriptions.items()
    }


def analysis_that_read(adverts: dict[PropertySource, ListingTextEvidence]) -> dict:
    """An analysis whose kept inputs are what select_listing_text records for these adverts."""
    first = next(iter(adverts))
    home = MergedProperty(
        canonical=Property(
            source=first,
            source_id="1",
            url=HttpUrl("https://example.com/1"),
            title="Flat",
            price_pcm=2000,
            bedrooms=2,
            address="1 Example Street",
        ),
        sources=tuple(adverts),
        source_urls={source: HttpUrl(f"https://example.com/{source}/1") for source in adverts},
        min_price=2000,
        max_price=2000,
        source_text=adverts,
    )
    return {"listing_text_inputs": select_listing_text(home)}


def test_the_canonical_source_wins_over_a_longer_advert() -> None:
    adverts = evidence(rightmove="Short advert.", zoopla="A much longer advert " * 10)
    assert (
        choose_advert_text(adverts, canonical_source="rightmove", analysis=None) == "Short advert."
    )


def test_text_the_analysis_read_wins_over_the_canonical_source() -> None:
    adverts = evidence(rightmove="Canonical advert.", zoopla="Read advert.")
    analysis = analysis_that_read(evidence(zoopla="Read advert."))
    assert choose_advert_text(adverts, canonical_source="rightmove", analysis=analysis) == (
        "Read advert."
    )


def test_text_changed_since_the_analysis_read_it_counts_as_unread() -> None:
    read = analysis_that_read(
        {
            RIGHTMOVE: ListingTextEvidence(description="Rightmove's words, as read."),
            # The analysis saw only Zoopla's features; its description came later.
            ZOOPLA: ListingTextEvidence(features=("Gas hob",)),
        }
    )
    now = {
        RIGHTMOVE: ListingTextEvidence(description="Rightmove's words, as read."),
        ZOOPLA: ListingTextEvidence(description="Added after the analysis.", features=("Gas hob",)),
    }
    assert choose_advert_text(now, canonical_source="zoopla", analysis=read) == (
        "Rightmove's words, as read."
    )


def test_among_sources_read_the_canonical_one_wins_then_source_name() -> None:
    adverts = evidence(openrent="OpenRent.", rightmove="Rightmove.", zoopla="Zoopla.")
    all_read = analysis_that_read(adverts)
    assert choose_advert_text(adverts, canonical_source="zoopla", analysis=all_read) == "Zoopla."
    assert choose_advert_text(adverts, canonical_source=None, analysis=all_read) == "OpenRent."


def test_an_advert_blank_once_cleaned_is_passed_over() -> None:
    adverts = evidence(rightmove="<br /><br />", zoopla="Zoopla's words.")
    assert choose_advert_text(adverts, canonical_source="rightmove", analysis=None) == (
        "Zoopla's words."
    )


def test_with_no_advert_left_the_text_is_empty() -> None:
    blank = evidence(rightmove="<br /><br />", zoopla="   ")
    assert choose_advert_text(blank, canonical_source="rightmove", analysis=None) == ""
    assert choose_advert_text({}, canonical_source="rightmove", analysis=None) == ""


def test_the_chosen_advert_comes_back_cleaned() -> None:
    adverts = evidence(rightmove="Bright flat.<br />Gas &amp; electric.")
    assert choose_advert_text(adverts, canonical_source="rightmove", analysis=None) == (
        "Bright flat.\nGas & electric."
    )


# Notes on the advert


def test_notes_are_numbered_in_text_order_and_name_their_source() -> None:
    result = annotate_listing(TEXT, ANALYSIS, outcode="E3", price_pcm=2500)
    check_invariants(TEXT, result)
    assert result["notes"] == [
        "£350 pcm above the stored E3 reference of £2,150 (unverified, historical).",
        "Photo assessment: Railway line directly beside the building. Check noise at a viewing.",
        "Photo assessment: no dishwasher seen.",
        "Second bedroom not seen in the supplied photos.",
    ]
    # The agent's adjectives ("stunning", "Immaculate") carry no mark of their own.
    assert marked(result) == [
        ("sought-after development", 1),
        ("private balcony", 2),
        ("modern fitted kitchen", 3),
        ("second bedroom", 4),
    ]


def test_spans_mark_the_words_of_the_cleaned_text() -> None:
    text = clean_advert_text("Bright flat.<br /><br /><b>Private balcony</b> &amp; garden.")
    result = annotate_listing(text, ANALYSIS)
    check_invariants(text, result)
    assert marked(result) == [("Private balcony", 1)]


def test_nothing_to_say_leaves_the_text_untouched() -> None:
    plain = "A stunning two bedroom flat with a balcony and a fitted kitchen."
    result = annotate_listing(plain, {"kitchen": {"has_dishwasher": "yes"}})
    assert result["notes"] == []
    assert result["segments"] == [{"text": plain, "note": None}]


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


def test_overlapping_findings_keep_the_first_and_leave_no_note_unanchored() -> None:
    kitchen = {
        "kitchen": {"has_dishwasher": "no"},
        "listing_red_flags": {"missing_room_photos": ["kitchen", "kitchen"]},
    }
    text = "A luxurious fitted kitchen."
    result = annotate_listing(text, kitchen)
    check_invariants(text, result)
    # Two findings, and a repeated room, compete for "kitchen": the first by position
    # keeps its note, the others are dropped, and no note is left without an anchor.
    assert marked(result) == [("fitted kitchen", 1)]
    assert result["notes"] == ["Photo assessment: no dishwasher seen."]


def test_works_with_the_analysis_model_as_well_as_a_dict(make_quality_analysis) -> None:
    analysis = make_quality_analysis(lowlights=["Potential traffic noise"])
    result = annotate_listing("A lovely flat with a private terrace.", analysis)
    assert marked(result) == [("private terrace", 1)]
    assert result["notes"][0].startswith("Photo assessment:")
