"""Saving and reading the stored assessment of a home."""

from home_finder.models import ConditionAnalysis


async def test_saved_assessment_reads_back(
    storage, make_merged_property, sample_quality_analysis
) -> None:
    home = make_merged_property()
    await storage.save_merged_property(home)
    await storage.save_quality_analysis(home.unique_id, sample_quality_analysis)

    stored = await storage.get_quality_analysis(home.unique_id)
    # Persistence fills unset enums (for example floor_level becomes "unknown"),
    # so compare the sections it leaves as given.
    assert stored is not None
    assert stored.overall_rating == 4
    assert stored.summary == sample_quality_analysis.summary
    assert stored.kitchen == sample_quality_analysis.kitchen
    assert stored.value == sample_quality_analysis.value


async def test_home_without_an_assessment_reads_as_none(storage) -> None:
    assert await storage.get_quality_analysis("nonexistent:999") is None


async def test_saving_again_replaces_the_assessment(
    storage, make_merged_property, sample_quality_analysis
) -> None:
    home = make_merged_property()
    await storage.save_merged_property(home)
    await storage.save_quality_analysis(home.unique_id, sample_quality_analysis)
    replacement = sample_quality_analysis.model_copy(
        update={
            "condition": ConditionAnalysis(
                overall_condition="fair",
                confidence="high",
                has_visible_damp="yes",
                maintenance_concerns=["damp patch in bathroom"],
            ),
            "condition_concerns": True,
            "concern_severity": "moderate",
            "overall_rating": 3,
            "summary": "Some damp concerns.",
        }
    )

    await storage.save_quality_analysis(home.unique_id, replacement)

    stored = await storage.get_quality_analysis(home.unique_id)
    assert stored is not None
    assert stored.overall_rating == 3
    assert stored.summary == "Some damp concerns."
    assert (stored.condition_concerns, stored.concern_severity) == (True, "moderate")
    assert stored.condition.maintenance_concerns == ["damp patch in bathroom"]


async def test_replacing_an_assessment_moves_the_analysed_at_time(
    storage, make_merged_property, make_quality_analysis
) -> None:
    """The analysis time shown on a listing is the time of the analysis it shows."""
    home = make_merged_property()
    await storage.save_merged_property(home)
    await storage.save_quality_analysis(home.unique_id, make_quality_analysis(summary="First"))
    # Age the first analysis, as if it had been saved long ago.
    conn = await storage._get_connection()
    await conn.execute(
        "UPDATE quality_analyses SET created_at = ? WHERE property_unique_id = ?",
        ("2020-01-01T00:00:00+00:00", home.unique_id),
    )
    await conn.commit()

    await storage.save_quality_analysis(home.unique_id, make_quality_analysis(summary="Second"))

    detail = await storage.web.get_property_detail(home.unique_id)
    assert detail is not None
    assert detail["quality_analysis"].summary == "Second"
    assert detail["analysed_at"] > "2025", "the date must belong to the analysis shown"
