"""Listing detail page (GET /property/{id}, rendered by property_detail into detail.html).

The page puts the home first: photos, then title and rent, with the tool's take beside
them, then the assessment, the viewing checklist, the advert's words and the rest.
The costs section and the floorplans have their own modules.
"""

from datetime import UTC, datetime

import pytest
from bs4 import BeautifulSoup, Tag
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from home_finder.analysis.listing_text import select_listing_text
from home_finder.db.storage import PropertyStorage
from home_finder.filters.fit_score import fit_tier_label
from home_finder.models import (
    FlooringNoiseAnalysis,
    ListingExtraction,
    ListingRedFlags,
    ListingTextEvidence,
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    PropertyType,
    UserStatus,
    ViewingNotes,
)
from home_finder.models.analysis import ImageReviewCoverage


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _landmark_name(el: Tag) -> str:
    """A page landmark by its id, else by its first class; the title by its tag."""
    return "h1" if el.name == "h1" else el.get("id") or el["class"][0]


def _single_source(prop: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop,
        sources=(prop.source,),
        source_urls={prop.source: prop.url},
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
    )


class TestDetailPage:
    async def test_found(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    def test_not_found(self, client: TestClient) -> None:
        resp = client.get("/property/nonexistent:999")
        assert resp.status_code == 404
        assert "Property not found" in resp.text
        assert "Back to Dashboard" in resp.text

    @pytest.mark.parametrize("saved_count", [3, 0])
    async def test_every_red_flag_is_listed_and_photo_gaps_are_unknowns(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
        saved_count: int,
    ) -> None:
        """The saved count can leave out photo flags, so it decides nothing shown.

        What the photos leave out is listed as unknown, not as something wrong.
        """
        flags = ListingRedFlags(
            missing_room_photos=["Second bedroom"],
            selective_angles=True,
            description_concerns=["Railway beside the building", "Price above average"],
            red_flag_count=saved_count,
        )
        analysis = base_analysis.model_copy(
            update={
                "listing_red_flags": flags,
                "lowlights": ["Potential traffic noise", "Missing key photos"],
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)
        assert [li.get_text() for li in soup.select(".verdict-watch li")] == [
            "Potential road or rail noise",
            "Railway beside the building",
            "Price above average",
        ]
        assert [li.get_text() for li in soup.select(".verdict-find li")][:2] == [
            "Limited views in supplied photos",
            "Not seen in supplied photos: Second bedroom",
        ]

    async def test_home_comes_first_and_the_judgement_is_stated_once(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Photos, then title and rent, then the take; the sections follow in reading order."""
        analysis = base_analysis.model_copy(
            update={"viewing_notes": ViewingNotes(check_items=["Run every tap."])}
        )
        await storage.save_merged_property(merged_a, commute_minutes=22)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)

        landmarks = soup.select(
            "#section-photos, h1, .listing-rent, .take, #section-nav, #section-overview, "
            "#section-viewing, #section-description, #section-quality, #section-costs, "
            "#section-location, #section-area"
        )
        assert [_landmark_name(el) for el in landmarks] == [
            "section-photos",
            "h1",
            "listing-rent",
            "take",
            "section-nav",
            "section-overview",
            "section-viewing",
            "section-description",
            "section-quality",
            "section-costs",
            "section-location",
            "section-area",
        ]
        # The section links follow the same order; the photos are above them.
        assert [a["href"] for a in soup.select("#section-nav a")] == [
            "#section-overview",
            "#section-viewing",
            "#section-description",
            "#section-quality",
            "#section-costs",
            "#section-location",
            "#section-area",
            "#top",
        ]

        # No judgement ahead of the photos, and each statement of it appears once:
        # the lead and the score in the take, the brief's lines in the assessment.
        judgement = ["detail-lead", "fit-brief-score", "brief-check", "fit-working"]
        assert soup.select_one("#section-photos").find_previous(class_=judgement) is None
        for name in judgement:
            assert len(soup.select(f".{name}")) == 1
        take = soup.select_one(".take")
        assert take.select_one(".detail-lead") is not None
        assert take.select_one(".fit-brief-score") is not None
        assert soup.select_one("#section-overview .brief-check") is not None

    async def test_header_gives_rent_beside_the_title_and_three_facts(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a, commute_minutes=22)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        header = _soup(client.get(f"/property/{merged_a.unique_id}").text).select_one(
            ".detail-header"
        )

        assert header.h1.get_text() == "1 bed in E8"
        assert " ".join(header.select_one(".listing-rent").get_text(" ").split()) == (
            "Rent £1,900 pcm"
        )
        facts = {
            fact.dt.get_text(strip=True): " ".join(fact.dd.get_text(" ").split())
            for fact in header.select(".detail-facts .detail-fact")
        }
        # Rent and fit are no longer cells: one sits beside the title, the other in the take.
        assert list(facts) == ["Bedrooms", "Floor area", "Commute"]
        assert facts["Bedrooms"] == "1"
        assert facts["Floor area"] == "Not listed"
        assert facts["Commute"].startswith("22 min")
        assert header.select_one(".status-selector") is not None

    async def test_assessment_lists_what_fits_what_doesnt_and_whats_unknown(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        highlights = ["Gas hob", "Dishwasher included", "Modern kitchen", "Private garden"]
        analysis = base_analysis.model_copy(
            update={
                "one_line": "Garden flat with a gas hob",
                "highlights": [*highlights, "High ceilings", "Period features"],
                "lowlights": ["Service charge unstated"],
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        verdict = _soup(client.get(f"/property/{merged_a.unique_id}").text).select_one(
            "#section-overview"
        )

        assert verdict.h2.get_text() == "Garden flat with a gas hob"
        assert verdict.select_one(".verdict-summary").get_text() == "Nice place."
        fits = verdict.select_one(".verdict-fits")
        assert [li.get_text() for li in fits.select(":scope > ul li")] == [
            *highlights,
            "High ceilings",
        ]
        assert fits.select_one(".verdict-more summary").get_text() == "1 more"
        assert verdict.select_one(".verdict-watch .verdict-empty").get_text() == "Nothing flagged."
        # The service charge is the landlord's cost, so the unstated-charge lowlight is dropped.
        find_out = [li.get_text() for li in verdict.select(".verdict-find li")]
        assert "Service charge" not in find_out
        headings = [h3.get_text(strip=True) for h3 in verdict.select(".verdict-list h3")]
        assert headings == ["What fits", "What doesn't", "What's unknown"]
        assert verdict.select_one("#section-fit h3").get_text() == "Your brief"

    async def test_description_rendered(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "A nice flat." in resp.text

    async def test_xss_in_description_escaped(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """A script tag is dropped with its contents; an encoded one is shown, escaped.

        Both renderings are covered: plain without an analysis, annotated with one.
        """
        merged = MergedProperty(
            canonical=prop_a,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_a.url},
            min_price=1900,
            max_price=1900,
            descriptions={
                PropertySource.OPENRENT: (
                    'Flat with a balcony.<script>alert("xss")</script> Shown: '
                    "&lt;script&gt;alert(1)&lt;/script&gt;"
                )
            },
        )
        await storage.save_merged_property(merged)
        plain = client.get(f"/property/{merged.unique_id}").text

        noisy = base_analysis.model_copy(update={"lowlights": ["Potential traffic noise"]})
        await storage.save_quality_analysis(prop_a.unique_id, noisy)
        annotated = client.get(f"/property/{merged.unique_id}").text
        assert 'class="mark-wavy">balcony</span>' in annotated

        for page in (plain, annotated):
            assert "<script>alert" not in page
            assert "xss" not in page
            assert ". Shown: &lt;script&gt;alert(1)&lt;/script&gt;" in page

    async def test_studio_detail_page_is_not_zero_bed(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_studio: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_studio)
        resp = client.get(f"/property/{merged_studio.unique_id}")
        soup = _soup(resp.text)
        bedrooms = next(
            fact.dd for fact in soup.select(".detail-fact") if fact.dt.get_text() == "Bedrooms"
        )
        assert bedrooms.get_text(strip=True) == "Studio"
        for meta in ('meta[name="description"]', 'meta[property="og:description"]'):
            assert soup.select_one(meta)["content"] == "Studio · 15 Mare Street, E8 3RH"
        assert "0 bed" not in resp.text

    async def test_status_control_shows_the_saved_status(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """The header's status control replaced the old status bar."""
        await storage.save_merged_property(merged_a)
        await storage.update_user_status(merged_a.unique_id, UserStatus.VIEWING_BOOKED)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "detail-status-bar" not in resp.text
        trigger = _soup(resp.text).select_one(".status-selector .status-selector-trigger")
        assert trigger.get_text(strip=True) == "Viewing"

    @pytest.mark.parametrize("checked", [False, True])
    async def test_freshness_says_when_first_seen_and_whether_availability_was_checked(
        self, client: TestClient, storage: PropertyStorage, checked: bool
    ) -> None:
        """An unchecked listing says so plainly; a checked one says when."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="999",
            url=HttpUrl("https://openrent.com/999"),
            title="Old listing",
            price_pcm=1900,
            bedrooms=1,
            address="1 Street",
            postcode="E8 3RH",
            first_seen="2025-01-01T00:00:00+00:00",
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        merged = _single_source(prop)
        await storage.save_merged_property(merged)
        expected = "First seen 1 Jan 2025 · availability not checked"
        if checked:
            await storage.update_property_last_checked(merged.unique_id)
            saved = await storage.web.get_property_detail(merged.unique_id)
            stamp = datetime.fromisoformat(saved["last_checked_at"])
            expected = (
                f"First seen 1 Jan 2025 · availability last checked {stamp.day} {stamp:%b %Y}"
            )

        resp = client.get(f"/property/{merged.unique_id}")
        freshness = _soup(resp.text).select_one(".detail-header .listing-freshness")
        assert " ".join(freshness.get_text(" ").split()) == expected
        assert "May be negotiable" not in resp.text

    async def test_off_market_banner_on_detail(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "off-market-banner" in resp.text
        assert "removed from the listing platform" in resp.text

    async def test_no_banner_for_active_property(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "off-market-banner" not in resp.text


class TestTake:
    """The take beside the photos: the brief's first gap, the fit score, one thing to check."""

    async def test_take_states_the_lead_the_score_and_the_first_check(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "viewing_notes": ViewingNotes(
                    check_items=["Run every tap.", "Open every window."],
                    questions_for_agent=["Is the furniture staying?"],
                    deal_breaker_tests=["Make a phone call in the bedroom."],
                )
            }
        )
        await storage.save_merged_property(merged_a, commute_minutes=22)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)
        take = soup.select_one("aside.take")

        assert "listing-top-with-take" in take.parent["class"]
        lead = take.select_one(".detail-lead")
        assert "detail-lead-unknown" in lead["class"]
        assert lead.select_one(".detail-lead-kicker").get_text() == "2 must-haves need checking"
        score = int(take.select_one(".fit-brief-score").get_text(strip=True))
        band = take.select_one(".take-fit-band").get_text(" ", strip=True)
        assert band.startswith(fit_tier_label(score).capitalize())
        assert "lowest:" in band
        # The one thing to check is the first check the analysis wrote for this home.
        check = take.select_one(".take-check")
        assert check.select_one(".take-check-item").get_text() == "Run every tap."
        assert check.a["href"] == "#section-viewing"
        # The take is where the AI's reading is first met, so its provenance line is here.
        assert "AI reading of the listing photos" in take.select_one(".provenance-note").get_text()
        assert soup.select_one("#section-overview .provenance-note") is None

    @pytest.mark.parametrize(
        ("notes", "expected"),
        [
            (
                ViewingNotes(deal_breaker_tests=["Make a phone call in the bedroom."]),
                "Make a phone call in the bedroom.",
            ),
            (ViewingNotes(questions_for_agent=["Is the furniture staying?"]), None),
            (None, None),
        ],
        ids=["first quick test stands in", "questions are not checks", "no viewing notes"],
    )
    async def test_take_shows_a_check_only_when_the_analysis_wrote_one(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
        notes: ViewingNotes | None,
        expected: str | None,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(
            prop_a.unique_id, base_analysis.model_copy(update={"viewing_notes": notes})
        )
        take = _soup(client.get(f"/property/{merged_a.unique_id}").text).select_one(".take")
        item = take.select_one(".take-check-item")
        assert (item.get_text() if item else None) == expected

    async def test_broken_hard_limit_leads_the_take_without_a_fit_band(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """The score doesn't count the limits, so a home over one isn't called a fit beside it."""
        await storage.save_merged_property(merged_a, commute_minutes=22)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        client.app.state.settings = client.app.state.settings.model_copy(update={"max_price": 1800})
        take = _soup(client.get(f"/property/{merged_a.unique_id}").text).select_one(".take")

        lead = take.select_one(".detail-lead")
        assert "detail-lead-broken" in lead["class"]
        assert lead.select_one(".detail-lead-kicker").get_text() == "Breaks a must-have"
        assert lead.select_one(".detail-lead-title").get_text() == (
            "£1,900 pcm, £100 over your limit."
        )
        assert take.select_one(".fit-brief-score") is not None
        band = take.select_one(".take-fit-band").get_text(" ", strip=True)
        assert band.startswith("Lowest:")
        assert "fit" not in band.lower()

    async def test_unassessed_home_has_no_take_and_says_so_under_the_title(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)
        assert soup.select_one(".take") is None
        assert "listing-top-with-take" not in soup.select_one(".listing-top")["class"]
        notice = soup.select_one(".detail-header .processing-notice")
        assert notice.get_text() == "Not assessed. A fit score is not available yet."


class TestQualitySection:
    """The room-by-room quality cards and the gallery review note."""

    @pytest.mark.parametrize("reviewed", [None, 7, 8])
    async def test_gallery_review_coverage_is_qualified_and_legacy_remains_unknown(
        self, client, storage, prop_a, merged_a, base_analysis, reviewed
    ):
        coverage = (
            ImageReviewCoverage(
                available_gallery_count=8,
                reviewed_gallery_count=reviewed,
                rooms_not_seen_in_review=("bathroom",) if reviewed == 7 else (),
            )
            if reviewed is not None
            else None
        )
        analysis = base_analysis.model_copy(update={"image_review_coverage": coverage})
        if reviewed == 8:
            analysis = analysis.model_copy(
                update={
                    "listing_red_flags": ListingRedFlags(
                        missing_room_photos=["bathroom"], red_flag_count=1
                    ),
                    "lowlights": ["No bathroom photos supplied"],
                }
            )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        response = client.get(f"/property/{merged_a.unique_id}")
        assert response.status_code == 200
        if reviewed is None:
            assert "available gallery photos" not in response.text
        else:
            assert f"Reviewed {reviewed} of 8 available gallery photos." in response.text
        if reviewed == 7:
            assert "Other photos were not reviewed; photo completeness is unknown." in response.text
            assert "Not seen in reviewed photos: bathroom." in response.text
            assert "No bathroom photos" not in response.text
        else:
            assert "Other photos were not reviewed" not in response.text
        if reviewed == 8:
            assert "Not seen in supplied photos: bathroom" in response.text
            # The red flag names the room, so the vaguer photo tag isn't repeated.
            assert "No bathroom photos supplied" not in response.text

    async def test_quality_cards_render_room_data(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        # Primary cards
        assert "Kitchen" in resp.text
        assert "Modern" in resp.text
        assert "Gas hob" in resp.text
        assert "Dishwasher" in resp.text
        assert "Condition" in resp.text
        assert "Light" in resp.text
        # Secondary cards
        assert "Living Room" in resp.text

    async def test_quality_section_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Room by room" in resp.text

    async def test_estimated_badge_absent_with_floorplan(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Properties WITH floorplan images should NOT show the 'estimated' badge."""
        await storage.save_merged_property(merged_a)
        await storage.save_property_images(
            merged_a.unique_id,
            [
                PropertyImage(
                    source=PropertySource.OPENRENT,
                    url=HttpUrl("https://example.com/fp.jpg"),
                    image_type="floorplan",
                )
            ],
        )
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Living Room" in resp.text
        assert "estimated" not in resp.text

    async def test_estimated_badge_shown_without_floorplan(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Properties WITHOUT floorplan images should show the 'estimated' badge."""
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Living Room" in resp.text
        assert "estimated" in resp.text


class TestAreaSummary:
    """The compact area card; the full guide is the area page (/area/{outcode})."""

    async def test_known_outcode_shows_area_summary(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)
        assert soup.select_one("#section-area h3").get_text() == "Area: E8"
        assert soup.select_one("#section-nav a[href='#section-area']") is not None
        stats = [stat.get_text(" ", strip=True) for stat in soup.select(".area-summary-stat")]
        assert any(stat.startswith("Crime ") for stat in stats)

    async def test_ward_selects_the_micro_area(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """The ward wins over the address, which would match Hackney Central."""
        await storage.save_merged_property(merged_a, ward="London Fields")
        card = _soup(client.get(f"/property/{merged_a.unique_id}").text).select_one(
            ".area-summary-card"
        )
        assert card.select_one(".area-summary-header strong").get_text() == (
            "London Fields / Broadway Market"
        )
        # The neighbourhood's hand-written hosting rating is unverified, so the listing
        # page doesn't show it; the area page still does.
        badges = [badge.get_text() for badge in card.select(".area-summary-badges .badge")]
        assert [badge.split(":")[0] for badge in badges] == ["WFH"]
        assert "Hosting:" not in card.get_text()
        link = card.select_one(".area-summary-link")
        assert link.get_text().startswith("Full area guide")
        assert link["href"] == "/area/E8?highlight=London%20Fields%20/%20Broadway%20Market"

    async def test_address_selects_the_micro_area_without_a_ward(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        card = _soup(client.get(f"/property/{merged_a.unique_id}").text).select_one(
            ".area-summary-card"
        )
        assert card.select_one(".area-summary-header strong").get_text() == (
            "Hackney Central / Mare Street"
        )

    async def test_unknown_outcode_has_no_area_summary(
        self, client: TestClient, storage: PropertyStorage, prop_a: Property
    ) -> None:
        merged = _single_source(prop_a.model_copy(update={"postcode": "ZZ9 9ZZ"}))
        await storage.save_merged_property(merged)
        resp = client.get(f"/property/{merged.unique_id}")
        assert resp.status_code == 200
        assert _soup(resp.text).select_one("#section-area") is None
        assert "Full area guide" not in resp.text
        assert "Hosting:" not in resp.text

    async def test_property_type_adds_the_sound_profile(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        def sound_stats() -> list[str]:
            soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)
            stats = [stat.get_text(" ", strip=True) for stat in soup.select(".area-summary-stat")]
            return [stat for stat in stats if stat.startswith("Sound")]

        await storage.save_merged_property(merged_a)
        assert sound_stats() == []
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(property_type=PropertyType.VICTORIAN),
            }
        )
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        assert len(sound_stats()) == 1

    async def test_noise_enforcement_is_left_to_the_area_page(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(property_type=PropertyType.VICTORIAN),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Noise Enforcement" not in resp.text

        resp = client.get("/area/E8")
        assert resp.status_code == 200
        assert "Noise Enforcement (Hackney)" in resp.text


class TestListingText:
    """The brief lead, annotated listing text and the plain description."""

    async def test_two_voices_lead_brief_notes_and_working_render(
        self, client, storage, prop_a, merged_a, base_analysis
    ) -> None:
        """The take leads with the brief's first gap; the brief is listed and the text annotated."""
        analysis = base_analysis.model_copy(
            update={
                "lowlights": ["Potential traffic noise", "No dishwasher"],
                "highlights": ["Gas hob"],
                "flooring_noise": FlooringNoiseAnalysis(
                    primary_flooring="wood",
                    has_double_glazing="yes",
                    hosting_noise_risk="moderate",
                    noise_indicators=["Railway line directly beside the building"],
                ),
            }
        )
        await storage.save_merged_property(merged_a, commute_minutes=22)
        await storage.update_source_text(
            merged_a.unique_id,
            ListingTextEvidence.observed(
                observed_at=datetime.now(UTC),
                description="A stunning flat with a private balcony and a fitted kitchen.",
            ),
        )
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        client.app.state.settings = client.app.state.settings.model_copy(
            update={"brief_must_haves": "workspace,sound"}
        )
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)

        lead = soup.select_one(".take .detail-lead")
        assert "detail-lead-risk" in lead["class"]
        # The noise tag is named for itself: the first noise note may be about the floors.
        assert lead.select_one(".detail-lead-title").get_text() == "Potential road or rail noise."
        assert lead.select_one(".detail-lead-detail").get_text() == (
            "Your must-have: quiet enough for calls. Check it at a viewing."
        )

        groups = [li.get_text(strip=True) for li in soup.select(".brief-check .brief-group")]
        assert groups == ["Must-haves", "Nice-to-haves"]
        conflict = soup.select_one(".brief-check .brief-conflict")
        assert conflict.select_one(".brief-label").get_text() == "Quiet enough for calls"
        assert "brief-must" in conflict["class"]

        # The score's working is closed until asked for, from the take or the brief check.
        score = soup.select_one(".take .fit-brief-score").get_text(strip=True)
        working = soup.select_one("#section-fit details#fit-working")
        assert not working.has_attr("open")
        assert working.summary.get_text(" ", strip=True) == f"How the {score} is made"
        assert len(working.select(".fit-working tbody tr")) == 6
        assert working.select_one(".fit-working tfoot td:last-child").get_text(strip=True) == score
        link = soup.select_one(".take a[href='#fit-working']")
        assert link.get_text(" ", strip=True) == f"How the {score} is made"

        notes = [li.find(string=True).strip() for li in soup.select(".annotated-notes li")]
        assert soup.select_one("#section-nav a[href='#section-description']") is not None
        assert notes == [
            "Photo assessment: Railway line directly beside the building. Check noise at a viewing."
        ]
        # Only the finding is marked; "stunning" is left as the agent wrote it.
        assert [m.get_text() for m in soup.select(".annotated-text .mark-wavy")] == [
            "private balcony"
        ]
        assert soup.select_one("#section-description .description-text") is None

    async def test_note_numbers_link_to_their_notes_and_back(
        self, client, storage, prop_a, merged_a, base_analysis
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "lowlights": ["Potential traffic noise"],
                "kitchen": base_analysis.kitchen.model_copy(update={"has_dishwasher": "no"}),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.update_source_text(
            merged_a.unique_id,
            ListingTextEvidence.observed(
                observed_at=datetime.now(UTC),
                description="A flat with a private balcony and a fitted kitchen.",
            ),
        )
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)

        refs = soup.select(".annotated-text .note-ref a")
        notes = soup.select(".annotated-notes li")
        assert [ref.get_text() for ref in refs] == ["1", "2"]
        assert len(notes) == 2
        for number, (ref, note) in enumerate(zip(refs, notes, strict=True), start=1):
            # Forward: the number names its note to a screen reader and lands on it.
            assert ref["aria-label"] == f"Note {number}"
            assert soup.select_one(ref["href"]) is note
            # Back: the note's own link returns to the number that cited it.
            back = note.select_one("a.note-back")
            assert back["aria-label"] == f"Back to note {number} in the text"
            assert soup.select_one(back["href"]) is ref

    async def test_advert_markup_renders_as_line_breaks_with_notes_on_the_words(
        self, client, storage, prop_a, merged_a, base_analysis
    ) -> None:
        """Portal markup becomes line breaks in both renderings, never visible tags."""
        await storage.save_merged_property(merged_a)
        await storage.update_source_text(
            merged_a.unique_id,
            ListingTextEvidence.observed(
                observed_at=datetime.now(UTC),
                description="Bright flat.<br /><br /><b>Private balcony</b>.<BR/>Gas &amp; oven.",
            ),
        )
        plain = client.get(f"/property/{merged_a.unique_id}").text
        assert "Bright flat.<br><br>Private balcony.<br>Gas &amp; oven." in plain
        assert "&lt;" not in plain

        noisy = base_analysis.model_copy(update={"lowlights": ["Potential traffic noise"]})
        await storage.save_quality_analysis(prop_a.unique_id, noisy)
        annotated = client.get(f"/property/{merged_a.unique_id}").text
        assert (
            '<p class="annotated-text">Bright flat.<br><br><span class="mark-wavy">Private balcony'
            '</span><sup class="note-ref">'
        ) in annotated
        assert "</sup>.<br>Gas &amp; oven.</p>" in annotated
        assert "&lt;" not in annotated

    async def test_page_shows_the_advert_the_analysis_read_not_the_longest(
        self, client, storage, prop_a, base_analysis
    ) -> None:
        zoopla_url = HttpUrl("https://www.zoopla.co.uk/to-rent/details/100")
        longer = "A much longer advert from the other portal. " * 5
        merged = MergedProperty(
            canonical=prop_a,
            sources=(PropertySource.OPENRENT, PropertySource.ZOOPLA),
            source_urls={PropertySource.OPENRENT: prop_a.url, PropertySource.ZOOPLA: zoopla_url},
            min_price=1900,
            max_price=1900,
            descriptions={PropertySource.OPENRENT: "Main advert.", PropertySource.ZOOPLA: longer},
        )
        await storage.save_merged_property(merged)

        # No analysis: the home's canonical source, however short.
        page = client.get(f"/property/{merged.unique_id}").text
        assert "Main advert." in page
        assert longer.strip() not in page

        # The analysis read only Zoopla's advert, unchanged since, so the notes sit on it.
        # Its kept inputs come from the analysis's own selection, hashes and all.
        zoopla_only = merged.model_copy(update={"descriptions": {PropertySource.ZOOPLA: longer}})
        read_zoopla = base_analysis.model_copy(
            update={"listing_text_inputs": select_listing_text(zoopla_only)}
        )
        await storage.save_quality_analysis(prop_a.unique_id, read_zoopla)
        page = client.get(f"/property/{merged.unique_id}").text
        assert f'<p class="annotated-text">{longer.strip()}</p>' in page
        assert "Main advert." not in page

    async def test_listing_text_keeps_its_paragraph_breaks(
        self, client, storage, prop_a, merged_a, base_analysis
    ) -> None:
        """A newline in the retained text renders as a line break, not a literal "<br>".

        Both renderings of the text are covered: the plain description shown when there is
        no analysis, and the annotated one shown when there is.
        """
        await storage.save_merged_property(merged_a)
        await storage.update_source_text(
            merged_a.unique_id,
            ListingTextEvidence.observed(
                observed_at=datetime.now(UTC), description="First paragraph.\n\nSecond paragraph."
            ),
        )
        plain = client.get(f"/property/{merged_a.unique_id}").text
        assert "First paragraph.<br><br>Second paragraph." in plain
        assert "&lt;br&gt;" not in plain

        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        annotated = client.get(f"/property/{merged_a.unique_id}").text
        assert (
            '<p class="annotated-text">First paragraph.<br><br>Second paragraph.</p>' in annotated
        )
        assert "&lt;br&gt;" not in annotated

    async def test_description_toggle_starts_hidden_and_collapsed(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """sections.js reveals the toggle for long text and flips aria-expanded on click."""
        await storage.save_merged_property(merged_a)
        soup = _soup(client.get(f"/property/{merged_a.unique_id}").text)
        toggle = soup.select_one("#section-description #description-toggle")
        assert toggle.has_attr("hidden")
        assert toggle["aria-expanded"] == "false"

    async def test_figures_are_marked_and_the_up_to_qualifier_is_set_apart(
        self, client, storage, prop_a, merged_a, base_analysis
    ) -> None:
        """Figures in sentences are wrapped in .figure; in a total, "up to" is wrapped instead."""
        await storage.save_merged_property(merged_a, commute_minutes=22)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

        listing = _soup(client.get(f"/property/{merged_a.unique_id}").text)
        assert listing.select_one(".money-total-qualifier").get_text() == "up to"
        assert listing.select_one(".fit-working tfoot .figure") is not None
