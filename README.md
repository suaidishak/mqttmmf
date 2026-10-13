# GitHub Actions setup and cutover

For the optional cross-station averaging and Supabase sink, follow
`SUPABASE_SETUP.md`. The snapshot retains raw messages and adds an explicitly
labelled derived summary. Supabase writes require separate configuration.

The workflow collects MMF1–MMF4 for 180 seconds, uploads one JSON snapshot,
then exits. It never writes live payloads to Git, runner files, caches or
artifacts. Logs contain activity and station counts only. Missing stations or
a failed subscription cause a nonzero exit and leave the remote file unchanged.
Retained readings keep their unverified-age label. This does not guarantee
every parameter or exact 15-minute delivery; GitHub schedules can be delayed
or dropped, and publications outside the collection window are missed.

## Configure the repository

Under Settings → Secrets and variables → Actions, add repository **secrets**:

- `MQTT_HOST`: the broker host from the private connection configuration.
- `MQTT_USERNAME`: broker username.
- `MQTT_PASSWORD`: broker password.
- `PYTHONANYWHERE_API_TOKEN`: PythonAnywhere Account → API Token.
- `PYTHONANYWHERE_USERNAME`: the destination account username.

Do not put these in workflow YAML, public issues or chat messages. The Windows
DPAPI-encrypted file is bound to the local user and cannot be used by a Linux
runner. Enter the original values directly in GitHub's secret fields.

Add a repository **variable** `PYTHONANYWHERE_REMOTE_FILE` with the absolute
destination path. For verification use
`/home/<account>/mysite/mqttmmf/mmf_github_candidate.json` to avoid replacing
the file still written by the Windows collector. Only a path within the
configured account home is accepted; `sample_files` is rejected.

Optional variables: `MQTT_PORT` (1883), `MQTT_TLS` (`false` or `true`),
`MQTT_TOPIC_ROOT` (BDNS), `MMF_SITE_CODE` (LSS-BIDOR).
Leave `MMF_SCHEDULE_ENABLED` unset initially.

## Verify before switching

1. In Actions, select **MQTT MMF to PythonAnywhere** → **Run workflow**.
   Keep `probe_only` checked. The probe connects for three minutes and reports
   `stationsWithData` and `stationsWithRetainedData`, without uploading.
2. All four stations must be present and the subscription must succeed.
   If data is not retained, judge whether publications are frequent enough
   during the window; one successful probe alone does not guarantee coverage
   between future runs. Keep the continuous collector if stations are missing.
3. Run again with `probe_only` unchecked. Confirm the candidate file arrives
   and has messages for all four stations. Repeat to confirm replacement.
4. After successful verification, stop the Windows collector and disable its
   Startup shortcut before switching the destination variable to
   `/home/<account>/mysite/mqttmmf/mmf_latest.json`.
5. Set repository variable `MMF_SCHEDULE_ENABLED` to `true`. Runs are requested
   every 15 minutes, at UTC minutes 02, 17, 32 and 47, followed by collection
   and upload. Confirm two scheduled uploads and check their UTC `snapshotAt`.

GitHub Actions logs provide activity for each run. This public repository's
workflow logs must not include credentials or operational readings. GitHub
can disable schedules in inactive public repositories after 60 days; monitor
failures and schedule state. Review Actions runner-minute allowances for the
chosen repository plan. Use an always-on host if strict delivery or continuous
MQTT coverage is required.

## Files to publish

Only `.github/workflows/mqtt-mmf.yml`, `pythonanywhere-mmf/collector.py`,
`pythonanywhere-mmf/requirements.txt`, `pythonanywhere-mmf/test_collector.py`,
`pythonanywhere-mmf/averages.py`, `pythonanywhere-mmf/supabase_writer.py`,
`pythonanywhere-mmf/test_averages.py`, `SUPABASE_SETUP.md`, the new Supabase
migration and this setup guide are needed. Do not publish the wider private-data
prototype, Windows credential files, generated data, or runtime configuration.

References: [GitHub scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets),
[PythonAnywhere upload API](https://help.pythonanywhere.com/pages/API/#files).
