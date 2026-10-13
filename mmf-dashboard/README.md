# Public LSS Weather Observatory

The owner explicitly approved public publication of calculated MMF averages on
2026-10-13. Raw MQTT messages and backend credentials are never published.
Supabase remains server-only; no anonymous table grants or RLS changes are made.

## Data flow

The existing MQTT collector still writes derived records to Supabase and the
raw-plus-summary snapshot to PythonAnywhere. After a successful non-probe run,
it calls `publish-mmf-dashboard.yml`. The publisher reads only the configured
LSS, selects at most 192 captures within the preceding seven days, and fetches
the selected parameter rows using a composite keyset cursor and capped pages.
The export uses an exact field allowlist, finite numeric validation, contribution
checks, pagination bounds and a 20 MiB output cap. Failure prevents deployment;
the previous website remains available. It never commits generated readings.

GitHub Pages serves the static dashboard and `data/averages.json`. This JSON,
CSV downloads and temporary Pages artifacts contain **public averaged data**.
Only the publication job uses the existing Actions backend key. The client has
no Supabase key, database access, broker credentials or raw payloads.

## Features

- Latest per-parameter average, reported units, contributor counts and unit coverage.
- Parameter trend, minimum/maximum and observed capture count; no combined axes
  for unrelated parameters. Source units are retained without conversions.
- Long collection gaps are drawn as breaks, not connected lines.
- Searchable readings, paginated history and CSV exports with UTC timestamps.
- Malaysia/UTC display selector; UTC storage remains unchanged.
- Real data-age indicator, failed-refresh recovery and accessible mobile layout.
- Checks the public file every minute while visible. Data updates only after a
  successful collector/publisher run; GitHub's 15-minute schedule is not guaranteed.
- Windows collector stays disabled. PythonAnywhere delivery continues.

## Activation

1. Repository Settings → Pages → Build and deployment → Source: GitHub Actions.
2. Set repository variable `MMF_DASHBOARD_PUBLIC_ENABLED=true` only for a site
   whose calculated readings the owner approves for public disclosure.
3. Existing `SUPABASE_URL`, `SUPABASE_SECRET_KEY` and `MMF_SITE_CODE` are reused.
4. Run **Publish MMF dashboard** (or push a dashboard source update). Verify its
   deployment, public JSON scope and dashboard against Supabase.
5. Successful future MQTT uploads automatically call the publishing workflow.

The publisher is serialized separately to prevent simultaneous Pages writes.
One-day artifact retention reduces duplicated data retention but does not make
public data private. Historical database rows remain in Supabase beyond the
website's bounded window. No capture/parameter schema migration is needed.

## Checks and local preview

```text
python -m unittest discover -s pythonanywhere-mmf -v
node --test mmf-dashboard/model.test.mjs
node --check mmf-dashboard/app.js
```

Serve this directory with a standard static HTTP server. Put a **synthetic**
fixture at the ignored `data/averages.json` path for local UI testing. The
production file is generated only on the GitHub runner. No frontend build or
third-party runtime libraries are needed.

References: [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[PostgREST filtering](https://docs.postgrest.org/en/stable/references/api/tables_views.html),
[Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys).
