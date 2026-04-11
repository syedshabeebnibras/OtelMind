# Deployment runbook

Hybrid deploy:
- **Dashboard** → Vercel (`otelmind-dashboard.vercel.app`)
- **Backend API + workers** → Railway (`otelmind-api-production.up.railway.app`)
- **Database** → Neon Postgres (existing)

All five background loops (partition maintenance, eval worker, trace
auto-scorer, daily golden regression, watchdog) run inside the Railway
container — Vercel's request-scoped model can't host them.

---

## One-time setup (already done, for reference)

### Railway
1. `railway init --name otelmind-backend`
2. `railway add --service otelmind-api` (empty service, no DB attached)
3. Set all env vars via `railway variables --set KEY=VALUE` — see
   [Env vars](#env-vars) below.
4. `railway up --service otelmind-api --ci`
5. `railway domain --service otelmind-api` → produces public URL.

### Vercel
1. Generate a token at <https://vercel.com/account/settings/tokens>.
2. `VERCEL_TOKEN=... vercel link --project otelmind-dashboard --yes --scope syedshabeebnibras-projects`
   (run from `dashboard/`).
3. `echo "<railway-url>" | vercel env add NEXT_PUBLIC_API_URL production`
4. `echo "<api-key>" | vercel env add NEXT_PUBLIC_OTELMIND_API_KEY production`
5. `vercel deploy --prod --yes`

---

## Daily operations

### Redeploy the backend after code changes

```bash
railway up --service otelmind-api --ci
```

Railway reuses the Docker build cache, so most deploys finish in ~30
seconds. Migrations run automatically on container start via the
`railway` stage entrypoint in `Dockerfile`.

### Redeploy the dashboard

```bash
cd dashboard
VERCEL_TOKEN=<token> vercel deploy --prod --yes
```

Or set up a GitHub integration in the Vercel dashboard for automatic
deploys on push to `main`.

### Watch backend logs live

```bash
railway logs --service otelmind-api
```

**Gotcha:** Railway CLI only returns build-phase logs for *failed*
deployments. If a deploy is stuck "unhealthy," temporarily remove
`healthcheckPath` from `railway.json`, push, and the container's real
stdout shows up in `railway logs`. Re-enable the healthcheck once
you've found the crash.

### Roll back

```bash
# Via web dashboard (fastest)
railway open

# Or via CLI — list previous deployments, redeploy a specific one
railway status --json | jq
```

---

## Env vars

### Railway (backend)

| Name | Value | Notes |
|---|---|---|
| `POSTGRES_HOST` | `ep-aged-voice-*.neon.tech` | Neon pooler host |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `neondb` | |
| `POSTGRES_USER` | `neondb_owner` | |
| `POSTGRES_PASSWORD` | `<secret>` | Rotate via Neon console |
| `LLM_PROVIDER` | `openai` | |
| `LLM_MODEL` | `gpt-4o` | Used by `LLMJudge` |
| `LLM_API_KEY` | `<openai-key>` | Rotate via OpenAI console |
| `PORT` | `8000` | **Must match `EXPOSE 8000` in Dockerfile's `railway` stage.** Railway's edge proxy routes traffic to whatever EXPOSE says; container binds to `$PORT`. Mismatch = healthcheck fails silently. |
| `WATCHDOG_INTERVAL_SECONDS` | `30` | |
| `WATCHDOG_LLM_JUDGE_ENABLED` | `false` | Heuristic mode only |
| `EVAL_WORKER_INTERVAL_SECONDS` | `15` | Pending-queue poll rate |
| `EVAL_AUTOSCORER_INTERVAL_SECONDS` | `60` | Trace sampling rate |
| `EVAL_AUTOSCORER_SAMPLE_RATE` | `0.1` | Fraction of new traces scored |
| `EVAL_AUTOSCORER_BATCH_SIZE` | `5` | Max per tick |
| `EVAL_GOLDEN_DATASET_PATH` | `config/eval_datasets/golden.yaml` | Baked into the image |
| `EVAL_REGRESSION_THRESHOLD` | `0.05` | 5% dim drop → alert |
| `EVAL_DAILY_RUN_UTC_HOUR` | `2` | Daily golden cron hour |
| `API_HOST` | `0.0.0.0` | Always bind externally |
| `API_RELOAD` | `false` | Never use uvicorn reload in prod |

### Vercel (dashboard)

| Name | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://otelmind-api-production.up.railway.app` |
| `NEXT_PUBLIC_OTELMIND_API_KEY` | `om_…` (see `api_keys` table in Neon) |

Both must be set in the **Production** environment. For Preview
deployments off feature branches, duplicate them into **Preview** too.

---

## Smoke test

After any deploy, run this against the production URLs:

```bash
BASE_API="https://otelmind-api-production.up.railway.app"
BASE_UI="https://otelmind-dashboard.vercel.app"
KEY="om_…"

# Backend health
curl -s "$BASE_API/api/v1/health"

# Full-stack proxy (dashboard → Vercel rewrite → Railway → Neon)
curl -s -H "x-api-key: $KEY" "$BASE_UI/api/v1/dashboard/stats"

# Every dashboard route
for r in /traces /failures /costs /alerts /evals; do
  echo "$r → $(curl -s -o /dev/null -w '%{http_code}' $BASE_UI$r)"
done
```

Expected: every response is 200, `/api/v1/dashboard/stats` returns
real trace counts from Neon.

---

## Rotating an API key

1. Generate a new key via SQL against Neon (there's no admin endpoint
   yet):
   ```python
   import secrets, hashlib, uuid
   raw = "om_" + secrets.token_urlsafe(32)
   key_hash = hashlib.sha256(raw.encode()).hexdigest()
   # INSERT INTO api_keys (id, tenant_id, name, key_hash, key_prefix, scopes)
   # VALUES (uuid4, tenant_id, 'name', key_hash, raw[:12], ['admin'])
   ```
2. Revoke the old key: `UPDATE api_keys SET revoked_at = now() WHERE key_prefix = 'om_…'`.
3. Update both Railway and Vercel env vars (`NEXT_PUBLIC_OTELMIND_API_KEY`).
4. Redeploy the dashboard so the new key lands in Vercel's build.

---

## Things that will bite you later

1. **Neon branch limit.** The free tier has a branch cap. If you create
   staging branches for every deploy, you'll hit it. Clean old branches
   from the Neon console.
2. **Railway `$PORT`.** Covered above — the PORT env var and Dockerfile
   EXPOSE must match. If healthchecks fail and runtime logs are empty,
   this is almost always the cause.
3. **Dashboard rewrites are server-side.** Browser requests hit
   `otelmind-dashboard.vercel.app/api/v1/*`. Vercel rewrites to
   `otelmind-api-production.up.railway.app/api/v1/*` at the edge.
   CORS is a non-issue because the browser only ever sees Vercel's
   domain. Don't "fix" this by adding CORS headers to Railway — you'd
   just be papering over a misconfiguration.
4. **Eval auto-scorer cold start.** The loop only scores traces
   created in the **last hour**. If the backend restarts during a
   traffic lull, you'll see `scored_traces: 0` on `/eval/quality`
   until new traces come in. This is by design (scoring old traces
   retroactively is wasteful) — not a bug.
5. **Daily golden cron.** The 02:00 UTC time is configurable via
   `EVAL_DAILY_RUN_UTC_HOUR`. The loop wakes every 10 minutes and
   fires once per UTC day, so if you deploy at 02:05 the run still
   happens that day; if you deploy at 01:55 it fires ten minutes later.
