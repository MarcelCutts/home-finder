"""Application configuration using pydantic-settings."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from home_finder.models import FurnishType, SearchCriteria, TransportMode


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
        default=10,
        ge=1,
        le=20,
        description="Maximum number of gallery images to analyze per property",
    )
    require_floorplan: bool = Field(
        default=True,
        description="Drop properties without floorplans before quality analysis",
    )

    # Deprecated: use enable_quality_filter instead
    enable_floorplan_filter: bool = Field(
        default=True,
        description="DEPRECATED: Use enable_quality_filter instead",
    )

    # Deduplication
    enable_image_hash_matching: bool = Field(
        default=False,
        description="Enable image hash comparison for cross-platform deduplication",
    )

    # Search criteria
    min_price: int = Field(default=1800, ge=0)
    max_price: int = Field(default=2200, ge=0)
    min_bedrooms: int = Field(default=1, ge=0)
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

    # Database
    database_path: str = Field(default="data/properties.db")

    # Scraping
    scrape_interval_minutes: int = Field(default=10, ge=1)

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
