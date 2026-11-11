"""POST /property/{id}/reanalyze queues an assessed home for the next --reanalyze run."""

from fastapi.testclient import TestClient

from home_finder.db.storage import PropertyStorage
from home_finder.models import MergedProperty, Property, PropertyQualityAnalysis


async def test_reanalyze_returns_queued(
    client: TestClient,
    storage: PropertyStorage,
    prop_a: Property,
    merged_a: MergedProperty,
    base_analysis: PropertyQualityAnalysis,
) -> None:
    await storage.save_merged_property(merged_a)
    await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

    resp = client.post(f"/property/{prop_a.unique_id}/reanalyze")
    assert resp.status_code == 200
    assert resp.json() == {"status": "queued"}


def test_reanalyze_unknown_property_404(client: TestClient) -> None:
    resp = client.post("/property/nonexistent-999/reanalyze")
    assert resp.status_code == 404


async def test_reanalyze_without_an_assessment_404(
    client: TestClient,
    storage: PropertyStorage,
    merged_a: MergedProperty,
) -> None:
    """There is nothing to reanalyse until the home has a quality analysis."""
    await storage.save_merged_property(merged_a)
    resp = client.post(f"/property/{merged_a.unique_id}/reanalyze")
    assert resp.status_code == 404
    assert await storage.processing.get_reanalysis_queue() == []


async def test_reanalyze_adds_the_home_to_the_queue(
    client: TestClient,
    storage: PropertyStorage,
    prop_a: Property,
    merged_a: MergedProperty,
    base_analysis: PropertyQualityAnalysis,
) -> None:
    await storage.save_merged_property(merged_a)
    await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

    client.post(f"/property/{prop_a.unique_id}/reanalyze")
    queue = await storage.processing.get_reanalysis_queue()
    assert len(queue) == 1
    assert queue[0].unique_id == prop_a.unique_id


async def test_reanalyze_idempotent(
    client: TestClient,
    storage: PropertyStorage,
    prop_a: Property,
    merged_a: MergedProperty,
    base_analysis: PropertyQualityAnalysis,
) -> None:
    await storage.save_merged_property(merged_a)
    await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

    resp1 = client.post(f"/property/{prop_a.unique_id}/reanalyze")
    resp2 = client.post(f"/property/{prop_a.unique_id}/reanalyze")
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    queue = await storage.processing.get_reanalysis_queue()
    assert len(queue) == 1
