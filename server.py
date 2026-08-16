from __future__ import annotations

import base64
import json
import os
import shlex
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from vercel.sandbox import AsyncSandbox

BASE = "/vercel/sandbox"
DATA = f"{BASE}/.applypilot"
RUNS = f"{DATA}/web-runs"
SANDBOX = os.environ.get("APPLYPILOT_SANDBOX_NAME", "applypilot-worker")

app = FastAPI(title="ApplyPilot Cloud")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

PROFILE_TEMPLATE = {
    "personal": {"full_name":"","preferred_name":"","email":"","password":"","phone":"","address":"","city":"","province_state":"","country":"","postal_code":"","linkedin_url":"","github_url":"","portfolio_url":"","website_url":""},
    "work_authorization": {"legally_authorized_to_work":"","require_sponsorship":"","work_permit_type":""},
    "availability": {"earliest_start_date":"","available_for_full_time":"Yes","available_for_contract":"No"},
    "compensation": {"salary_expectation":"","salary_currency":"USD","salary_range_min":"","salary_range_max":"","currency_conversion_note":""},
    "experience": {"years_of_experience_total":"","education_level":"","current_job_title":"","current_company":"","target_role":""},
    "skills_boundary": {"languages":[],"frameworks":[],"devops":[],"databases":[],"tools":[]},
    "resume_facts": {"preserved_companies":[],"preserved_projects":[],"preserved_school":"","real_metrics":[]},
    "eeo_voluntary": {"gender":"Decline to self-identify","race_ethnicity":"Decline to self-identify","veteran_status":"Decline to self-identify","disability_status":"I do not wish to answer"}
}
DEFAULT_SEARCHES = {"searches":[{"query":"software engineer","location":"United States","sites":["indeed","linkedin","glassdoor","zip_recruiter","google"]}]}

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
    headless: bool = True
    url: str | None = None

async def _out(result) -> str:
    value = result.stdout()
    if hasattr(value, "__await__"):
        value = await value
    return str(value or "").strip()

async def _run(sb: AsyncSandbox, cmd: str, args: list[str] | None = None) -> str:
    return await _out(await sb.run_command(cmd, args or []))

async def _sb() -> AsyncSandbox:
    try:
        sb = await AsyncSandbox.get(name=SANDBOX)
        await _run(sb, "bash", ["-lc", f"mkdir -p {RUNS} && echo ok"])
        return sb
    except Exception:
        try:
            sb = await AsyncSandbox.create(name=SANDBOX, timeout=3_600_000)
            await _run(sb, "bash", ["-lc", f"mkdir -p {RUNS}"])
            return sb
        except Exception as exc:
            raise HTTPException(503, f"Vercel Sandbox unavailable: {exc}") from exc

async def _exists(sb: AsyncSandbox, path: str) -> bool:
    return (await _run(sb, "bash", ["-lc", f"test -e {shlex.quote(path)} && echo 1 || echo 0"])) == "1"

async def _read(sb: AsyncSandbox, path: str, default: str = "") -> str:
    code = f"import pathlib;p=pathlib.Path({path!r});print(p.read_text(encoding='utf-8') if p.exists() else {default!r})"
    return await _run(sb, "python3", ["-c", code])

async def _write(sb: AsyncSandbox, path: str, data: bytes):
    b64 = base64.b64encode(data).decode()
    code = f"import base64,pathlib;p=pathlib.Path({path!r});p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode({b64!r}))"
    await _run(sb, "python3", ["-c", code])

async def _bootstrap(sb: AsyncSandbox) -> dict[str, Any]:
    marker = f"{BASE}/.applypilot-cloud-ready"
    pidfile = f"{BASE}/.applypilot-cloud-bootstrap.pid"
    logfile = f"{BASE}/.applypilot-cloud-bootstrap.log"
    rcfile = f"{BASE}/.applypilot-cloud-bootstrap.rc"
    if await _exists(sb, marker):
        state = "ready"
    else:
        alive = "0"
        if await _exists(sb, pidfile):
            pid = (await _read(sb, pidfile)).strip()
            if pid:
                alive = await _run(sb, "bash", ["-lc", f"kill -0 {shlex.quote(pid)} 2>/dev/null && echo 1 || echo 0"])
        if alive == "1":
            state = "installing"
        else:
            script = f'''set -e
cd {BASE}
rm -f .applypilot-cloud-ready .applypilot-cloud-bootstrap.rc
python3 -m venv .cloudvenv
. .cloudvenv/bin/activate
python -m pip install --upgrade pip
pip install applypilot
pip install --no-deps python-jobspy
pip install pydantic tls-client requests markdownify regex
command -v node
command -v npm
command -v npx
npm install -g @anthropic-ai/claude-code
npx -y playwright@latest install --with-deps chromium
mkdir -p {BASE}/bin
CHROME=$(find "$HOME/.cache/ms-playwright" -type f -path '*chrome-linux*/chrome' 2>/dev/null | head -1 || true)
if [ -z "$CHROME" ]; then CHROME=$(find "$HOME/.cache/ms-playwright" -type f -name chrome 2>/dev/null | head -1 || true); fi
[ -n "$CHROME" ] && ln -sf "$CHROME" {BASE}/bin/chromium
PATH={BASE}/bin:$PATH .cloudvenv/bin/applypilot --help >/dev/null
command -v claude
test -x {BASE}/bin/chromium
touch .applypilot-cloud-ready
'''
            launch = f"cd {BASE}; nohup bash -lc {shlex.quote(script)} > {logfile} 2>&1; echo $? > {rcfile}"
            await _run(sb, "bash", ["-lc", f"nohup bash -lc {shlex.quote(launch)} >/dev/null 2>&1 & echo $! > {pidfile}"])
            state = "installing"
    rc = (await _read(sb, rcfile, "")).strip()
    if state != "ready" and rc and rc != "0":
        state = "failed"
    log = await _run(sb, "bash", ["-lc", f"tail -n 40 {logfile} 2>/dev/null || true"])
    return {"state":state,"return_code":rc or None,"log":log.splitlines()}

@app.get("/")
async def root():
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.get("/api/setup")
async def get_setup():
    sb = await _sb(); await _bootstrap(sb)
    try: profile = json.loads(await _read(sb, f"{DATA}/profile.json", "{}") or "{}")
    except Exception: profile = {}
    merged = json.loads(json.dumps(PROFILE_TEMPLATE))
    for k,v in profile.items():
        if isinstance(v,dict) and isinstance(merged.get(k),dict): merged[k].update(v)
        else: merged[k]=v
    raw = await _read(sb, f"{DATA}/searches.yaml", "")
    try: searches = yaml.safe_load(raw) or DEFAULT_SEARCHES
    except Exception: searches = DEFAULT_SEARCHES
    return {"profile":merged,"searches":searches,"resume_pdf":await _exists(sb,f"{DATA}/resume.pdf"),"resume_txt":await _exists(sb,f"{DATA}/resume.txt"),"app_dir":DATA,"cloud":True}

@app.post("/api/setup")
async def save_setup(payload: ProfilePayload):
    sb = await _sb(); await _bootstrap(sb)
    await _write(sb, f"{DATA}/profile.json", json.dumps(payload.profile,indent=2).encode())
    if payload.searches is not None:
        await _write(sb, f"{DATA}/searches.yaml", yaml.safe_dump(payload.searches,sort_keys=False).encode())
    allowed={"GEMINI_API_KEY","OPENAI_API_KEY","LLM_URL","LLM_MODEL","CAPSOLVER_API_KEY","ANTHROPIC_API_KEY"}
    current=await _read(sb,f"{DATA}/.env","")
    env={}
    for line in current.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k,v=line.split("=",1); env[k.strip()]=v.strip()
    for k,v in (payload.api_keys or {}).items():
        if k in allowed and v: env[k]=v.strip()
    await _write(sb,f"{DATA}/.env",("\n".join(f"{k}={v}" for k,v in env.items())+("\n" if env else "")).encode())
    return {"ok":True}

@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)):
    sb=await _sb(); data=await file.read(); name=(file.filename or "").lower()
    if name.endswith(".pdf"): path,kind=f"{DATA}/resume.pdf","pdf"
    elif name.endswith(".txt"): path,kind=f"{DATA}/resume.txt","txt"
    else: raise HTTPException(400,"Upload a PDF or TXT resume.")
    await _write(sb,path,data); return {"ok":True,"type":kind,"path":path}

@app.get("/api/doctor")
async def doctor():
    sb=await _sb(); boot=await _bootstrap(sb)
    code=f'''import json,pathlib,shutil
b=pathlib.Path({BASE!r});a=pathlib.Path({DATA!r});env=(a/'.env').read_text() if (a/'.env').exists() else ''
c={{'sandbox':True,'cloud_worker_installed':(b/'.applypilot-cloud-ready').exists(),'applypilot':(b/'.cloudvenv/bin/applypilot').exists(),'node':shutil.which('node') is not None,'npx':shutil.which('npx') is not None,'claude':shutil.which('claude') is not None,'chrome':(b/'bin/chromium').exists(),'profile':(a/'profile.json').exists(),'searches':(a/'searches.yaml').exists(),'resume':(a/'resume.pdf').exists() or (a/'resume.txt').exists(),'llm_key':any((k+'=') in env for k in ('GEMINI_API_KEY','OPENAI_API_KEY','LLM_URL')),'anthropic_key':'ANTHROPIC_API_KEY=' in env}}
c['ready_discovery']=c['cloud_worker_installed'] and c['profile'] and c['searches'];c['ready_ai']=c['ready_discovery'] and c['llm_key'] and c['resume'];c['ready_apply']=c['ready_ai'] and c['claude'] and c['chrome'] and c['anthropic_key'];print(json.dumps(c))'''
    try: checks=json.loads(await _run(sb,"python3",["-c",code]))
    except Exception: checks={"sandbox":True,"ready_discovery":False,"ready_ai":False,"ready_apply":False}
    checks["bootstrap_state"]=boot["state"]; checks["bootstrap_log"]=boot["log"][-12:]; return checks

DB_SCRIPT=f'''import json,pathlib,sqlite3
p=pathlib.Path({DATA!r})/'applypilot.db'
if not p.exists(): print(json.dumps({{'stats':{{'total':0,'found':0,'scored':0,'ready':0,'applied':0,'failed':0}},'jobs':[]}}));raise SystemExit
c=sqlite3.connect(p);c.row_factory=sqlite3.Row;rows=[dict(r) for r in c.execute('select * from jobs order by discovered_at desc limit 2000')]
def s(r):
 if r.get('applied_at'):return 'applied'
 if r.get('apply_status') in ('failed','error'):return 'failed'
 if r.get('tailored_resume_path') and r.get('application_url'):return 'ready'
 if r.get('cover_letter_path'):return 'cover'
 if r.get('tailored_resume_path'):return 'tailored'
 if r.get('fit_score') is not None:return 'scored'
 if r.get('full_description'):return 'enriched'
 return 'found'
for r in rows:r['stage']=s(r)
n={{}}
for r in rows:n[r['stage']]=n.get(r['stage'],0)+1
st={{'total':len(rows),'found':n.get('found',0)+n.get('enriched',0),'scored':n.get('scored',0)+n.get('tailored',0)+n.get('cover',0),'ready':n.get('ready',0),'applied':n.get('applied',0),'failed':n.get('failed',0)}}
print(json.dumps({{'stats':st,'jobs':rows}},default=str))'''

async def _db(sb):
    try:return json.loads(await _run(sb,"python3",["-c",DB_SCRIPT]))
    except Exception:return {"stats":{"total":0,"found":0,"scored":0,"ready":0,"applied":0,"failed":0},"jobs":[]}

@app.get("/api/stats")
async def stats(): return (await _db(await _sb()))["stats"]

@app.get("/api/jobs")
async def jobs(stage:str="all",q:str="",limit:int=500):
    rows=(await _db(await _sb()))["jobs"];ql=q.lower().strip();out=[]
    for r in rows:
        if stage!="all" and r.get("stage")!=stage: continue
        if ql and ql not in " ".join(str(r.get(k) or "") for k in ("title","site","location","url")).lower():continue
        out.append(r)
        if len(out)>=min(limit,2000):break
    return out

def _cmd(p:RunPayload)->str:
    w=max(1,min(p.workers,8))
    if p.command=="run":
        parts=["applypilot","run","--workers",str(w),"--min-score",str(max(1,min(p.min_score,10)))] + (["--dry-run"] if p.dry_run else [])
    elif p.command=="apply":
        parts=["applypilot","apply","--workers",str(w)]
        if p.dry_run:parts.append("--dry-run")
        if p.continuous:parts.append("--continuous")
        if p.headless:parts.append("--headless")
        if p.url:parts += ["--url",p.url]
    elif p.command in {"doctor","status"}:parts=["applypilot",p.command]
    else:raise HTTPException(400,"Unsupported command")
    return " ".join(shlex.quote(x) for x in parts)

@app.post("/api/run")
async def start_run(p:RunPayload):
    sb=await _sb();boot=await _bootstrap(sb)
    if boot["state"]!="ready":raise HTTPException(409,f"Cloud worker is {boot['state']}. Open System and wait for setup to finish.")
    rid=uuid.uuid4().hex[:12];folder=f"{RUNS}/{rid}";cmd=_cmd(p)
    script=f'''mkdir -p {folder};cd {BASE};printf %s {shlex.quote(cmd)} > {folder}/command
( . .cloudvenv/bin/activate;export APPLYPILOT_DIR={DATA};export PATH={BASE}/bin:$PATH;set -a;[ -f {DATA}/.env ] && . {DATA}/.env;set +a;echo Started: {shlex.quote(cmd)};bash -lc {shlex.quote(cmd)};rc=$?;echo $rc > {folder}/rc;echo Finished with code $rc ) > {folder}/log 2>&1 & echo $! > {folder}/pid;cat {folder}/pid'''
    pid=await _run(sb,"bash",["-lc",script]);return {"run_id":rid,"pid":pid,"command_line":cmd}

@app.get("/api/run/{run_id}")
async def get_run(run_id:str):
    if not run_id.isalnum():raise HTTPException(400,"Bad run id")
    sb=await _sb();f=f"{RUNS}/{run_id}"
    code=f'''import json,pathlib,os
f=pathlib.Path({f!r})
if not f.exists():print(json.dumps({{'missing':True}}));raise SystemExit
pid=(f/'pid').read_text().strip() if (f/'pid').exists() else '';rc=(f/'rc').read_text().strip() if (f/'rc').exists() else '';cmd=(f/'command').read_text() if (f/'command').exists() else '';log=(f/'log').read_text(errors='replace').splitlines()[-500:] if (f/'log').exists() else []
alive=False
if pid:
 try:os.kill(int(pid),0);alive=True
 except Exception:pass
status='running' if alive and not rc else ('completed' if rc=='0' else ('failed' if rc else 'queued'))
print(json.dumps({{'id':{run_id!r},'status':status,'return_code':rc or None,'command_line':cmd,'log':log,'pid':pid or None}}))'''
    d=json.loads(await _run(sb,"python3",["-c",code]));
    if d.get("missing"):raise HTTPException(404,"Unknown run")
    return d

@app.post("/api/run/{run_id}/stop")
async def stop_run(run_id:str):
    sb=await _sb();f=f"{RUNS}/{run_id}";await _run(sb,"bash",["-lc",f"pid=$(cat {f}/pid 2>/dev/null||true);[ -n \"$pid\" ]&&kill -TERM $pid 2>/dev/null||true;echo 143>{f}/rc"]);return {"ok":True}

@app.get("/api/runs")
async def list_runs():
    raw=await _run(await _sb(),"bash",["-lc",f"find {RUNS} -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null|tail -20||true"]);return [{"id":x} for x in raw.splitlines() if x]
