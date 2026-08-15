const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
let currentRun=null, pollTimer=null, setupData=null;

function esc(v){return String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]))}
function showView(id){
  $$(".view").forEach(v=>v.classList.toggle("active",v.id===id));
  $$(".nav").forEach(v=>v.classList.toggle("active",v.dataset.view===id));
  const names={dashboard:["Dashboard","Your autonomous job-search command center."],onboarding:["My profile","Answer once. ApplyPilot reuses it everywhere."],jobs:["Jobs","Everything found, scored, prepared, and submitted."],runner:["Automation","Run the original ApplyPilot pipeline from the web."],setup:["System","Check whether this machine is ready."]};
  $("#title").textContent=names[id][0];$("#subtitle").textContent=names[id][1];
  if(id==="jobs")loadJobs(); if(id==="setup")loadDoctor();
}
$$(".nav").forEach(n=>n.onclick=()=>showView(n.dataset.view));

async function api(url,opts={}){const r=await fetch(url,opts);if(!r.ok)throw new Error(await r.text());return r.json()}
async function loadAll(){await Promise.all([loadStats(),loadJobs(true),loadDoctor(),loadSetup()])}

async function loadStats(){
  const s=await api("/api/stats");
  const keys=[["total","All jobs"],["found","Found"],["scored","Scored / prepared"],["ready","Ready"],["applied","Applied"],["failed","Failed"]];
  $("#stats").innerHTML=keys.map(([k,l])=>`<div class=stat><div class=n>${s[k]||0}</div><div class=l>${l}</div></div>`).join("");
  $("#pipeline").innerHTML=[["Found",s.found],["Scored",s.scored],["Ready",s.ready],["Applied",s.applied],["Failed",s.failed]].map(x=>`<div class=pipe><b>${x[1]||0}</b><small>${x[0]}</small></div>`).join("");
}

function jobTable(rows,limit){
  const data=limit?rows.slice(0,limit):rows;
  if(!data.length)return `<p class=muted>No jobs yet. Run Find & prepare jobs.</p>`;
  return `<table><thead><tr><th>Job</th><th>Source</th><th>Location</th><th>Score</th><th>Status</th><th>Attempts</th></tr></thead><tbody>`+
    data.map(j=>`<tr><td class=titlecell><a href="${esc(j.application_url||j.url)}" target=_blank style="color:white;text-decoration:none"><b>${esc(j.title||"Untitled")}</b></a><div class=muted>${esc((j.url||"").slice(0,60))}</div></td><td>${esc(j.site||"")}</td><td>${esc(j.location||"")}</td><td class=score>${j.fit_score??"—"}</td><td><span class="badge ${esc(j.stage)}">${esc(j.stage)}</span>${j.apply_error?`<div class=muted title="${esc(j.apply_error)}">has error</div>`:""}</td><td>${j.apply_attempts||0}</td></tr>`).join("")+
    `</tbody></table>`;
}
async function loadJobs(recentOnly=false){
  const stage=recentOnly?"all":($("#stageFilter")?.value||"all");
  const q=recentOnly?"":encodeURIComponent($("#jobSearch")?.value||"");
  const rows=await api(`/api/jobs?stage=${stage}&q=${q}`);
  if(recentOnly)$("#recentJobs").innerHTML=jobTable(rows,8); else $("#jobsTable").innerHTML=jobTable(rows);
}

const profileFields={
 "personal":["full_name","preferred_name","email","password","phone","address","city","province_state","country","postal_code","linkedin_url","github_url","portfolio_url","website_url"],
 "work_authorization":["legally_authorized_to_work","require_sponsorship","work_permit_type"],
 "availability":["earliest_start_date","available_for_full_time","available_for_contract"],
 "compensation":["salary_expectation","salary_currency","salary_range_min","salary_range_max","currency_conversion_note"],
 "experience":["years_of_experience_total","education_level","current_job_title","current_company","target_role"],
 "skills_boundary":["languages","frameworks","devops","databases","tools"],
 "resume_facts":["preserved_companies","preserved_projects","preserved_school","real_metrics"],
 "eeo_voluntary":["gender","race_ethnicity","veteran_status","disability_status"]
};
const pretty=s=>s.replaceAll("_"," ").replace(/\b\w/g,c=>c.toUpperCase());
function renderProfile(p){
 let html="";
 for(const [section,fields] of Object.entries(profileFields)){
   html+=`<div class=section-title>${pretty(section)}</div><div class=formgrid>`;
   for(const f of fields){
     let v=p?.[section]?.[f]??"";
     if(Array.isArray(v))v=v.join(", ");
     html+=`<label class="${["address","linkedin_url","github_url","portfolio_url","website_url"].includes(f)?"full":""}">${pretty(f)}<input data-section="${section}" data-field="${f}" value="${esc(v)}" ${f==="password"?'type="password"':""}></label>`;
   }
   html+="</div>";
 }
 $("#profileForm").innerHTML=html;
}
async function loadSetup(){
  setupData=await api("/api/setup"); renderProfile(setupData.profile);
  const s=setupData.searches?.searches?.[0]||{};
  $("#searchQuery").value=s.query||"";$("#searchLocation").value=s.location||"";$("#searchSites").value=(s.sites||[]).join(", ");
  $("#resumeStatus").textContent=(setupData.resume_pdf||setupData.resume_txt)?"Résumé already saved.":"No résumé uploaded yet.";
}
async function saveProfile(){
 const p=structuredClone(setupData?.profile||{});
 $$('[data-section]').forEach(el=>{let v=el.value;const sec=el.dataset.section,f=el.dataset.field;if(["skills_boundary"].includes(sec)||(["preserved_companies","preserved_projects","real_metrics"].includes(f)))v=v.split(",").map(x=>x.trim()).filter(Boolean);p[sec]??={};p[sec][f]=v;});
 const searches={searches:[{query:$("#searchQuery").value.trim(),location:$("#searchLocation").value.trim(),sites:$("#searchSites").value.split(",").map(x=>x.trim()).filter(Boolean)}]};
 const api_keys={GEMINI_API_KEY:$("#gemini").value,OPENAI_API_KEY:$("#openai").value,LLM_MODEL:$("#llmModel").value,CAPSOLVER_API_KEY:$("#capsolver").value};
 await api("/api/setup",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile:p,searches,api_keys})});
 alert("Saved directly into ApplyPilot.");
 await loadDoctor();
}
async function uploadResume(){
 const f=$("#resumeFile").files[0]; if(!f)return alert("Choose a PDF or TXT.");
 const fd=new FormData();fd.append("file",f);
 const r=await api("/api/resume",{method:"POST",body:fd});$("#resumeStatus").textContent=`Saved ${r.type.toUpperCase()} résumé.`;
}

async function loadDoctor(){
 const d=await api("/api/doctor");
 $("#doctor").innerHTML=Object.entries(d).filter(([k])=>!k.startsWith("ready_")).map(([k,v])=>`<div class=checkrow><span>${pretty(k)}</span><b class="${v?"ok":"no"}">${v?"READY":"MISSING"}</b></div>`).join("");
 const ok=d.ready_apply;$("#readyDot").style.background=ok?"var(--good)":"var(--warn)";$("#readyText").textContent=ok?"Full auto-apply ready":d.ready_ai?"AI prep ready; apply setup incomplete":"Setup incomplete";
}

async function runPipeline(){
 const body={command:"run",workers:+$("#runWorkers").value,min_score:+$("#runMinScore").value,dry_run:$("#runDry").checked};
 const r=await api("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});watchRun(r.run_id);
}
async function runApply(){
 const body={command:"apply",workers:+$("#applyWorkers").value,dry_run:$("#applyDry").checked,continuous:$("#applyContinuous").checked,headless:$("#applyHeadless").checked,url:$("#applyUrl").value.trim()||null};
 const r=await api("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});watchRun(r.run_id);
}
function watchRun(id){currentRun=id;if(pollTimer)clearInterval(pollTimer);pollRun();pollTimer=setInterval(pollRun,1000)}
async function pollRun(){
 if(!currentRun)return; const r=await api(`/api/run/${currentRun}`);$("#runState").textContent=`${r.status} · ${r.command_line||""}`;$("#log").textContent=(r.log||[]).join("\n")||"Starting…";$("#log").scrollTop=$("#log").scrollHeight;
 if(["completed","failed"].includes(r.status)){clearInterval(pollTimer);pollTimer=null;loadStats();loadJobs(true)}
}
async function stopRun(){if(currentRun)await api(`/api/run/${currentRun}/stop`,{method:"POST"})}
loadAll().catch(e=>console.error(e));
