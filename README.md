# Home Finder

Multi-platform London rental property scraper with commute filtering and Telegram notifications.

## Features

- **Multi-platform scraping**: OpenRent, Rightmove, Zoopla, OnTheMarket
- **Commute filtering**: Filter properties within X minutes of your destination using TravelTime API
- **Telegram notifications**: Get instant alerts for matching properties
- **Deduplication**: Avoid seeing the same property from multiple platforms
- **SQLite storage**: Track seen properties to only notify about new listings

## Quick Start

1. **Install dependencies**:
   ```bash
   uv sync
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

## Configuration

Create a `.env` file with these settings:

### Required
- `HOME_FINDER_TELEGRAM_BOT_TOKEN`: Your Telegram bot token (from @BotFather)
- `HOME_FINDER_TELEGRAM_CHAT_ID`: Your Telegram chat ID

### Optional (enables commute filtering)
- `HOME_FINDER_TRAVELTIME_APP_ID`: TravelTime API app ID
- `HOME_FINDER_TRAVELTIME_API_KEY`: TravelTime API key

### Search Criteria
- `HOME_FINDER_MIN_PRICE`: Minimum monthly rent (default: 1800)
- `HOME_FINDER_MAX_PRICE`: Maximum monthly rent (default: 2200)
- `HOME_FINDER_MIN_BEDROOMS`: Minimum bedrooms (default: 1)
- `HOME_FINDER_MAX_BEDROOMS`: Maximum bedrooms (default: 2)
- `HOME_FINDER_DESTINATION_POSTCODE`: Your destination postcode (default: N1 5AA)
- `HOME_FINDER_MAX_COMMUTE_MINUTES`: Maximum commute time (default: 30)

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

## Running Tests

```bash
uv run pytest
```

With coverage:
```bash
uv run pytest --cov=src
```

## Project Structure

```
src/home_finder/
├── scrapers/          # Platform scrapers (OpenRent, Rightmove, etc.)
├── filters/           # Criteria and commute filtering
├── notifiers/         # Telegram notifications
├── db/                # SQLite storage
├── models.py          # Pydantic models
├── config.py          # Settings management
├── logging.py         # Structured logging
└── main.py            # Main entry point
```

## Deployment

For continuous monitoring, run with a scheduler:

### systemd timer (recommended)
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
Description=Run Home Finder every 10 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
```

Enable:
```bash
sudo systemctl enable --now home-finder.timer
```

### cron
```bash
*/10 * * * * cd /path/to/home-finder && /path/to/uv run home-finder >> /var/log/home-finder.log 2>&1
```

## License

MIT
