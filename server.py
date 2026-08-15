from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_DIR = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))
PROFILE_PATH = APP_DIR / "profile.json"
SEARCH_PATH = APP_DIR / "searches.yaml"
ENV_PATH = APP_DIR / ".env"
DB_PATH = APP_DIR / "applypilot.db"
RESUME_TXT = APP_DIR / "resume.txt"
RESUME_PDF = APP_DIR / "resume.pdf"

APP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ApplyPilot Web")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

_runs: dict[str, dict[str, Any]] = {}
_runs_lock = threading.Lock()

PROFILE_TEMPLATE = {
    "personal": {
        "full_name": "", "preferred_name": "", "email": "", "password": "", "phone": "",
        "address": "", "city": "", "province_state": "", "country": "", "postal_code": "",
        "linkedin_url": "", "github_url": "", "portfolio_url": "", "website_url": ""
    },
    "work_authorization": {
        "legally_authorized_to_work": "", "require_sponsorship": "", "work_permit_type": ""
    },
    "availability": {
        "earliest_start_date": "", "available_for_full_time": "Yes", "available_for_contract": "No"
    },
    "compensation": {
        "salary_expectation": "", "salary_currency": "USD", "salary_range_min": "",
        "salary_range_max": "", "currency_conversion_note": ""
    },
    "experience": {
        "years_of_experience_total": "", "education_level": "", "current_job_title": "",
        "current_company": "", "target_role": ""
    },
    "skills_boundary": {
        "languages": [], "frameworks": [], "devops": [], "databases": [], "tools": []
    },
    "resume_facts": {
        "preserved_companies": [], "preserved_projects": [], "preserved_school": "", "real_metrics": []
    },
    "eeo_voluntary": {
        "gender": "Decline to self-identify",
        "race_ethnicity": "Decline to self-identify",
        "veteran_status": "Decline to self-identify",
        "disability_status": "I do not wish to answer"
    }
}

class ProfilePayload(BaseModel):
    profile: dict[str, Any]
    searches: dict[str, Any] | None = None
    api_keys: dict[str, str] | None = None

class RunPayload(BaseModel):
    command: str
    workers: int = 1
    min_score: int = 7
    dry_run: bool = False
    continuous: bool = False
    headless: bool = False
    url: str | None = None

def _read_json(path: Path, fallback: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

def _read_yaml(path: Path, fallback: Any):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or fallback
    except Exception:
        return fallback

def _merge_dicts(base: dict, incoming: dict) -> dict:
    out = dict(base)
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dicts(out[k], v)
        else:
            out[k] = v
    return out

def _db():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _job_stage(row: sqlite3.Row) -> str:
    if row["applied_at"]:
        return "applied"
    if row["apply_status"] in ("failed", "error"):
        return "failed"
    if row["tailored_resume_path"] and row["application_url"]:
        return "ready"
    if row["cover_letter_path"]:
        return "cover"
    if row["tailored_resume_path"]:
        return "tailored"
    if row["fit_score"] is not None:
        return "scored"
    if row["full_description"]:
        return "enriched"
    return "found"

@app.get("/")
def root():
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.get("/api/setup")
def get_setup():
    profile = _merge_dicts(PROFILE_TEMPLATE, _read_json(PROFILE_PATH, {}))
    searches = _read_yaml(SEARCH_PATH, {
        "searches": [{
            "query": "software engineer",
            "location": "United States",
            "sites": ["indeed", "linkedin", "glassdoor", "zip_recruiter", "google"]
        }]
    })
    return {
        "profile": profile,
        "searches": searches,
        "resume_pdf": RESUME_PDF.exists(),
        "resume_txt": RESUME_TXT.exists(),
        "app_dir": str(APP_DIR),
    }

@app.post("/api/setup")
def save_setup(payload: ProfilePayload):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = _merge_dicts(PROFILE_TEMPLATE, payload.profile)
    PROFILE_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    if payload.searches is not None:
        SEARCH_PATH.write_text(yaml.safe_dump(payload.searches, sort_keys=False), encoding="utf-8")

    if payload.api_keys:
        allowed = {"GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL", "LLM_MODEL", "CAPSOLVER_API_KEY"}
        existing = {}
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
        for k, v in payload.api_keys.items():
            if k in allowed and v:
                existing[k] = v.strip()
        ENV_PATH.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n", encoding="utf-8")
    return {"ok": True}

@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)):
    name = (file.filename or "").lower()
    data = await file.read()
    if name.endswith(".pdf"):
        RESUME_PDF.write_bytes(data)
        return {"ok": True, "type": "pdf", "path": str(RESUME_PDF)}
    if name.endswith(".txt"):
        RESUME_TXT.write_bytes(data)
        return {"ok": True, "type": "txt", "path": str(RESUME_TXT)}
    raise HTTPException(400, "Upload a PDF or TXT resume.")

@app.get("/api/doctor")
def doctor():
    checks = {
        "applypilot": bool(shutil.which("applypilot")),
        "python": bool(shutil.which("python3") or shutil.which("python")),
        "node": bool(shutil.which("node")),
        "npx": bool(shutil.which("npx")),
        "claude": bool(shutil.which("claude")),
        "chrome": Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists()
                  or bool(shutil.which("google-chrome") or shutil.which("chromium")),
        "profile": PROFILE_PATH.exists(),
        "searches": SEARCH_PATH.exists(),
        "resume": RESUME_PDF.exists() or RESUME_TXT.exists(),
        "llm_key": False,
    }
    if ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8")
        checks["llm_key"] = any(k + "=" in text for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL"))
    checks["ready_discovery"] = checks["applypilot"] and checks["profile"] and checks["searches"]
    checks["ready_ai"] = checks["ready_discovery"] and checks["llm_key"]
    checks["ready_apply"] = checks["ready_ai"] and checks["node"] and checks["npx"] and checks["claude"] and checks["chrome"]
    return checks

@app.get("/api/stats")
def stats():
    conn = _db()
    if conn is None:
        return {"total": 0, "found": 0, "scored": 0, "ready": 0, "applied": 0, "failed": 0}
    try:
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        stages = {}
        for r in rows:
            s = _job_stage(r)
            stages[s] = stages.get(s, 0) + 1
        return {
            "total": len(rows),
            "found": stages.get("found", 0) + stages.get("enriched", 0),
            "scored": stages.get("scored", 0) + stages.get("tailored", 0) + stages.get("cover", 0),
            "ready": stages.get("ready", 0),
            "applied": stages.get("applied", 0),
            "failed": stages.get("failed", 0),
        }
    finally:
        conn.close()

@app.get("/api/jobs")
def jobs(stage: str = "all", q: str = "", limit: int = 500):
    conn = _db()
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT ?", (min(limit, 2000),)).fetchall()
        out = []
        ql = q.lower().strip()
        for r in rows:
            d = dict(r)
            d["stage"] = _job_stage(r)
            if stage != "all" and d["stage"] != stage:
                continue
            hay = " ".join(str(d.get(k) or "") for k in ("title", "site", "location", "url")).lower()
            if ql and ql not in hay:
                continue
            out.append(d)
        return out
    finally:
        conn.close()

@app.get("/api/job-file")
def job_file(path: str):
    p = Path(path).expanduser().resolve()
    allowed_roots = [
        (APP_DIR / "tailored_resumes").resolve(),
        (APP_DIR / "cover_letters").resolve(),
        APP_DIR.resolve(),
    ]
    if not any(str(p).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(403, "File outside ApplyPilot data directory.")
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found.")
    return FileResponse(p)

def _build_command(p: RunPayload) -> list[str]:
    if not shutil.which("applypilot"):
        raise RuntimeError("applypilot CLI not found. Run ./setup.sh first.")
    if p.command == "run":
        cmd = ["applypilot", "run", "--workers", str(max(1, min(p.workers, 16))), "--min-score", str(p.min_score)]
        if p.dry_run: cmd.append("--dry-run")
        return cmd
    if p.command == "apply":
        cmd = ["applypilot", "apply", "--workers", str(max(1, min(p.workers, 8)))]
        if p.dry_run: cmd.append("--dry-run")
        if p.continuous: cmd.append("--continuous")
        if p.headless: cmd.append("--headless")
        if p.url: cmd += ["--url", p.url]
        return cmd
    if p.command == "doctor":
        return ["applypilot", "doctor"]
    if p.command == "status":
        return ["applypilot", "status"]
    raise RuntimeError("Unsupported command")

def _run_worker(run_id: str, cmd: list[str]):
    env = os.environ.copy()
    env["APPLYPILOT_DIR"] = str(APP_DIR)
    with _runs_lock:
        _runs[run_id]["status"] = "running"
        _runs[run_id]["command_line"] = " ".join(cmd)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env
        )
        with _runs_lock:
            _runs[run_id]["pid"] = proc.pid
        for line in iter(proc.stdout.readline, ""):
            with _runs_lock:
                log = _runs[run_id]["log"]
                log.append(line.rstrip())
                if len(log) > 3000:
                    del log[:1000]
        rc = proc.wait()
        with _runs_lock:
            _runs[run_id]["return_code"] = rc
            _runs[run_id]["status"] = "completed" if rc == 0 else "failed"
            _runs[run_id]["finished_at"] = time.time()
    except Exception as e:
        with _runs_lock:
            _runs[run_id]["status"] = "failed"
            _runs[run_id]["log"].append(f"ERROR: {e}")
            _runs[run_id]["finished_at"] = time.time()

@app.post("/api/run")
def start_run(payload: RunPayload):
    try:
        cmd = _build_command(payload)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    run_id = uuid.uuid4().hex[:12]
    with _runs_lock:
        _runs[run_id] = {
            "id": run_id, "status": "queued", "log": [], "started_at": time.time(),
            "payload": payload.model_dump()
        }
    threading.Thread(target=_run_worker, args=(run_id, cmd), daemon=True).start()
    return {"run_id": run_id}

@app.get("/api/run/{run_id}")
def get_run(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(404, "Unknown run.")
        return dict(run)

@app.post("/api/run/{run_id}/stop")
def stop_run(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(404, "Unknown run.")
        pid = run.get("pid")
    if pid:
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
    return {"ok": True}

@app.get("/api/runs")
def list_runs():
    with _runs_lock:
        return sorted((dict(v) for v in _runs.values()), key=lambda x: x["started_at"], reverse=True)[:20]
