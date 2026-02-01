"""Application configuration using pydantic-settings."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from home_finder.models import SearchCriteria, TransportMode


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="HOME_FINDER_",
        extra="ignore",
    )

    # Telegram configuration
    telegram_bot_token: SecretStr = Field(description="Telegram bot token from @BotFather")
    telegram_chat_id: int = Field(description="Telegram chat ID to send notifications to")

    # TravelTime API configuration
    traveltime_app_id: str = Field(description="TravelTime API application ID")
    traveltime_api_key: SecretStr = Field(description="TravelTime API key")

    # Search criteria
    min_price: int = Field(default=1800, ge=0)
    max_price: int = Field(default=2200, ge=0)
    min_bedrooms: int = Field(default=1, ge=0)
    max_bedrooms: int = Field(default=2, ge=0)
    destination_postcode: str = Field(default="N1 5AA")
    max_commute_minutes: int = Field(default=30, ge=1, le=120)

    # Database
    database_path: str = Field(default="data/properties.db")

    # Scraping
    scrape_interval_minutes: int = Field(default=10, ge=1)

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
