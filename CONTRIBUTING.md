# Contributing to Home Finder

Thank you for your interest in contributing to Home Finder! This document provides guidelines and instructions for contributing.

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/marcelcutts/home-finder.git
   cd home-finder
   ```

2. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. **Install dependencies** (including dev dependencies):
   ```bash
   uv sync --all-extras
   ```

4. **Install Playwright browsers**:
   ```bash
   uv run playwright install chromium
   ```

5. **Set up environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your test credentials
   ```

## Code Style

This project uses strict code quality tools:

### Linting & Formatting (ruff)

```bash
# Check for issues
uv run ruff check src tests

# Auto-fix issues
uv run ruff check --fix src tests

# Format code
uv run ruff format src tests
```

### Type Checking (mypy)

```bash
uv run mypy src
```

The project uses `--strict` mode for mypy. All code must be fully typed.

## Testing

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src

# Run specific test file
uv run pytest tests/test_scrapers.py

# Run tests matching a pattern
uv run pytest -k "test_openrent"
```

### Writing Tests

- Place tests in the `tests/` directory
- Use `pytest-asyncio` for async tests (asyncio_mode is set to "auto")
- Use `hypothesis` for property-based testing where appropriate
- Mock external services (TravelTime API, Telegram) in tests

## Pull Request Process

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the code style guidelines

3. **Ensure all checks pass**:
   ```bash
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run mypy src
   uv run pytest
   ```

4. **Commit your changes** with a clear commit message:
   ```bash
   git commit -m "Add feature: description of the feature"
   ```

5. **Push and create a Pull Request**:
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Fill out the PR template** with:
   - Description of changes
   - Testing done
   - Any breaking changes

## Adding a New Scraper

To add support for a new property platform:

1. Create a new file in `src/home_finder/scrapers/`
2. Implement the scraper following the existing patterns
3. Add the scraper to the registry in `src/home_finder/scrapers/__init__.py`
4. Add tests in `tests/test_scrapers.py`
5. Update the README with the new platform

## Reporting Issues

When reporting issues, please include:

- Python version (`python --version`)
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (with sensitive data removed)

## Questions?

Feel free to open an issue for questions about contributing.
