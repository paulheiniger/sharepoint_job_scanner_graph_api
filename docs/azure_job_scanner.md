# Azure scheduled job scanner

The daily scanner should run as an Azure Container Apps **scheduled job**, not
as another continuously running API. The job uses the existing
`scripts/daily_refresh.sh` command and writes authoritative scan state and
business records to PostgreSQL/Neon.

## Persistence boundary

`sharepoint_delta_state` and the resolved Graph item identifiers already live
in PostgreSQL. A missing local cache therefore does **not** make Graph start an
unrestricted full traversal. However, the current incremental parsers map
changed files back to `.jobscan_manifest.json` and reparse the affected cached
job folder. Until that parser is made cache-independent, mount an Azure Files
share at `/mnt/spraytec-scanner` and seed it from the current primary scanner.

Persist these paths:

- `/mnt/spraytec-scanner/cache/sharepoint`
- `/mnt/spraytec-scanner/cache/office_timesheets`
- `/mnt/spraytec-scanner/cache/warranty_sources`
- `/mnt/spraytec-scanner/cache/warranty_master`
- `/mnt/spraytec-scanner/output`
- `/mnt/spraytec-scanner/locks`

The entrypoint refuses to run a real refresh when no SharePoint cache manifest
is present. This turns an incomplete cache migration into a visible failed job
instead of a successful but incomplete refresh.

## Runtime configuration

Configure these Container Apps Job secrets or environment variables:

- `DATABASE_URL` (or `NEON_DATABASE_URL`)
- `MS_TENANT_ID`
- `MS_CLIENT_ID`
- `MS_CLIENT_SECRET`
- `SHAREPOINT_SITE_URL` (optional; defaults to the Data site)
- `SHAREPOINT_LIBRARY` (optional; defaults to `Documents`)
- `RUN_QUICKBOOKS_SYNC=1` only after the QuickBooks production connection is
  ready for unattended refreshes

Use one replica, completion count one, and parallelism one. Set a timeout long
enough for document extraction and SQL materialization. Keep the persistent
lock path enabled as a second overlap guard.

Azure scheduled-job cron expressions use UTC. Choose the UTC schedule
deliberately around daylight-saving changes rather than assuming
`America/New_York` is supported.

## Cutover order

1. Build and push `services/job_scanner/Dockerfile`.
2. Create an Azure Files share and register it with the existing
   `spraytec-ai-env` Container Apps environment.
3. Create `spraytec-job-scanner` with the volume mounted at
   `/mnt/spraytec-scanner`, but do not enable the production schedule yet.
4. Seed the share from the current primary scanner's `.cache/sharepoint`,
   `.cache/office_timesheets`, warranty caches, and `output` directories.
5. Start one manual job execution and verify its exit status, logs,
   `sharepoint_incremental_runs`, delta state, document extraction status, and
   dashboard snapshot timestamps.
6. Enable the schedule and leave the Mac Studio refresh disabled-but-ready for
   one cycle as rollback.
7. After at least two successful scheduled Azure runs, remove the old launchd
   schedule. Do not delete the old cache until the rollback window closes.

The GitHub workflow updates the image of an already bootstrapped job on pushes
to `main`; it intentionally does not create storage or copy secrets.
