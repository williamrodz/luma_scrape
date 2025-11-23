# ⚡ LUMA Energy Grid Scraper

This is a simple scheduled web scraper that pulls data from the [LUMA System Overview](https://lumapr.com/system-overview/?lang=en) page and logs it for analysis.

## 🛠 Setup

### 1. Clone the Repo

```bash
git clone https://github.com/your-username/luma-scraper.git
cd luma-scraper
```

### 2. Install uv (if not already installed)

```bash
# On macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via Homebrew
brew install uv
```

### 3. Sync Dependencies

```bash
uv sync --no-install-project
```

This will create a `.venv` virtual environment and install all dependencies. The `--no-install-project` flag is needed because this is a script-based project (not a package), so we only need to install dependencies, not the project itself.

### 4. Activate Virtual Environment

For Fish shell:
```bash
source .venv/bin/activate.fish
```

For bash/zsh:
```bash
source .venv/bin/activate
```

After activating, you can run your scripts normally:
```bash
python scrape_luma_grid_status.py
```

Alternatively, you can use `uv run --no-project` to run commands without activating the venv:
```bash
uv run --no-project python scrape_luma_grid_status.py
```

## 📝 Notes

- The `uv.lock` file should be committed to the repository to ensure reproducible builds across different environments.
- The old `venv/` directory can be removed after migration is complete.