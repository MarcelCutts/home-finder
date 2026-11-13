"""The card partial (GET /property/{id}/card) and status changes (PATCH /property/{id}/status)."""

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from home_finder.db.storage import PropertyStorage
from home_finder.models import USER_STATUS_META, MergedProperty, Property, PropertyQualityAnalysis


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


class TestCardPartial:
    """The single-card partial the dashboard map loads when a marker is clicked."""

    async def test_returns_card_html(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        assert "property-card" in resp.text
        assert merged_a.unique_id in resp.text

    def test_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.get("/property/nonexistent:999/card")
        assert resp.status_code == 404

    async def test_card_includes_price(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        assert "1,900" in resp.text

    async def test_card_includes_quality_data(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        assert "fit-tier-pill" in resp.text

    async def test_card_is_partial_not_full_page(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Card endpoint returns just the card, not wrapped in base.html."""
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        assert "<nav" not in resp.text
        assert "I Hate Moving" not in resp.text
        assert "<article" in resp.text


class TestStatusChange:
    """PATCH /property/{unique_id}/status, from a card or as the status control partial.

    The listing page no longer shows that control; the route still returns it.
    """

    async def test_patch_returns_selector_partial(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text
        assert "status-selector" in resp.text
        # hx-target must use "closest" to avoid colon-in-id CSS selector bug
        assert 'hx-target="closest .status-selector-wrap"' in resp.text
        assert 'hx-swap="morph:innerHTML"' in resp.text

    async def test_patch_returns_card_partial_for_htmx_card_swap(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """A card's quick action sends source=card and gets the whole card back."""
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "interested", "source": "card"},
        )
        assert resp.status_code == 200
        assert "property-card" in resp.text
        assert 'data-status="interested"' in resp.text
        assert "<!DOCTYPE html>" not in resp.text

    async def test_consecutive_status_changes(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Each PATCH reports the status it saved, and the history keeps every change."""
        await storage.save_merged_property(merged_a)
        steps = [
            ("interested", "Interested"),
            ("enquired", "Enquired"),
            ("viewing_booked", "Viewing"),
        ]
        for status, label in steps:
            resp = client.patch(
                f"/property/{merged_a.unique_id}/status",
                data={"status": status},
            )
            assert resp.status_code == 200, f"Failed on {status}"
            trigger = _soup(resp.text).select_one(".status-selector-trigger")
            assert trigger.get_text(strip=True) == label
        history = await storage.get_status_history(merged_a.unique_id)
        assert sorted(event["to_status"] for event in history) == sorted(s for s, _ in steps)

    async def test_response_htmx_attrs_on_all_buttons(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Every status-option button in the PATCH response must have correct HTMX attrs."""
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 200
        buttons = _soup(resp.text).select("button.status-option")
        # Three groups (triage, progress, outcome) of three statuses
        assert len(buttons) == 9
        for btn in buttons:
            assert btn.get("hx-target") == "closest .status-selector-wrap"
            assert btn.get("hx-swap") == "morph:innerHTML"
            assert btn.get("hx-patch")

    async def test_active_and_disabled_state_updates(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Only the saved status's option is active, disabled and checked, after each change."""
        await storage.save_merged_property(merged_a)
        for status in ("interested", "enquired"):
            resp = client.patch(
                f"/property/{merged_a.unique_id}/status",
                data={"status": status},
            )
            assert resp.status_code == 200
            soup = _soup(resp.text)
            label = USER_STATUS_META[status]["label"]
            for selector in (
                "button.status-option[disabled]",
                "button.status-option.active",
                "button.status-option:has(svg.status-option-check)",
            ):
                assert [b.get_text(strip=True) for b in soup.select(selector)] == [label]

    @pytest.mark.parametrize("status", ["viewing_booked", "rejected"])
    async def test_trigger_label_and_color_update(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty, status: str
    ) -> None:
        """The trigger shows the saved status's label and colour from USER_STATUS_META."""
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": status},
        )
        assert resp.status_code == 200
        meta = USER_STATUS_META[status]
        trigger = _soup(resp.text).select_one(".status-selector-trigger")
        assert trigger.get_text(strip=True) == meta["label"]
        assert f"--status-color: {meta['color']}" in trigger["style"]


class TestStatusChangeErrors:
    """HTMX requests get an HTML toast for the #error-toast target; others get JSON."""

    async def test_patch_invalid_status_htmx_returns_html(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "bogus"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert resp.text == '<p class="toast-message">Invalid status: bogus</p>'

    def test_patch_missing_htmx_returns_html(self, client: TestClient) -> None:
        resp = client.patch(
            "/property/fake:999/status",
            data={"status": "interested"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert "toast-message" in resp.text

    def test_patch_missing_non_htmx_returns_json(self, client: TestClient) -> None:
        resp = client.patch(
            "/property/fake:999/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "Property not found"

    async def test_patch_invalid_non_htmx_returns_json(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "bogus"},
        )
        assert resp.status_code == 400
        assert "Invalid status" in resp.json()["error"]
        assert await storage.get_status_history(merged_a.unique_id) == []
