# Migrating a docrag instance

A docrag deployment is **compute-in-cloud, data-on-disk**:

| Layer | Where | Migrate? |
|---|---|---|
| Embeddings + LLM | Azure OpenAI (`.env` keys) | No — already cloud. Copy `.env`. |
| Index (`.index/*.db`) | SQLite + sqlite-vec + FTS5, local | **Yes** — the real payload. |
| Source corpora (`corpora/`) | PDFs / HTML, local | **Yes** (regenerable from sources, but re-indexing costs Azure calls + time). |
| Runtime (`.venv/`) | local, OS-specific | No — rebuild on target (or use Docker). |
| HF model cache (docling, reranker) | `~/.cache/huggingface` | No — re-downloads; only needed to *index*, not to *serve*. |

So migration = **move `.index` + `corpora` + `.env`, then rebuild the runtime** on
the target. `docrag_migrate.py` does the move; Docker rebuilds the runtime
OS-agnostically.

These tools are **config-driven** — one manifest per instance — so the same
scripts move this repo and the separate YouTube docrag repo.

---

## 1. Export (on the source machine)

Stop the server first so the DB is quiescent:

```bash
.venv/Scripts/python.exe server/server.py --stop      # Windows
```

Bundle it (checkpoints WAL into each `.db`, then tar.gz):

```bash
.venv/Scripts/python.exe migrate/docrag_migrate.py export \
    --manifest migrate/manifests/docrag.json \
    --out docrag-bundle.tar.gz \
    --checksums
```

Produces `docrag-bundle.tar.gz` (~2.2 GB for building-codes) containing
`.index/`, `corpora/`, `.env`, `.mcp.json`, and a `BUNDLE_MANIFEST.json`
(source OS, git rev, per-file sizes/hashes).

> `--checksums` records per-file sha256 (slower). Omit for a faster pack.
> WAL/`-shm`/`.pid` files are excluded automatically.

## 2. Move the bundle

`scp`, USB, Azure Blob — whatever. It's one file. Verify it survived:

```bash
sha256sum docrag-bundle.tar.gz   # compare both ends
```

## 3. Import (on the target Linux machine)

```bash
git clone <repo-url> docrag && cd docrag        # the code (small)

python3 migrate/docrag_migrate.py import \
    --bundle ../docrag-bundle.tar.gz \
    --repo-root .                                # extracts .index/, corpora/, .env here
```

This extracts the data and rewrites `.mcp.json`'s `command` to the target's
python path (defaults to `.venv/bin/python` on Linux).

### Then pick a runtime

**A. Docker (recommended — no venv, no path fights):**

```bash
mkdir -p data && mv .index corpora data/        # compose mounts ./data
docker compose up -d                            # serve at http://localhost:8099
```

**B. Bare-metal venv:**

```bash
python3 migrate/docrag_migrate.py import \
    --bundle ../docrag-bundle.tar.gz --repo-root . \
    --bootstrap-venv --requirements requirements.txt --self-test
# or, serve-only (no torch/docling): --requirements requirements.serve.txt
```

`--bootstrap-venv` creates `.venv` + installs deps; `--self-test` runs
`docrag.db --self-test` to confirm the index opens.

---

## The separate YouTube instance

It's a different repo. To migrate it with these same scripts:

1. Copy `migrate/manifests/youtube.example.json` → `youtube.json`.
2. Set `repo_root` to that repo's path; adjust `index_dir` / `data_dirs` if its
   layout differs (e.g. transcripts under `data/`).
3. `python migrate/docrag_migrate.py export --manifest migrate/manifests/youtube.json --out yt-bundle.tar.gz`

Same import flow. If that repo doesn't yet have its own `Dockerfile` /
`docker-compose.yml`, copy these as a starting point and adjust `requirements`.

---

## Long-term architecture

Today each box holds its own copy of the data. Two upgrades, in order:

1. **Docker (done here)** — kills the Windows/Linux venv + path divergence.
   Image = code only; data is a mounted volume. Migrating = move the volume.

2. **Azure Blob as source of truth** — push `*.db` + `corpora/` to a Blob
   container; any machine pulls on setup. You're already Azure-native, so this
   centralizes versioning with no new vendor. (Wrap as `migrate/blob_push.py` /
   `blob_pull.py` over `azure-storage-blob` when wanted.)

3. **Only if many machines must hit one *live* DB concurrently:** migrate
   SQLite → pgvector on Azure Database for PostgreSQL. Biggest change
   (`db.py` / `query.py` / `embed.py`); skip until concurrency actually demands
   it — local SQLite is faster for single-box serving.
