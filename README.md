# NexusOS-Ultra

NexusOS-Ultra is a local-first AI workspace and cloud-browser command layer designed for a 2015 Intel MacBook Air. The local machine runs the lightweight dashboard, API gateway, file mesh, and logs. Heavy browser execution is routed to Skyvern or another authorized remote browser runtime.

The package is intentionally guarded. It does not include CAPTCHA bypass, anti-bot evasion, credential theft, stealth scraping, or unauthorized access tooling. Use browser automation only on sites and accounts where you have permission.

## What is included

- Next.js 14 cyberpunk dashboard on `http://localhost:3000`
- FastAPI OpenClaw-compatible gateway on `http://localhost:8088`
- Gemini/Groq LLM router with model routing and usage counters
- Skyvern REST client for authorized remote browser tasks
- macOS file mesh for:
  - `/Users/${USER}` mapped to `/workspace/local_mac_system`
  - `~/Library/Mobile Documents/com~apple~CloudDocs` mapped to `/workspace/icloud_drive`
- Guarded self-evolution daemon that can inspect project logs, propose repairs, validate Python syntax, write backups, and record patches when explicitly enabled
- Local Postgres database for durable workspace state
- Validation script that checks the file tree, Python syntax, JSON configs, and frontend essentials

## Requirements

- macOS Monterey or newer recommended
- Docker Desktop for Mac
- Node is not required on the host when using Docker Compose
- Gemini API key for heavy context reasoning
- Groq API key for low-latency execution loops
- Skyvern API key for live cloud-browser tasks

## macOS Full Disk Access setup

Docker can only bind-mount folders macOS allows it to read.

1. Open **System Settings**.
2. Go to **Privacy & Security**.
3. Open **Full Disk Access**.
4. Enable access for:
   - **Docker**
   - **Terminal**
   - **iTerm** if you use it
5. Open Docker Desktop, then go to **Settings → Resources → File Sharing** and make sure these paths are allowed:
   - `/Users`
   - `/Users/YOUR_MAC_USERNAME/Library/Mobile Documents`
6. Quit and reopen Docker Desktop after changing permissions.

## Fast start

```bash
cd nexusos-ultra
cp .env.example .env
docker compose up --build
```

Open:

- Dashboard: `http://localhost:3000`
- Gateway health: `http://localhost:8088/health`

## Add API keys

Edit `.env`:

```bash
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
SKYVERN_API_KEY=your_skyvern_key
SKYVERN_ALLOWED_DOMAINS=example.com,hackernews.org,news.ycombinator.com
```

Restart:

```bash
docker compose down
docker compose up --build
```

## Self-evolution mode

Self-evolution is disabled by default. Enable it only after confirming the dashboard and gateway work.

```bash
NEXUS_SELF_EVOLUTION=1
```

The daemon is restricted to the project folder. It will not patch arbitrary files outside the mounted application source. It backs up files before replacing them and records patch events under `runtime/patches`.

Manual one-shot repair command inside the gateway container:

```bash
docker exec -it nexusos-openclaw-gateway python /app/core-agent/self_evolution.py --once
```

## Validate the package

```bash
python3 scripts/validate_stack.py
```

Expected result:

```text
NexusOS-Ultra validation passed.
```

## File mesh safety

The file mesh permits access only under:

- `/workspace/local_mac_system`
- `/workspace/icloud_drive`

It blocks dangerous or noisy sync paths such as `.Trash`, `.DocumentRevisions-V100`, `.fseventsd`, `.Spotlight-V100`, `Mobile Documents/.Trash`, `.icloud` placeholders for unsafe writes, and project-recursive runtime loops.

## Browser automation safety

The Skyvern client only dispatches tasks when:

- `SKYVERN_API_KEY` is configured
- the target domain is present in `SKYVERN_ALLOWED_DOMAINS`
- compliance mode is enabled
- the task is framed as an authorized user workflow

The client parses returned task artifacts and stores extraction payloads in the file mesh under `exports/`.

## Troubleshooting

### Docker cannot mount iCloud Drive

Run this command to verify the host path:

```bash
ls "$HOME/Library/Mobile Documents/com~apple~CloudDocs"
```

If it fails, open iCloud Drive once in Finder and make sure iCloud Drive is enabled in Apple ID settings.

### Dashboard cannot reach gateway

Check:

```bash
curl http://localhost:8088/health
docker logs nexusos-openclaw-gateway --tail 100
```

### File tree is empty

Confirm Docker file sharing and Full Disk Access settings. Then restart Docker Desktop.

### Live browser task does not start

Check:

```bash
grep SKYVERN .env
curl http://localhost:8088/health
```

The stack remains usable without Skyvern credentials, but live cloud browser execution needs a real key.
