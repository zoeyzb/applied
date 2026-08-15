# ApplyPilot Web

A local web control panel for the original open-source **Pickle-Pixel/ApplyPilot** package.

## What it adds

- Guided web form for ApplyPilot's `profile.json`
- Resume upload
- Search preferences UI
- Gemini/OpenAI/CapSolver key setup
- System readiness / doctor screen
- Dashboard for found, scored, ready, applied and failed jobs
- Searchable job history backed by ApplyPilot's real SQLite DB
- Buttons for `applypilot run` and `applypilot apply`
- Live command logs
- Dry-run, parallel workers, headless mode, continuous mode, specific-URL apply

## Important design choice

This app does **not** reimplement ApplyPilot. It wraps the original installed `applypilot`
CLI and uses its native `~/.applypilot` data directory. You can use the CLI and web UI
interchangeably.

## Install on macOS

```bash
chmod +x setup.sh start.sh
./setup.sh
./start.sh
```

Open:

```text
http://127.0.0.1:8787
```

For the original ApplyPilot full auto-apply tier you also need Chrome, Node/npx, an LLM key,
and Claude Code CLI, because the upstream project uses those for browser-driven submission.

## Data

Your data stays in the original ApplyPilot directory:

```text
~/.applypilot/
  profile.json
  searches.yaml
  .env
  resume.pdf / resume.txt
  applypilot.db
  tailored_resumes/
  cover_letters/
  logs/
```

## License note

ApplyPilot itself is AGPL-3.0. If you deploy/distribute a modified combined service,
review and comply with the upstream AGPL-3.0 obligations.
