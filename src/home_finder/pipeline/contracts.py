"""Inputs, outputs, and progress accounting shared by pipeline stages.

Stage results describe ordinary completion, including empty batches. Mutable
CandidateProgress captures partial work when a stage fails or is cancelled.
Legacy database metric names are translated only at the persistence boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from home_finder.analysis.analyzer import TokenUsage

from home_finder.models import (
    PropertyAggregate,
    PropertyQualityAnalysis,
    SourceListing,
    TransportMode,
)


@dataclass
class ScraperMetrics:
    """Per-scraper performance metrics for a single pipeline run."""

    scraper_name: str = ""
    started_at: str = ""
    completed_at: str | None = None
    duration_seconds: float = 0.0
    areas_attempted: int = 0
    areas_completed: int = 0
    properties_found: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    parse_errors: int = 0
    is_healthy: bool = True
    error_message: str | None = None


@dataclass
class CollectedListings:
    """Selected source listings and collection health, including empty discovery."""

    listings: list[SourceListing] = field(default_factory=list)
    scraper_metrics: list[ScraperMetrics] = field(default_factory=list)


@dataclass
class FilteredListings:
    """Selected source listings and survivor counts for the two selection rules."""

    listings: list[SourceListing] = field(default_factory=list)
    criteria_passed_count: int = 0
    location_passed_count: int = 0


@dataclass
class CommuteAssessments:
    """All candidates with updated coordinates and advisory commute projections."""

    candidates: list[PropertyAggregate]
    measured_commutes: dict[str, tuple[int, TransportMode]] = field(default_factory=dict)
    within_limit_count: int = 0


@dataclass
class ConsolidationResult:
    """Incoming aggregates left for analysis, plus established anchors updated."""

    unmatched_candidates: list[PropertyAggregate] = field(default_factory=list)
    anchors_updated: int = 0


@dataclass
class CandidateAdmission:
    """Result of consolidation followed by the floorplan/photo admission policy."""

    candidates: list[PropertyAggregate] = field(default_factory=list)
    anchors_updated: int = 0
    unmatched_candidate_count: int = 0
    media_admitted_count: int = 0


@dataclass
class CandidateProgress:
    """Mutable accounting snapshot; safe to persist after interrupted preparation."""

    observed_listing_count: int = 0
    media_ready_count: int = 0
    anchors_updated: int = 0
    prepared_candidate_count: int = 0
    stage_timings: dict[str, float] = field(default_factory=dict)
    scraper_metrics: list[ScraperMetrics] = field(default_factory=list)
    criteria_passed_count: int = 0
    location_passed_count: int = 0
    new_listing_count: int = 0
    commute_within_limit_count: int = 0
    unmatched_candidate_count: int = 0
    media_admitted_count: int = 0

    @property
    def collection_status(self) -> str:
        """Collection health is independent of downstream assessment/delivery."""
        attempted = [m for m in self.scraper_metrics if m.areas_attempted > 0]
        if all(m.is_healthy for m in attempted):
            return "completed"
        if any(m.properties_found or m.is_healthy for m in attempted):
            return "partial"
        return "failed"

    def run_columns(self) -> dict[str, int | float]:
        """Translate explicit stage metrics to the existing pipeline_runs schema."""
        columns: dict[str, int | float] = {
            "scraped_count": max(
                self.observed_listing_count, sum(m.properties_found for m in self.scraper_metrics)
            ),
            "new_count": self.prepared_candidate_count,
            "enriched_count": self.media_ready_count,
            "anchors_updated": self.anchors_updated,
            "criteria_filtered_count": self.criteria_passed_count,
            "location_filtered_count": self.location_passed_count,
            "new_property_count": self.new_listing_count,
            "commute_within_limit_count": self.commute_within_limit_count,
            "post_dedup_count": self.unmatched_candidate_count,
            "post_floorplan_count": self.media_admitted_count,
        }
        duration_columns = {
            "collection_seconds": "scraping_seconds",
            "filtering_seconds": "filtering_seconds",
            "preparation_seconds": "enrichment_seconds",
        }
        columns.update(
            {duration_columns[name]: seconds for name, seconds in self.stage_timings.items()}
        )
        return columns


@dataclass
class CandidateBatch:
    """Prepared aggregates and commute projections, even when no candidates remain."""

    candidates: list[PropertyAggregate] = field(default_factory=list)
    commute_lookup: dict[str, tuple[int, TransportMode]] = field(default_factory=dict)
    progress: CandidateProgress = field(default_factory=CandidateProgress)


class AnalysisDisposition(StrEnum):
    ASSESSED = "assessed"
    SKIPPED = "skipped"
    DEFERRED = "deferred"
    FAILED = "failed"


@dataclass(frozen=True)
class AnalysisOutcome:
    """A provider result or explicit skip, before its durable completion policy.

    An absent assessment means different things only through its disposition:
    skipped analysis is complete; missing media is deferred. Exceptions remain
    the channel for provider unavailability, spend admission, and cancellation.
    """

    candidate: PropertyAggregate
    disposition: AnalysisDisposition
    assessment: PropertyQualityAnalysis | None = None
    reason: str | None = None

    @classmethod
    def from_assessment(
        cls, candidate: PropertyAggregate, assessment: PropertyQualityAnalysis | None
    ) -> AnalysisOutcome:
        """Translate the provider's compatibility return value at one boundary."""
        if assessment is None:
            return cls(candidate, AnalysisDisposition.DEFERRED, reason="missing_cached_images")
        if assessment.overall_rating is None:
            return cls(candidate, AnalysisDisposition.FAILED, assessment, "analysis_incomplete")
        return cls(candidate, AnalysisDisposition.ASSESSED, assessment)


@dataclass
class InitialAssessmentSummary:
    """Completed assessments and explicit skipped/deferred/failed outcomes.

    processed_count projects to the legacy analyzed_count column. That column
    includes skipped analysis and is not a provider-call count.
    """

    assessed: int = 0
    skipped: int = 0
    deferred: int = 0
    failed: int = 0
    token_usage: TokenUsage | None = None

    @property
    def processed_count(self) -> int:
        return self.assessed + self.skipped


@dataclass
class ReanalysisSummary:
    """Outcomes of one requested-reassessment queue drain."""

    completed: int = 0
    failed: int = 0
    deferred: int = 0
