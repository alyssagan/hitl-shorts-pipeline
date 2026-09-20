"""The review web page (served at /review and /review/{job_id}).

One self-contained HTML page. It holds no business logic: it reads the job from the JSON API and posts the
same decisions the terminal does (POST /jobs/{id}/assets/review then /assets/approve), so every click ends up
in the same hash-chained decision log with your name on it.
"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Asset review</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6a66;--line:#dedbd3;--ok:#1d7a46;--okbg:#e4f4ea;--no:#b3261e;--nobg:#fbe9e7;
--hi:#b3261e;--med:#a15c00;--lo:#1d7a46;--acc:#2b4fd6}
@media (prefers-color-scheme:dark){:root{--bg:#16161a;--card:#202026;--ink:#ecebe6;--mute:#9b9a95;--line:#34343c;--okbg:#17301f;--nobg:#3a1f1c;--acc:#8ea4ff;--ok:#5fd18b;--no:#ff8a80;--hi:#ff8a80;--med:#ffb74d;--lo:#5fd18b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px}
h1{font-size:18px;margin:0 0 2px}.sub{color:var(--mute);font-size:13px}
.bar{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;margin-top:10px}
.bar label{font-size:13px;color:var(--mute);display:flex;align-items:center;gap:6px}
input,select,textarea,button{font:inherit;color:inherit}
input[type=text],select,textarea{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:5px 8px}
button{border:1px solid var(--line);background:var(--card);border-radius:6px;padding:6px 12px;cursor:pointer}
button:hover{border-color:var(--acc)}button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--acc);color:#fff;border-color:var(--acc)}
.tally{font-weight:600}.chip{display:inline-block;border-radius:99px;padding:1px 9px;font-size:12px;border:1px solid var(--line);color:var(--mute)}
main{padding:16px;max-width:1500px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
.card{background:var(--card);border:2px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card[data-d=approve]{border-color:var(--ok)}.card[data-d=reject]{border-color:var(--no);opacity:.72}
.media{background:#000;display:flex;align-items:center;justify-content:center;height:230px}
.media img,.media video{max-width:100%;max-height:100%;object-fit:contain;cursor:zoom-in}
.body{padding:10px 12px;display:flex;flex-direction:column;gap:6px;flex:1}
.title{font-weight:600;word-break:break-word}.meta{font-size:12.5px;color:var(--mute);word-break:break-word}
.badges{display:flex;flex-wrap:wrap;gap:5px}.b{font-size:12px;border-radius:5px;padding:1px 7px;border:1px solid var(--line)}
.b.hi{color:var(--hi);border-color:var(--hi)}.b.med{color:var(--med);border-color:var(--med)}.b.lo{color:var(--lo);border-color:var(--lo)}
.score{font-weight:700}
details{font-size:13px}summary{cursor:pointer;color:var(--acc)}
.why{margin:6px 0 0;padding:8px;background:var(--bg);border-radius:6px;font-size:12.5px;white-space:pre-wrap;word-break:break-word}
.flag{margin:4px 0}.flag b{font-size:12px}
.acts{display:flex;gap:8px;padding:0 12px 12px}.acts button{flex:1;font-weight:600}
.acts .use.on{background:var(--ok);color:#fff;border-color:var(--ok)}.acts .rej.on{background:var(--no);color:#fff;border-color:var(--no)}
textarea.note{width:100%;min-height:52px}
.banner{padding:10px 14px;border-radius:8px;background:var(--nobg);margin-bottom:14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:16px 0}
.err{color:var(--no);margin:8px 0;white-space:pre-wrap}
.lb{position:fixed;inset:0;background:rgba(0,0,0,.88);display:flex;align-items:center;justify-content:center;z-index:20}
.lb img,.lb video{max-width:94vw;max-height:94vh}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:8px;text-align:left}a{color:var(--acc)}
</style></head><body>
<div id="app"></div>
<script>
"use strict";
const $app = document.getElementById("app");
const parts = location.pathname.split("/").filter(Boolean);   // ["review", "<id>"]
const JOB = parts[1] || null;
const store = {get(k){try{return localStorage.getItem(k)}catch(e){return null}}, set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};

function h(tag, attrs, ...kids){
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs||{})){
    if (k==="class") e.className=v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v===true) e.setAttribute(k,""); else if (v!==false && v!=null) e.setAttribute(k,v);
  }
  for (const c of kids.flat()) if (c!=null && c!==false) e.append(c.nodeType?c:document.createTextNode(String(c)));
  return e;
}
async function api(method, path, body){
  const r = await fetch(path,{method,headers:{"content-type":"application/json"},body:body?JSON.stringify(body):undefined});
  const j = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const pct = v => v==null ? "n/a" : Math.round(v*100)+"%";

/* ---------------------------------------------------------------- job list */
async function listPage(){
  const jobs = await api("GET","/jobs");
  const rows = (Array.isArray(jobs)?jobs:(jobs.jobs||[])).map(j =>
    h("tr",{}, h("td",{}, h("a",{href:"/review/"+j.id}, j.subject||j.id)), h("td",{}, j.state), h("td",{}, (j.assets||[]).length+" assets")));
  $app.replaceChildren(h("header",{}, h("h1",{},"Video projects"), h("div",{class:"sub"},"Pick one to review its assets.")),
    h("main",{}, h("table",{}, rows.length?rows:h("tr",{},h("td",{},"No projects yet.")))));
}

/* ------------------------------------------------------------ review page */
let job=null, decisions={}, notes={}, minScore=0.5, showHidden=false, srcFilter="", kindFilter="", sortBy="score", busy=false, error="";

async function load(){
  job = await api("GET","/jobs/"+JOB);
  const opt = (job.providers&&job.providers.options)||{};
  if (store.get("minScore:"+JOB)==null) minScore = opt.min_relevance!=null ? Number(opt.min_relevance) : 0.5;
  else minScore = Number(store.get("minScore:"+JOB));
  for (const a of job.assets){
    if (a.status!=="pending" && decisions[a.id]==null){ decisions[a.id]=a.status==="approved"?"approve":"reject"; notes[a.id]=a.decision_note||""; }
  }
  render();
}
const score = a => (a.vetting && a.vetting.relevance!=null) ? a.vetting.relevance : null;
const below = a => score(a)!=null && score(a) < minScore - 1e-9;
const risk = a => (a.vetting&&a.vetting.risk)||"low";

function visible(){
  let xs = job.assets.filter(a => (showHidden || !below(a)) && (!srcFilter||a.source===srcFilter) && (!kindFilter||a.kind===kindFilter));
  const by = {score:(a,b)=>(score(b)??-1)-(score(a)??-1), source:(a,b)=>a.source.localeCompare(b.source),
              risk:(a,b)=>["high","medium","low"].indexOf(risk(a))-["high","medium","low"].indexOf(risk(b))};
  return xs.sort(by[sortBy]);
}
function setDecision(a, d){
  decisions[a.id] = decisions[a.id]===d ? undefined : d; if(decisions[a.id]==null) delete decisions[a.id];
  render();
}
function counts(){
  let use=0, rej=0, und=0, hidden=0;
  for (const a of job.assets){
    const d = decisions[a.id];
    if (d==="approve") use++; else if (d==="reject") rej++;
    else if (below(a)) hidden++; else und++;
  }
  return {use, rej, und, hidden};
}

function card(a){
  const v = a.vetting||{}, d = decisions[a.id], r = risk(a);
  const media = a.kind==="video"
    ? h("video",{src:`/jobs/${JOB}/assets/${a.id}/file`,controls:true,muted:true,preload:"metadata",playsinline:true})
    : h("img",{src:`/jobs/${JOB}/assets/${a.id}/file`,loading:"lazy",alt:a.title||"",onclick:()=>lightbox(a)});
  const flags = (v.flags||[]).map(f => h("div",{class:"flag"}, h("b",{}, f.rule+" ("+f.severity+")"), ": ", f.message,
                   h("div",{class:"meta"},"evidence: "+f.evidence)));
  const why = h("details",{}, h("summary",{},"Why this score and risk"),
    h("div",{class:"why"},
      `Relevance score: ${pct(score(a))}  (threshold ${pct(minScore)})\n${v.relevance_why||"(no keyword to score against)"}\n` +
      `Formula: best approved keyword, share of its words found in title/description/tags/page URL. See docs/SCORING.md.\n\n` +
      `${v.summary||""}\nRules version: ${v.method||"?"}\nFound by search: "${a.query||""}"`),
    flags.length?h("div",{class:"why"}, flags):null);
  const needsNote = d==="approve" && r==="high";
  return h("div",{class:"card","data-d":d||""},
    h("div",{class:"media"}, media),
    h("div",{class:"body"},
      h("div",{class:"badges"},
        h("span",{class:"b score"}, "score "+pct(score(a))),
        h("span",{class:"b "+({high:"hi",medium:"med",low:"lo"}[r])}, "risk "+r),
        h("span",{class:"b"}, a.source), h("span",{class:"b"}, a.kind),
        below(a)?h("span",{class:"b"},"below threshold"):null,
        v.usable===false?h("span",{class:"b hi"},"can't be used (too small)"):null),
      h("div",{class:"title"}, a.title||a.id),
      h("div",{class:"meta"}, "license: "+(a.license||"(none found)")+" · by: "+(a.author||"(unknown)")),
      h("div",{class:"meta"}, a.page_url||a.source_url ? h("a",{href:a.page_url||a.source_url,target:"_blank",rel:"noopener"},"open original page") : "no source page recorded",
         a.width?` · ${a.width}×${a.height}`:"", a.duration?` · ${Math.round(a.duration)}s`:""),
      a.description?h("div",{class:"meta"}, a.description.slice(0,260)):null,
      why,
      needsNote?h("textarea",{class:"note",placeholder:"HIGH risk: why is it OK to use this? (required)",
          oninput:e=>{notes[a.id]=e.target.value; updateSubmit();}}, notes[a.id]||""):null),
    h("div",{class:"acts"},
      h("button",{class:"use"+(d==="approve"?" on":""),disabled:v.usable===false,onclick:()=>setDecision(a,"approve")},"Use"),
      h("button",{class:"rej"+(d==="reject"?" on":""),onclick:()=>setDecision(a,"reject")},"Reject")));
}

function lightbox(a){
  const el = h("div",{class:"lb",onclick:()=>el.remove()}, h("img",{src:`/jobs/${JOB}/assets/${a.id}/file`}));
  document.body.append(el);
}

function updateSubmit(){
  const b = document.getElementById("submit"); if(!b) return;
  const c = counts();
  const missing = job.assets.filter(a=>decisions[a.id]==="approve" && risk(a)==="high" && !(notes[a.id]||"").trim()).length;
  b.disabled = busy || c.und>0 || c.use===0 || missing>0;
  b.textContent = c.und>0 ? `Decide ${c.und} more first` : c.use===0 ? "Approve at least one" : missing>0 ? `${missing} high-risk need a note` : `Save decisions and continue (${c.use} used)`;
}

async function submit(){
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    const out = {};
    for (const a of job.assets){
      if (a.status!=="pending" && decisions[a.id]===(a.status==="approved"?"approve":"reject")) continue;
      let d = decisions[a.id], note = notes[a.id]||"";
      if (!d){ d="reject"; note=`hidden below the ${pct(minScore)} relevance threshold (score ${pct(score(a))}); not looked at`; }
      out[a.id]={decision:d, note};
    }
    await api("POST",`/jobs/${JOB}/assets/review`,{decisions:out, reviewer});
    await api("POST",`/jobs/${JOB}/assets/approve`,{reviewer});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function searchAgain(){
  const fb = document.getElementById("fb").value, xq = document.getElementById("xq").value;
  busy=true; error=""; render();
  try{
    await api("POST",`/jobs/${JOB}/assets/reject`,{feedback:fb, extra_queries:xq.split(",").map(s=>s.trim()).filter(Boolean), reviewer:(store.get("reviewer")||"").trim()});
    busy=false; decisions={}; notes={}; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function bulk(kind){
  for (const a of visible()){
    if (decisions[a.id]) continue;
    if (kind==="use" && risk(a)!=="high" && (a.vetting||{}).usable!==false) decisions[a.id]="approve";
    if (kind==="rej") decisions[a.id]="reject";
  }
  render();
}

function render(){
  if(!job) return;
  const c = counts(), reviewing = job.state==="assets_review";
  const sources = [...new Set(job.assets.map(a=>a.source))];
  const opt=(id,cur,vals)=>h("select",{id,onchange:e=>{ if(id==="src")srcFilter=e.target.value; if(id==="kind")kindFilter=e.target.value; if(id==="sort")sortBy=e.target.value; render();}},
      vals.map(([v,t])=>h("option",{value:v,selected:v===cur},t)));
  const xs = visible();
  const warn = (job.source_notes||[]).filter(n=>n.warning).map(n=>h("div",{class:"banner"},"Warning: "+n.warning));
  const top = h("header",{},
    h("div",{}, h("h1",{}, job.subject), h("div",{class:"sub"}, "Job ", job.id, " · state: ", h("span",{class:"chip"}, job.state), " · ",
        h("a",{href:"/review"},"all projects"), " · ", h("a",{href:`/jobs/${JOB}/decisions?format=md`,target:"_blank"},"decision log"))),
    h("div",{class:"bar"},
      h("span",{class:"tally"}, `${c.use} use · ${c.rej} reject · ${c.und} undecided · ${c.hidden} hidden`),
      h("label",{}, "Your name ", h("input",{type:"text",value:store.get("reviewer")||"",placeholder:"recorded in the log",oninput:e=>store.set("reviewer",e.target.value)})),
      h("label",{}, "Min score ", h("input",{type:"range",min:0,max:100,step:5,value:Math.round(minScore*100),
          oninput:e=>{minScore=e.target.value/100;store.set("minScore:"+JOB,String(minScore));render();}}), h("b",{}, pct(minScore))),
      h("label",{}, h("input",{type:"checkbox",checked:showHidden,onchange:e=>{showHidden=e.target.checked;render();}}), "show below threshold"),
      h("label",{}, "Source ", opt("src",srcFilter,[["","all"],...sources.map(s=>[s,s])])),
      h("label",{}, "Type ", opt("kind",kindFilter,[["","all"],["image","photos"],["video","videos"]])),
      h("label",{}, "Sort ", opt("sort",sortBy,[["score","best score"],["source","source"],["risk","risk (high first)"]])),
      h("button",{onclick:()=>bulk("use")},"Use all shown (not high-risk)"),
      h("button",{onclick:()=>bulk("rej")},"Reject all shown undecided"),
      h("button",{id:"submit",class:"primary",disabled:true,onclick:submit},"...")));
  const body = h("main",{},
    !reviewing?h("div",{class:"banner"},`This project is in state "${job.state}", not asset review. This page will refresh when it reaches asset review.`):null,
    warn, error?h("div",{class:"err"},error):null,
    h("div",{class:"sub",style:"margin-bottom:10px"},
      `Showing ${xs.length} of ${job.assets.length}. Score = share of a keyword's words found in the item's own title/description/tags (docs/SCORING.md). `+
      `Items under ${pct(minScore)} are hidden; if you submit while they are hidden they are recorded as rejected, unseen.`),
    h("div",{class:"grid"}, xs.map(card)),
    h("div",{class:"panel"}, h("b",{},"Not enough good ones? Search again"),
      h("div",{class:"sub"},"Fetches the next page of results (no repeats). Add specific new search terms, comma separated."),
      h("div",{class:"bar"},
        h("input",{type:"text",id:"xq",placeholder:"e.g. whitechapel 1888, victorian london street",size:44}),
        h("input",{type:"text",id:"fb",placeholder:"what was wrong with these?",size:34}),
        h("button",{disabled:!reviewing||busy,onclick:searchAgain},"Search again"))));
  $app.replaceChildren(top, body);
  updateSubmit();
  document.querySelectorAll("video").forEach(v=>v.muted=true);
}

async function poll(){
  try{
    const j = await api("GET","/jobs/"+JOB);
    const was = job && job.state;
    if (j.state!==was || (j.assets||[]).length!==(job.assets||[]).length){ if(!busy) await load(); }
  }catch(e){}
}

(async function(){
  try{
    if(!JOB) return await listPage();
    await load(); setInterval(poll, 4000);
  }catch(e){ $app.replaceChildren(h("main",{},h("div",{class:"err"},String(e.message||e)))); }
})();
</script></body></html>
"""
