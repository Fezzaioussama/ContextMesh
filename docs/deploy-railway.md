# Deploy to Railway

ContextMesh runs on Railway as three services in one project:

| Service | What it is | Storage | Public |
| --- | --- | --- | --- |
| `contextmesh` | This repository: the built frontend, the API, and the ingestion worker in one container | Volume at `/data` (uploads) | Yes |
| `Postgres` | Railway PostgreSQL: conversations, sources, jobs | Managed by Railway | No |
| `Qdrant` | `qdrant/qdrant:v1.19.1` image: the vector index | Volume at `/qdrant/storage` | No |

The API and worker share one container because both read and write the uploaded
files, and a Railway volume belongs to a single service.
[`backend/scripts/start_app.sh`](../backend/scripts/start_app.sh) applies the
migrations, then starts both processes. If either one stops, the container exits
and Railway restarts it. [`railway.toml`](../railway.toml) builds the root
[`Dockerfile`](../Dockerfile) and sets the health check, restart policy, and
required volume.

> **The deployment has no login.** Anyone who has the URL can read the indexed
> documents, upload files, and spend the OpenRouter credits of the configured key.
> Give that key a [credit limit on OpenRouter](https://openrouter.ai/settings/keys)
> and share the URL only with people you trust.

## 1. Create the project and the databases

1. In Railway, choose **New Project → Empty Project**.
2. **Create → Database → PostgreSQL**. Keep the service name `Postgres`.
3. **Create → Docker Image**, enter `qdrant/qdrant:v1.19.1`, and name the
   service `Qdrant`. Then, on that service:
   - **Variables**: add `QDRANT__SERVICE__HOST` = `::`, so Qdrant also listens on
     the IPv6 private network that older Railway environments use.
   - **Volume**: attach one mounted at `/qdrant/storage`.
   - Do not generate a public domain. Only the app reaches Qdrant, over the
     private network.

## 2. Add the app

1. **Create → GitHub Repo**, choose `Fezzaioussama/ContextMesh`, and name the
   service `contextmesh`. In **Settings → Source**, deploy the `main` branch.
2. **Volume**: attach one mounted at `/data`. A deploy without it is refused
   (`requiredMountPath` in `railway.toml`).
3. **Variables**: add these. The `${{…}}` references resolve to the other
   services, so the service names must match `Postgres` and `Qdrant`.

   | Variable | Value |
   | --- | --- |
   | `CONTEXTMESH_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
   | `CONTEXTMESH_QDRANT_URL` | `http://${{Qdrant.RAILWAY_PRIVATE_DOMAIN}}:6333` |
   | `OPENROUTER_API_KEY` | your OpenRouter key |

   Every other setting has a working default. See
   [`.env.example`](../.env.example) for the models (`OPENROUTER_MODEL`,
   `OPENROUTER_EMBEDDING_MODEL`, `OPENROUTER_RERANK_MODEL`), upload and crawl
   limits, and optional LangSmith tracing. Railway sets `PORT` itself. Do not set
   `CONTEXTMESH_FRONTEND_DIR` or `CONTEXTMESH_BLOB_DIR`: the image sets them.
4. **Settings → Networking → Generate Domain**.

Railway then builds and deploys. The deploy goes live once
`/health/ready` answers, which happens after the migrations have run and
PostgreSQL responds.

## 3. Check the deployment

1. Open `https://<your-domain>/health/ready`. It should return
   `{"status":"ready"}`.
2. Open `https://<your-domain>/`, choose **Sources**, upload a small Markdown
   file, and wait for **Ready**. Then ask a question about it.
3. In the `contextmesh` service logs, every step logs one line with ids and counts
   only: `flow=upload_document step=job_ended …`, then
   `flow=ask_question step=answered …`. [`flows.md`](flows.md) lists every line.

Each push to `main` deploys again. Because the app has a volume, Railway stops the
old container before starting the new one, so a deploy has a short downtime.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The deploy fails its health check | The service logs: an `alembic` error or `database_unavailable` usually means `CONTEXTMESH_DATABASE_URL` does not reference `Postgres`. |
| Documents stay **Indexing…** or show **Failed** | Logs with `job_failed code=…`. `provider_not_configured` means `OPENROUTER_API_KEY` is missing. |
| Indexing fails, or answers miss passages that are clearly there | Qdrant may be unreachable: check `CONTEXTMESH_QDRANT_URL`, the `Qdrant` service name, and `QDRANT__SERVICE__HOST`. Readiness checks PostgreSQL only, so search falls back to keyword matching. |
| The deploy is refused before it starts | The `/data` volume is not attached to `contextmesh`. |

Changing `OPENROUTER_EMBEDDING_MODEL` re-indexes every document when the worker
starts, which spends embedding credits. The previous model's vectors stay in
Qdrant.

To build and run the same image locally, run `docker build -t contextmesh .`.
It needs the same variables, a PostgreSQL URL, and a Qdrant URL.
