# Home Finder

Multi-platform London rental property scraper with commute filtering, AI quality analysis, and Telegram notifications.

## Features

- **Multi-platform scraping**: OpenRent, Rightmove, Zoopla, OnTheMarket
- **Cross-platform deduplication**: Weighted multi-signal matching merges the same property listed on different platforms
- **Commute filtering**: Filter properties within X minutes of your destination using TravelTime API
- **AI quality analysis**: Claude vision analyzes property images for condition, kitchen, space, and value
- **Floorplan gating**: Optionally require properties to have floorplans before notifying
- **Rich Telegram notifications**: Property cards with photos, star ratings, commute times, quality summaries, and direct links
- **Detail enrichment**: Fetches gallery images, floorplans, and descriptions from property detail pages
- **SQLite storage**: Track seen properties to only notify about new listings, with notification retry on failure

## Quick Start

1. **Install dependencies**:

   ```bash
   uv sync --all-extras
   ```

2. **Configure environment**:

   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

3. **Run**:
   ```bash
   uv run home-finder
   ```

### Run Modes

```bash
uv run home-finder                    # Full pipeline with Telegram notifications
uv run home-finder --dry-run          # Full pipeline, save to DB but no notifications
uv run home-finder --scrape-only      # Just scrape and print (no filtering/storage)
uv run home-finder --max-per-scraper 5  # Limit results per scraper (for testing)
```

## Configuration

Create a `.env` file with these settings (all use `HOME_FINDER_` prefix):

### Required

- `HOME_FINDER_TELEGRAM_BOT_TOKEN`: Your Telegram bot token (from @BotFather)
- `HOME_FINDER_TELEGRAM_CHAT_ID`: Your Telegram chat ID

### Optional APIs

- `HOME_FINDER_TRAVELTIME_APP_ID`: TravelTime API app ID (enables commute filtering)
- `HOME_FINDER_TRAVELTIME_API_KEY`: TravelTime API key
- `HOME_FINDER_ANTHROPIC_API_KEY`: Anthropic API key (enables AI quality analysis)

### Feature Flags

- `HOME_FINDER_ENABLE_QUALITY_FILTER`: Enable Claude vision property analysis (default: true)
- `HOME_FINDER_REQUIRE_FLOORPLAN`: Drop properties without floorplans (default: true)
- `HOME_FINDER_QUALITY_FILTER_MAX_IMAGES`: Max gallery images to analyze per property (default: 10, max: 20)
- `HOME_FINDER_ENABLE_IMAGE_HASH_MATCHING`: Enable perceptual image hashing for deduplication (default: false)

### Search Criteria

- `HOME_FINDER_MIN_PRICE`: Minimum monthly rent (default: 1800)
- `HOME_FINDER_MAX_PRICE`: Maximum monthly rent (default: 2200)
- `HOME_FINDER_MIN_BEDROOMS`: Minimum bedrooms (default: 1)
- `HOME_FINDER_MAX_BEDROOMS`: Maximum bedrooms (default: 2)
- `HOME_FINDER_DESTINATION_POSTCODE`: Your destination postcode (default: N1 5AA)
- `HOME_FINDER_MAX_COMMUTE_MINUTES`: Maximum commute time in minutes (default: 30)

### Scraper Filters

- `HOME_FINDER_FURNISH_TYPES`: Comma-separated furnishing filter (default: unfurnished,part_furnished)
- `HOME_FINDER_MIN_BATHROOMS`: Minimum bathrooms (default: 1)
- `HOME_FINDER_INCLUDE_LET_AGREED`: Include already-let properties (default: false)

### Other

- `HOME_FINDER_DATABASE_PATH`: SQLite database path (default: data/properties.db)

## Getting API Keys

### Telegram Bot

1. Message @BotFather on Telegram
2. Send `/newbot` and follow instructions
3. Copy the bot token to your `.env`
4. Send a message to your bot
5. Get your chat ID from `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`

### TravelTime API (Optional)

1. Sign up at https://traveltime.com/
2. Get your App ID and API Key from the dashboard
3. Add to your `.env`

### Anthropic API (Optional)

1. Get an API key at https://console.anthropic.com/
2. Add to your `.env` as `HOME_FINDER_ANTHROPIC_API_KEY`
3. Enables AI-powered property quality analysis using Claude vision

## Running Tests

```bash
uv run pytest                         # Run all tests (slow tests excluded by default)
uv run pytest tests/test_models.py    # Run specific test file
uv run pytest -k "test_openrent"      # Run tests matching pattern
uv run pytest --cov=src               # Run with coverage
uv run pytest -m slow                 # Run slow tests (real scraping)
```

## Project Structure

```
src/home_finder/
├── scrapers/              # Platform scrapers
│   ├── base.py            # Abstract BaseScraper interface
│   ├── openrent.py        # OpenRent (crawlee)
│   ├── rightmove.py       # Rightmove (crawlee + typeahead API)
│   ├── zoopla.py          # Zoopla (Playwright for Cloudflare bypass)
│   ├── onthemarket.py     # OnTheMarket (curl_cffi)
│   ├── zoopla_models.py   # Pydantic models for Zoopla JSON parsing
│   ├── detail_fetcher.py  # Gallery/floorplan extraction from detail pages
│   └── location_utils.py  # Outcode detection utilities
├── filters/               # Filtering and analysis
│   ├── criteria.py        # Price/bedroom filtering
│   ├── commute.py         # TravelTime API commute filtering
│   ├── deduplication.py   # Weighted multi-signal cross-platform deduplication
│   ├── location.py        # Location validation (catches scraper leakage)
│   ├── detail_enrichment.py  # Enriches merged properties with images/descriptions
│   ├── floorplan.py       # Floorplan analysis (legacy)
│   └── quality.py         # Claude vision property quality analysis
├── notifiers/
│   └── telegram.py        # Rich Telegram notifications with quality cards
├── db/
│   └── storage.py         # SQLite storage with notification tracking
├── utils/
│   ├── address.py         # Address normalization for deduplication
│   └── image_hash.py      # Perceptual image hashing
├── models.py              # Pydantic models (Property, MergedProperty, etc.)
├── config.py              # Settings management (pydantic-settings)
├── logging.py             # Structured logging (structlog)
└── main.py                # Pipeline orchestration and CLI
```

## Deployment

### Fly.io (recommended)

Deploys to London (`lhr`) for UK IP, with supercronic for cron scheduling:

```bash
fly apps create home-finder
fly volumes create home_finder_data --region lhr --size 1
fly secrets set HOME_FINDER_TELEGRAM_BOT_TOKEN=xxx HOME_FINDER_TELEGRAM_CHAT_ID=xxx ...
fly deploy
```

### Docker (local)

```bash
docker build -t home-finder .
docker run --env-file .env -v ./data:/app/data home-finder
```

### systemd timer

Create `/etc/systemd/system/home-finder.service`:

```ini
[Unit]
Description=Home Finder Property Scraper
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/home-finder
ExecStart=/path/to/uv run home-finder
User=your-user

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/home-finder.timer`:

```ini
[Unit]
Description=Run Home Finder every 55 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=55min

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl enable --now home-finder.timer
```

### cron

```bash
*/55 * * * * cd /path/to/home-finder && /path/to/uv run home-finder >> /var/log/home-finder.log 2>&1
```

## License

MIT
