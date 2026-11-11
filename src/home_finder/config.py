"""Application configuration using pydantic-settings."""

import json
import re
from datetime import time
from os import PathLike
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from dotenv.parser import parse_stream
from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

from home_finder.data.location_mappings import resolve_area_outcodes
from home_finder.models import FurnishType, SearchCriteria, TransportMode
from home_finder.models.analysis import AnalysisConfig, EvaluationPhaseConfig, VisualPhaseConfig
from home_finder.models.notifications import NotificationPolicy
from home_finder.models.spend import SpendPolicy


def _validate_dotenv_syntax(source: DotEnvSettingsSource) -> None:
    """Reject invalid bindings discarded by Pydantic's permissive dotenv loader."""
    env_files = source.env_file
    if env_files is None:
        return
    if isinstance(env_files, (str, PathLike)):
        env_files = [env_files]
    for env_file in env_files:
        path = Path(env_file).expanduser()
        if not path.is_file():
            continue
        with path.open(encoding=source.env_file_encoding or "utf-8") as stream:
            for binding in parse_stream(stream):
                if binding.error:
                    raise SettingsError(
                        f"Invalid dotenv syntax in {path} at line {binding.original.line}. "
                        "Check quoting and assignments."
                    )


def new_runtime_analysis_config(
    *, max_images: int = 20, enable_extended_thinking: bool = True
) -> AnalysisConfig:
    """New-run defaults, separate from historical configuration deserialization.

    Reserve 111k for visual text/schema/framing, plus 5.5k per configured image
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
            input_token_limit=111_000 + 5_500 * (max_images + 1),
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
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        if isinstance(dotenv_settings, DotEnvSettingsSource):
            _validate_dotenv_syntax(dotenv_settings)
        # Pydantic ignores unknown process variables even with extra="forbid".
        # Check our namespace in both sources, while allowing other tools' keys.
        for source in (env_settings, dotenv_settings):
            if isinstance(source, EnvSettingsSource):
                prefix = source.env_prefix.lower()
                unknown = sorted(
                    name.upper()
                    for name in source.env_vars
                    if name.lower().startswith(prefix)
                    and name.lower()[len(prefix) :] not in settings_cls.model_fields
                )
                if unknown:
                    raise SettingsError(
                        f"Unknown Home Finder setting(s): {', '.join(unknown)}. "
                        "See .env.example for supported names."
                    )
        return init_settings, env_settings, dotenv_settings, file_secret_settings

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

    # TravelTime API configuration (optional, needed for advisory commute assessment)
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
        description="Require a source-declared floorplan, an exempt source, "
        "or enough photos before analysis",
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
    notification_policy: NotificationPolicy = Field(
        default_factory=NotificationPolicy,
        description="Versioned discovery selection; default minimum fit 60 with rated evidence",
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
        description="Legacy default for each assessment queue cap; "
        "use the explicit initial/reanalysis limits",
    )
    max_initial_analyses_per_run: int = Field(
        default_factory=lambda data: data["max_analysis_per_run"],
        ge=1,
        le=500,
        description="Maximum initial assessments admitted per run, "
        "sharing slots between discovery and recovery",
    )
    max_reanalyses_per_run: int = Field(
        default_factory=lambda data: data["max_analysis_per_run"],
        ge=1,
        le=500,
        description="Maximum requested reassessments admitted per run, "
        "independently of initial assessments",
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
    commute_arrival_time: time = Field(
        default=time(9, 0),
        description=(
            "London clock time (HH:MM) that measured commutes arrive by, on the first "
            "weekday that time is still ahead when the run measures commutes"
        ),
    )
    brief_must_haves: str = Field(
        default="workspace,hosting",
        description=(
            "Comma-separated fit dimensions that are must-haves, so a weak one leads the "
            "listing page: workspace, hosting, sound, kitchen, vibe, condition. The rest "
            "are nice-to-haves. Rent, bedrooms and commute are always must-haves."
        ),
    )

    # Scraper filters
    furnish_types: str = Field(
        default="unfurnished,part_furnished",
        description="Source search hint: furnished, unfurnished, part_furnished; "
        "support varies by portal (see README)",
    )
    min_bathrooms: int = Field(
        default=1,
        ge=0,
        description="Source search hint for minimum bathrooms; not enforced on every portal",
    )
    include_let_agreed: bool = Field(
        default=False,
        description="Request let-agreed search results where supported; "
        "does not override later off-market exclusion",
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
    proxy_url: SecretStr = Field(
        default=SecretStr(""),
        description="HTTP/SOCKS5 proxy URL (e.g. socks5://user:pass@host:port)",
    )

    # Telegram webhook (enables inline button callbacks when set)
    telegram_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
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
        description="Canonical HTTP(S) dashboard origin, without path, query or credentials",
    )
    web_port: int = Field(default=8000, ge=1, le=65535, description="Web server port")
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

    @field_validator("web_base_url")
    @classmethod
    def _web_origin(cls, value: str) -> str:
        if not value:
            return value
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError:
            raise ValueError("web_base_url has an invalid host or port") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or "?" in value
            or "#" in value
            or port == 0
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError("web_base_url must be an HTTP(S) origin without credentials or a path")
        return value.rstrip("/")

    @field_validator("proxy_url")
    @classmethod
    def _proxy_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            return value
        try:
            url = urlsplit(raw)
            port = url.port
        except ValueError:
            raise ValueError("proxy_url has an invalid host or port") from None
        if (
            url.scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
            or not url.hostname
            or url.path not in {"", "/"}
            or "?" in raw
            or "#" in raw
            or port == 0
            or any(c.isspace() or ord(c) < 32 for c in raw)
        ):
            raise ValueError("proxy_url must be an HTTP(S) or SOCKS proxy URL")
        return value

    @field_validator("telegram_webhook_secret")
    @classmethod
    def _webhook_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", raw):
            raise ValueError("telegram_webhook_secret must contain 1-256 letters, digits, _ or -")
        return value

    @field_validator("database_path", "web_host", "destination_postcode")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("brief_must_haves")
    @classmethod
    def _known_must_haves(cls, value: str) -> str:
        """Only fit dimensions can be must-haves; a typo must not silently drop one."""
        from home_finder.filters.fit_score import WEIGHTS

        unknown = [
            p.strip() for p in value.split(",") if p.strip() and p.strip().lower() not in WEIGHTS
        ]
        if unknown:
            raise ValueError(
                f"brief_must_haves has unknown dimensions; choose from {', '.join(WEIGHTS)}"
            )
        return value

    @field_validator("commute_arrival_time")
    @classmethod
    def _london_clock_time(cls, value: time) -> time:
        """An offset would contradict London's own GMT/BST switch, so refuse one."""
        if value.tzinfo is not None:
            raise ValueError(
                "commute_arrival_time is a London clock time; give HH:MM without an offset"
            )
        return value

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
    def _validate_search(self) -> "Settings":
        """Validate the same search contract before any mode starts work."""
        try:
            self.get_search_areas()
        except ValueError:
            raise ValueError(
                "Unknown search area in search_areas; use supported outcodes or borough names"
            ) from None
        try:
            self.get_furnish_types()
        except ValueError:
            raise ValueError(
                "furnish_types must contain furnished, unfurnished or part_furnished"
            ) from None
        if self.min_price > self.max_price:
            raise ValueError("min_price must be <= max_price")
        if self.min_bedrooms > self.max_bedrooms:
            raise ValueError("min_bedrooms must be <= max_bedrooms")
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
