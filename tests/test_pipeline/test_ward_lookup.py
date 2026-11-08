"""Ward acquisition selects useful evidence and persists only resolved locations."""

from unittest.mock import AsyncMock

import pytest

from home_finder.db import PropertyStorage
from home_finder.pipeline.assessment import _lookup_wards
from home_finder.utils import postcode_lookup


@pytest.mark.parametrize("resolved", [True, False], ids=["partial-results", "no-results"])
async def test_ward_lookup_preserves_unresolved_homes_and_existing_wards(
    tmp_path, monkeypatch, make_merged_property, resolved
):
    homes = [
        make_merged_property(source_id="coordinates", latitude=51.54, longitude=-0.05),
        make_merged_property(
            source_id="postcode", postcode="E8 3RH", latitude=None, longitude=None
        ),
        make_merged_property(source_id="outcode", postcode="E8", latitude=None, longitude=None),
        make_merged_property(source_id="known", latitude=51.55, longitude=-0.06),
    ]
    reverse = AsyncMock(return_value=["London Fields" if resolved else None])
    forward = AsyncMock(return_value="Dalston" if resolved else None)
    monkeypatch.setattr(postcode_lookup, "bulk_reverse_lookup_wards", reverse)
    monkeypatch.setattr(postcode_lookup, "lookup_ward", forward)
    path = str(tmp_path / "wards.db")
    async with PropertyStorage(path) as storage:
        for home in homes:
            await storage.save_merged_property(home)
        await storage.update_wards({homes[3].unique_id: "Existing ward"})

        await _lookup_wards(storage)

    # Reopen to establish durable results, not just mutations on input objects.
    async with PropertyStorage(path) as storage:
        async with storage._read_connection() as conn:
            rows = await (await conn.execute("SELECT unique_id, ward FROM properties")).fetchall()
        assert dict(rows) == {
            homes[0].unique_id: "London Fields" if resolved else None,
            homes[1].unique_id: "Dalston" if resolved else None,
            homes[2].unique_id: None,
            homes[3].unique_id: "Existing ward",
        }
    reverse.assert_awaited_once()
    forward.assert_awaited_once()
    assert reverse.await_args.args == ([(51.54, -0.05)],)
    assert forward.await_args.args == ("E8 3RH",)
    assert reverse.await_args.kwargs["client"] is forward.await_args.kwargs["client"]
    assert reverse.await_args.kwargs["client"].is_closed


async def test_empty_ward_queue_does_not_call_acquisition(monkeypatch):
    reverse = AsyncMock()
    forward = AsyncMock()
    monkeypatch.setattr(postcode_lookup, "bulk_reverse_lookup_wards", reverse)
    monkeypatch.setattr(postcode_lookup, "lookup_ward", forward)
    async with PropertyStorage(":memory:") as storage:
        await _lookup_wards(storage)
    reverse.assert_not_awaited()
    forward.assert_not_awaited()
