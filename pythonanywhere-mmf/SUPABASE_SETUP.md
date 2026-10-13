# Derived MMF averages in Supabase

The collector produces one capture per LSS and actual UTC capture timestamp.
Each parameter's mean uses one latest valid reading per station providing that
parameter. It does not average all publications during the collection window.

For example, `mw` readings 0, 4, 8, 12 from four stations produce 6 with
`contributingStations=4`. A temperature reported only by two stations at 30
and 34 produces 32 with `contributingStations=2`. Missing values are not zero.

Raw source messages remain unchanged in the PythonAnywhere snapshot. The file
also includes a labelled `derivedSummary`; only the calculated summary values
and their counts/units are sent to Supabase. Nothing writes live source data
or credentials into Git or the Sites D1 database.

## Rules

- Parameter codes come from source object keys or the final topic component.
  No fixed parameter list, case conversion, alias guesses or unit conversions.
- Latest receipt time per station and parameter wins. Direct parameter topics
  win receipt-time ties over the combined `MMF` mirror, so a station never gets
  two votes for the same parameter.
- Boolean, null, text, non-finite numeric values and metadata are excluded.
  Retained readings and messages older than 15 minutes are excluded from means
  because their measurement age is unverified.
- Conflicting reported units exclude that parameter. A missing unit does not
  become an assumed unit: `stationsWithUnit` records how many contributors had
  an explicit unit. Review unit completeness before engineering interpretation.
- All parameters use an arithmetic mean as requested, including directional
  values. This is not a circular wind-direction mean or an approved weather
  quality-control model. Such rules require a subsequent explicit change.
- `capturedAt` is the collector's actual snapshot time, not a source measurement
  timestamp or a fabricated regular quarter-hour timestamp.
- Configure `MQTT_STATIONS` with the expected comma-separated stations for the
  LSS (for example `MMF1,MMF2`). Other station topics are excluded. Missing an
  expected station still fails the capture, but missing individual parameters
  on a present station is valid. Station count is configurable from 1 to 64;
  memory and RPC safety limits are 4,096 topics and 1,024 parameters per capture.

## Schema and access

The new CLI-generated migration is under `supabase/migrations/`:

- `mmf_captures`: composite primary key `(site_code, captured_at)`, station
  count, aggregation method, timestamp basis and insertion time.
- `mmf_parameter_averages`: one typed numeric row per parameter, key
  `(site_code, captured_at, parameter_code)`, mean, denominator, unit and unit
  coverage. A composite foreign key links each value to its capture.

New parameters add rows rather than columns, so schema migrations are not
needed for every weather-station variation. The capture primary key supports
latest captures and date ranges per LSS. The parameter primary key supports
fetching all values in one capture, while the additional B-tree on
`(site_code, parameter_code, captured_at DESC)` supports parameter time series.
Do not add an index for every observed parameter name.

Both tables enable RLS and revoke public, anonymous and signed-in-client access.
Only trusted server-side `service_role` access is provisioned. No blanket
authenticated-user read policy is added without an application authorization
model. A future application should use an authorized backend or site-specific
RLS policies, never a secret key in browser code.

`store_mmf_capture` validates the bounded parameter list and inserts the capture
and all parameter rows in one transaction. Its invoker-rights RPC is restricted
to `service_role`. Retries with the same LSS, timestamp and values acknowledge
the existing record. A different payload at the same key is rejected; the
collector doesn't silently overwrite historical observations.

## Configure GitHub after the database is provisioned

1. Select the intended Supabase project; apply the new migration and verify its
   tables, function permissions, RLS, synthetic retry/validation tests and plans.
2. Add the project's `sb_secret_...` backend key as the Actions repository secret
   `SUPABASE_SECRET_KEY`. Enter it in GitHub's secret field, not chat or source.
3. Set repository variable `SUPABASE_URL` to the project's HTTPS API base URL.
4. Set `MMF_SUPABASE_ENABLED=true`. Until this is set, the collector continues
   PythonAnywhere delivery and calculates the summary but does not write to DB.
5. Run a manual upload and verify a capture plus its parameter rows in Supabase.
   Observe scheduled captures too. A DB write failure returns nonzero and logs
   generic failures without data/key values. The raw-file upload follows the
   confirmed DB write; if the file upload fails, the DB capture remains valid.

The current integration uses the HTTPS Data API with a modern secret key in
the `apikey` header, not as a JWT `Authorization` bearer. No Supabase SDK or
direct database connection pool is needed for these small periodic writes.

## Query patterns and scaling

Provisioned and tested on the owner's **LSS Repository** project. Atomic insert,
identical retry, conflict rejection, contributor-count validation, empty inputs
and client privilege checks passed in a transaction that was rolled back. A
20,000-row temporary synthetic table using the same index design produced an
index-only scan for the demonstrated bounded time-series query, with 0.057 ms
reported execution time. This is a small synthetic query-plan check, not an
application load test or a guarantee at production scale.

Advisors reported informational notices only: [RLS with no client policies](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
is intentional for server-only access, and [unused series index](https://supabase.com/docs/guides/database/database-linter?lint=0005_unused_index)
is expected before application reads. No warning/error findings were returned.

Use parameterized application queries with bounded UTC time ranges, keyset
pagination and a page limit of 100 by default, at most 1,000. For example:

```sql
-- Parameter trend; use the series index and continue with the oldest returned
-- timestamp instead of using large OFFSET values.
select captured_at, average_value, contributing_stations, unit
from public.mmf_parameter_averages
where site_code = $1 and parameter_code = $2
  and captured_at >= $3 and captured_at < $4
  and captured_at < $5
order by captured_at desc
limit $6;

-- All parameters in a single capture; use the primary key's first two columns.
select parameter_code, average_value, contributing_stations, unit
from public.mmf_parameter_averages
where site_code = $1 and captured_at = $2
order by parameter_code;
```

Do not query the entire history for every page, dynamically pivot every
parameter into columns, or introduce JSON containment indexes unless the
application's actual query patterns need them. At the nominal cadence there
are 35,040 captures per LSS per year and that count times the number of reported
parameters in the value table. Monitor query plans, storage, vacuum and latency;
add retention/partitioning based on measured volume. Indexing is a sound
baseline, not a guarantee of zero performance issues at arbitrary scale.

The existing GitHub schedule requests 15-minute runs, but observed run times
have large gaps. GitHub Actions cannot guarantee the required real-time cadence;
use a dependable scheduler or always-on collector when exact cadence matters.

References: [Supabase secret keys](https://supabase.com/docs/guides/getting-started/api-keys),
[RLS](https://supabase.com/docs/guides/database/postgres/row-level-security),
[query optimization](https://supabase.com/docs/guides/database/query-optimization).
