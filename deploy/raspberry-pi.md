# Running the construction docrag on a Raspberry Pi (always-on)

Profile: **Online (Azure) · Web UI + MCP · Docker.**

docrag is *compute-in-cloud, data-on-disk*. The Pi runs only the slim **serve**
layer — SQLite vector search (`sqlite-vec`) + HTTP calls to Azure OpenAI for
embeddings and LLM synthesis. No torch, no GPU, no ML stack. Indexing stays on
your PC; the Pi consumes a prebuilt index bundle.

> The Pi needs **always-on internet** to Azure. Every query embeds the question
> and synthesizes the answer via Azure OpenAI (`.env` keys). This is NOT an
> offline appliance.

---

## 0. Pi prerequisites (one time)

- Raspberry Pi 4 or **5**, **64-bit OS (aarch64 / Raspberry Pi OS 64-bit)** —
  required; `sqlite-vec` + the slim image are arm64. 4 GB+ RAM ideal (2 GB serves).
- ~5 GB free disk (index bundle ≈ 2.2 GB + image + corpora).
- Docker + Compose plugin, and Docker enabled on boot:
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER          # re-login after this
  sudo systemctl enable --now docker     # start on boot
  ```
- Outbound internet to `*.openai.azure.com`.

## 1. Export the bundle (on your PC)

```bash
.venv/Scripts/python.exe server/server.py --stop        # quiesce the DB
.venv/Scripts/python.exe migrate/docrag_migrate.py export \
    --manifest migrate/manifests/docrag.json \
    --out docrag-bundle.tar.gz --checksums
```
Produces `docrag-bundle.tar.gz` (~2.2 GB): `.index/`, `corpora/`, `.env`,
`.mcp.json`, `BUNDLE_MANIFEST.json`.

## 2. Move it to the Pi

```bash
scp docrag-bundle.tar.gz pi@<pi-ip>:~/
sha256sum docrag-bundle.tar.gz          # compare both ends (optional)
```

## 3. Import + lay out data (on the Pi)

```bash
git clone <repo-url> docrag && cd docrag
python3 migrate/docrag_migrate.py import --bundle ~/docrag-bundle.tar.gz --repo-root .
mkdir -p data && mv .index corpora data/     # docker-compose mounts ./data
# .env (Azure keys) is extracted from the bundle into the repo root — keep it there.
```

## 4. Bring up the web UI (always-on)

```bash
docker compose up -d                          # builds the arm64 serve image locally, then runs it
docker compose logs -f docrag                 # watch first boot
```
- Serves at **`http://<pi-ip>:8099`** from any machine on the LAN.
- `restart: unless-stopped` (compose) + Docker-enabled-on-boot ⇒ survives reboots
  and crashes. That's the "always running" part.
- The image builds **on the Pi**, so it's native arm64 — do NOT copy an amd64
  image from your PC. First build pulls arm64 wheels for `lxml`/`sqlite-vec`
  (~a few minutes).

## 5. MCP access (the "Both" half)

The web UI is for humans; MCP exposes docrag as agent tools. The MCP server is
stdio — the client spawns it — so there's no extra always-on service, just the
image + mounted data. Two client configs:

**A. Agent runs ON the Pi** — `.mcp.json`:
```json
{
  "mcpServers": {
    "docrag": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--env-file", "/home/pi/docrag/.env",
               "-v", "/home/pi/docrag/data/.index:/data/.index",
               "-v", "/home/pi/docrag/data/corpora:/data/corpora:ro",
               "docrag:serve", "python", "-m", "docrag.mcp_server"]
    }
  }
}
```

**B. Agent runs on your laptop, hitting the Pi** — stdio piped over SSH:
```json
{
  "mcpServers": {
    "docrag": {
      "command": "ssh",
      "args": ["pi@<pi-ip>",
               "docker run --rm -i --env-file /home/pi/docrag/.env",
               "-v /home/pi/docrag/data/.index:/data/.index",
               "-v /home/pi/docrag/data/corpora:/data/corpora:ro",
               "docrag:serve python -m docrag.mcp_server"]
    }
  }
}
```
(Requires key-based SSH to the Pi. Each tool call spawns a short-lived container.)

## 6. Gotchas

- **`DOCRAG_RERANK=0`** stays set (compose already does this) — the reranker
  needs torch, which isn't in the serve image.
- **Latency is network-bound** (Azure round-trips), not Pi-CPU-bound → expect
  similar speed to your PC.
- **The Pi can't re-index** (no docling/torch in the serve image). When the
  construction corpus changes, re-export on your PC and re-ship — or copy just
  the updated `data/.index/*.db` and `docker compose restart docrag`.

## 7. Updating the index later

```bash
# PC: re-export after re-indexing
python migrate/docrag_migrate.py export --manifest migrate/manifests/docrag.json --out docrag-bundle.tar.gz
# Pi: re-import over the running instance
docker compose down
python3 migrate/docrag_migrate.py import --bundle ~/docrag-bundle.tar.gz --repo-root .
mv .index corpora data/ 2>/dev/null; docker compose up -d
```
