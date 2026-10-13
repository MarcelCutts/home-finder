"""Application configuration using pydantic-settings."""

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from home_finder.data.location_mappings import resolve_area_outcodes
from home_finder.models import FurnishType, SearchCriteria, TransportMode
from home_finder.models.analysis import AnalysisConfig, EvaluationPhaseConfig, VisualPhaseConfig
from home_finder.models.spend import SpendPolicy


def new_runtime_analysis_config(
    *, max_images: int = 20, enable_extended_thinking: bool = True
) -> AnalysisConfig:
    """New-run defaults, separate from historical configuration deserialization.

    Reserve 110k for visual text/schema/framing, plus 5.5k per configured image
    and one spare floorplan slot (5k visual tokens plus metadata headroom).
    The native payload check still defers unusually large text before dispatch.
    Gallery selection and the persisted input-budget policy remain unchanged.
    """
    if type(max_images) is not int or not 1 <= max_images <= 20:
        raise ValueError("max_images must be an integer between 1 and 20")
    return AnalysisConfig(
        visual=VisualPhaseConfig(
            model="claude-sonnet-5-5",
            reasoning="adaptive" if enable_extended_thinking else "disabled",
            effort="low",
            input_token_limit=110_000 + 5_500 * (max_images + 1),
            max_output_tokens=8192,
            sdk_retries=0,
        ),
        evaluation=EvaluationPhaseConfig(
            model="claude-sonnet-5-5",
            reasoning="disabled",
            effort="low",
            input_token_limit=140_000,
            max_output_tokens=8192,
            sdk_retries=0,
        ),
    )


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="HOME_FINDER_",
        extra="ignore",
    )

    # Telegram configuration (required for notifications)
    telegram_bot_token: SecretStr = Field(
        default=SecretStr(""),
        description="Telegram bot token from @BotFather",
    )
    telegram_chat_id: int = Field(
        default=0,
        description="Telegram chat ID to send notifications to",
    )
    telegram_owner_user_id: int | None = Field(
        default=None,
        gt=0,
        description="Callback owner user ID; defaults to a positive private chat ID",
    )

    # TravelTime API configuration (optional, needed for commute filtering)
    traveltime_app_id: str = Field(
        default="",
        description="TravelTime API application ID",
    )
    traveltime_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="TravelTime API key",
    )

    # Anthropic API (optional, needed for property quality analysis)
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Anthropic API key for property quality analysis",
    )

    # Property quality analysis (optional)
    enable_quality_filter: bool = Field(
        default=True,
        description="Enable property quality analysis using Claude vision",
    )
    quality_filter_max_images: int = Field(
        default=20,
        ge=1,
        le=20,
        description="Maximum number of gallery images to analyze per property",
    )
    require_floorplan: bool = Field(
        default=True,
        description="Drop properties without floorplans before quality analysis",
    )
    min_gallery_for_photo_inference: int = Field(
        default=5,
        ge=0,
        description="Minimum gallery images to bypass floorplan gate (0 disables)",
    )
    enable_extended_thinking: bool = Field(
        default=True,
        description="Enable extended thinking for deeper quality analysis",
    )
    analysis_config: Annotated[AnalysisConfig, NoDecode] = Field(
        default_factory=lambda data: new_runtime_analysis_config(
            max_images=data["quality_filter_max_images"],
            enable_extended_thinking=data["enable_extended_thinking"],
        ),
        description="Phase JSON overrides the bounded runtime profile; omitted fields retain "
        "current defaults. Historical configuration decoding is independent.",
    )
    # AI analysis budget
    analysis_spend_policy: SpendPolicy | None = Field(
        default=None,
        description="Exact USD caps as JSON; absent policy defers every paid analysis attempt",
    )
    max_analysis_per_run: int = Field(
        default=75,
        ge=1,
        le=500,
        description="Maximum properties to analyze per pipeline run; excess deferred to next run",
    )
    quality_concurrency: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum concurrent quality analysis API calls",
    )
    property_analysis_timeout: float = Field(
        default=600.0,
        ge=30.0,
        le=900.0,
        description="Max wall-clock seconds per property for quality analysis"
        " (including SDK retries)",
    )

    # Enrichment retry
    max_enrichment_per_run: int = Field(
        default=75,
        ge=1,
        le=500,
        description="Maximum homes to enrich per run, sharing slots between backlog and discovery",
    )
    max_enrichment_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max enrichment retry attempts before giving up on a property",
    )

    # Observability
    event_retention_runs: int = Field(
        default=30,
        ge=1,
        description="Keep property events for the last N pipeline runs",
    )

    # Deduplication
    enable_image_hash_matching: bool = Field(
        default=True,
        description="Enable image hash comparison for cross-platform deduplication",
    )

    # Search criteria
    min_price: int = Field(default=1800, ge=0)
    max_price: int = Field(default=2500, ge=0)
    min_bedrooms: int = Field(default=0, ge=0)
    max_bedrooms: int = Field(default=2, ge=0)
    destination_postcode: str = Field(default="N1 5AA")
    max_commute_minutes: int = Field(default=30, ge=1, le=120)

    # Scraper filters
    furnish_types: str = Field(
        default="unfurnished,part_furnished",
        description="Comma-separated: furnished, unfurnished, part_furnished",
    )
    min_bathrooms: int = Field(
        default=1,
        ge=0,
        description="Minimum number of bathrooms",
    )
    include_let_agreed: bool = Field(
        default=False,
        description="Include properties already let agreed",
    )

    # Search areas (boroughs or outcodes)
    search_areas: str = Field(
        default="e2,e3,e5,e8,e9,e10,e15,e17,n1,n5,n15,n16,n17",
        description="Comma-separated list of boroughs or outcodes to search",
    )

    # Zoopla anti-bot tuning
    zoopla_max_areas_per_run: int = Field(
        default=4,
        ge=1,
        le=20,
        description="Max search areas per Zoopla run (rotate subset to avoid Cloudflare blocks)",
    )

    # Proxy (for accessing geo-restricted sites like Zoopla from outside the UK)
    proxy_url: str = Field(
        default="",
        description="HTTP/SOCKS5 proxy URL (e.g. socks5://user:pass@host:port)",
    )

    # Telegram webhook (enables inline button callbacks when set)
    telegram_webhook_secret: str = Field(
        default="",
        description="Secret token for Telegram webhook validation",
    )

    # Web dashboard
    web_owner_username: str = Field(default="owner", pattern=r"^[A-Za-z0-9._-]{1,64}$")
    web_owner_password: SecretStr = Field(
        default=SecretStr(""),
        description="Owner password (24+ characters); unset or short denies access",
    )
    web_base_url: str = Field(
        default="",
        description="Base URL for web dashboard (e.g. https://home-finder.fly.dev)",
    )
    web_port: int = Field(default=8000, description="Web server port")
    carto_basemap_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="CARTO browser tile key; restrict allowed origins at the provider",
    )
    web_host: str = Field(default="0.0.0.0", description="Web server host")
    pipeline_interval_minutes: int = Field(
        default=55,
        ge=1,
        description="Minimum delay after a serve-mode run finishes, plus 0-5 minutes jitter",
    )

    # Database
    database_path: str = Field(default="data/properties.db")

    @field_validator("analysis_config", mode="before")
    @classmethod
    def _parse_analysis_config(cls, value: Any, info: ValidationInfo) -> Any:
        """Resolve new-run overrides without reinterpreting stored configuration."""
        if isinstance(value, str):
            value = json.loads(value)
        if value is None:
            raise ValueError(
                "analysis_config cannot be null; omit it for runtime defaults or set "
                "enable_quality_filter=false to disable analysis"
            )
        if not isinstance(value, dict):
            # Explicit model instances are already resolved; malformed inputs
            # still pass through the field's ordinary model validation.
            return value
        profile = new_runtime_analysis_config(
            max_images=info.data.get("quality_filter_max_images", 20),
            enable_extended_thinking=info.data.get("enable_extended_thinking", True),
        ).model_dump()
        for key, override in value.items():
            if key in {"visual", "evaluation"} and isinstance(override, dict):
                profile[key] = {**profile[key], **override}
            else:
                profile[key] = override
        return profile

    @model_validator(mode="after")
    def _validate_csv_fields(self) -> "Settings":
        """Eagerly validate CSV config fields at startup."""
        self.get_search_areas()
        self.get_furnish_types()
        return self

    @property
    def data_dir(self) -> str:
        """Return the directory containing the database (for image cache etc)."""
        return str(Path(self.database_path).parent)

    def get_search_areas(self) -> list[str]:
        """Parse search_areas string into a list of area names."""
        areas = [a.strip() for a in self.search_areas.split(",") if a.strip()]
        resolve_area_outcodes(areas)
        return areas

    def get_furnish_types(self) -> tuple[FurnishType, ...]:
        """Parse furnish_types string into FurnishType enum values."""
        return tuple(FurnishType(t.strip()) for t in self.furnish_types.split(",") if t.strip())

    def get_search_criteria(self) -> SearchCriteria:
        """Build SearchCriteria from settings."""
        return SearchCriteria(
            min_price=self.min_price,
            max_price=self.max_price,
            min_bedrooms=self.min_bedrooms,
            max_bedrooms=self.max_bedrooms,
            destination_postcode=self.destination_postcode,
            max_commute_minutes=self.max_commute_minutes,
            transport_modes=(TransportMode.CYCLING, TransportMode.PUBLIC_TRANSPORT),
        )
