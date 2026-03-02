# FichasIA

## Cursor Cloud specific instructions

**Overview:** FichasIA is a single-file Python Flask app (`app.py`) that generates real estate listing sheets ("fichas inmobiliarias") by scraping property portals (ZonaProp, Argenprop, MercadoLibre) using Playwright/Chromium and optionally publishing them to Netlify.

### Running the app

```
python3 app.py
```

Runs on `http://localhost:8080`. Default credentials: `admin` / `admin123`. User data is stored in `users.json` (auto-created on first run).

### Key caveats

- `~/.local/bin` must be on `PATH` for the `playwright` CLI to work (pip installs there as non-root). This is already configured in `~/.bashrc`.
- Playwright Chromium must be installed before the scraping feature works: `python3 -m playwright install chromium && python3 -m playwright install-deps chromium`.
- There is no test suite, no linter configuration, and no build step. The codebase is vanilla Python + HTML templates.
- The Netlify token is per-user and optional; ficha generation works without it, but the HTML won't be published online.
- `users.json` is gitignored by default behavior (not committed); deleting it resets all users to the default admin account.
