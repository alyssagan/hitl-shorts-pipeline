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
.acts .dup.on{background:var(--med);color:#fff;border-color:var(--med)}
textarea.note{width:100%;min-height:52px}
.labelform{display:flex;flex-direction:column;gap:6px;padding:8px;background:var(--bg);border-radius:6px}
.labelform select,.labelform input{width:100%}
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
let logLines=[], logOpen=false, logLevel="INFO";
async function loadLog(){
  try{ const r = await fetch(`/jobs/${JOB}/log?tail=150&level=${logLevel}`); logLines = (await r.text()).split("\n").filter(Boolean); }catch(e){}
  const el = document.getElementById("logbox"); if(el){ el.textContent = logLines.join("\n"); el.scrollTop = el.scrollHeight; }
}
let job=null, decisions={}, notes={}, labels={}, labelSaved={}, labelForms={}, dupIds={}, minScore=0.5, showHidden=false, srcFilter="", kindFilter="", sortBy="risk", busy=false, error="", batchSize=10;
let sceneNarration={}, sceneOrder=null, sceneClipOverride={}, sceneNotes={}, dragSceneId=null;
let dragAssetPath=null, pendingIntent={};   // pendingIntent: assetId -> scene id it was dragged onto, while it's still high-risk/pending
let methodDefs={}, labelReasons=[];

async function loadStatic(){
  // Job-independent, small and unchanging within a session -- fetched once (docs/EVALUATION.md, docs/REVIEW_UI.md).
  try{ methodDefs = await api("GET","/methods"); }catch(e){}
  try{ const r = await api("GET","/label-reasons"); labelReasons = r.suggested_reasons||[]; }catch(e){}
}

async function load(){
  job = await api("GET","/jobs/"+JOB);
  const opt = (job.providers&&job.providers.options)||{};
  if (store.get("minScore:"+JOB)==null) minScore = opt.min_relevance!=null ? Number(opt.min_relevance) : 0.5;
  else minScore = Number(store.get("minScore:"+JOB));
  if (store.get("batchSize:"+JOB)==null) batchSize = opt.max_queries!=null ? Number(opt.max_queries) : 10;
  else batchSize = Number(store.get("batchSize:"+JOB));
  for (const a of job.assets){
    if (a.status!=="pending" && decisions[a.id]==null){ decisions[a.id]=a.status==="approved"?"approve":"reject"; notes[a.id]=a.decision_note||""; }
  }
  syncScenes();
  render();
}
function syncScenes(){
  // Keeps any in-progress narration/order edits across a poll-driven reload -- but only for the SAME set of
  // scenes. A rewrite ("Search again"/"Search again" at gate 3, i.e. scenes/reject) replaces job.scenes
  // entirely with new ids, so a changed id set means start fresh rather than mixing old drafts into it.
  const ids = new Set((job.scenes||[]).map(s=>s.id));
  const known = new Set(Object.keys(sceneNarration));
  const sameSet = ids.size===known.size && [...ids].every(id=>known.has(id));
  if (!sameSet){ sceneNarration={}; sceneOrder=null; sceneClipOverride={}; sceneNotes={}; }
  for (const s of (job.scenes||[])){
    if (sceneNarration[s.id]==null) sceneNarration[s.id] = s.narration;
    if (sceneNotes[s.id]==null) sceneNotes[s.id] = s.note||"";
  }
}
function orderedScenes(){
  if (!sceneOrder) return job.scenes;
  const by = Object.fromEntries(job.scenes.map(s=>[s.id,s]));
  return sceneOrder.map(id=>by[id]).filter(Boolean);
}
const score = a => (a.vetting && a.vetting.relevance!=null) ? a.vetting.relevance : null;
const below = a => score(a)!=null && score(a) < minScore - 1e-9;
const risk = a => (a.vetting&&a.vetting.risk)||"low";

function visible(){
  let xs = job.assets.filter(a => (showHidden || !below(a)) && (!srcFilter||a.source===srcFilter) && (!kindFilter||a.kind===kindFilter));
  // "risk" is the default: safest first (low, then medium, then high risk), and within each risk group,
  // best-scoring (most relevant) items first -- so the top of the page is always what you'd want to approve
  // first, and risk only gets worse as you scroll, with the strongest candidates surfacing first at each level.
  const by = {score:(a,b)=>(score(b)??-1)-(score(a)??-1), source:(a,b)=>a.source.localeCompare(b.source),
              risk:(a,b)=>["low","medium","high"].indexOf(risk(a))-["low","medium","high"].indexOf(risk(b))
                          || (score(b)??-1)-(score(a)??-1)};
  return xs.sort(by[sortBy]);
}
// Label (use/duplicate/irrelevant) and decision (approve/reject) are two independent axes (docs/EVALUATION.md):
// `decision` drives the pipeline's approved pool, `label` drives RELEVANCE_LABELS.jsonl. The three buttons below
// set both at once because in normal review they move together (Use approves + labels "use"; Duplicate/Irrelevant
// both reject + label accordingly, since a duplicate is not automatically wanted in the video) -- but the label
// save itself (saveLabel) is a separate API call from the approve/reject submit, and succeeds immediately so
// labelling works even before you hit "Save and continue".
function setLabel(a, lbl){
  const was = labels[a.id];
  labels[a.id] = was===lbl ? undefined : lbl;
  if (labels[a.id]==null){
    delete labels[a.id]; delete decisions[a.id]; delete labelSaved[a.id];
    render();
    return;
  }
  decisions[a.id] = lbl==="use" ? "approve" : "reject";
  delete labelSaved[a.id];
  render();
  saveLabel(a);
}
async function saveLabel(a){
  const lbl = labels[a.id];
  if (!lbl) return;
  const form = labelForms[a.id]||{};
  const reviewer = (store.get("reviewer")||"").trim();
  try{
    await api("POST", `/jobs/${JOB}/assets/label`, {
      asset_id: a.id, label: lbl, reviewer,
      reason: form.reason||"", note: form.note||"",
      duplicate_of_asset_id: lbl==="duplicate" ? (dupIds[a.id]||"") : "",
    });
    labelSaved[a.id] = true; error="";
  }catch(e){ error = String(e.message||e); }
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
  const v = a.vetting||{}, d = decisions[a.id], r = risk(a), lbl = labels[a.id];
  const media = a.kind==="video"
    ? h("video",{src:`/jobs/${JOB}/assets/${a.id}/file`,controls:true,muted:true,preload:"metadata",playsinline:true})
    : h("img",{src:`/jobs/${JOB}/assets/${a.id}/file`,loading:"lazy",alt:a.title||"",onclick:()=>lightbox(a)});
  const flags = (v.flags||[]).map(f => h("div",{class:"flag"}, h("b",{}, f.rule+" ("+f.severity+")"), ": ", f.message,
                   h("div",{class:"meta"},"evidence: "+f.evidence)));
  // scoring_method/method_version are separate, versioned fields (docs/SCORING_CHANGELOG.md), e.g. "tfidf"/"tfidf-v1" --
  // print both, plus any fallback note, rather than reconstructing a combined string.
  const scoredBy = v.scoring_method ? `scored by: ${v.scoring_method} [${v.method_version}]`
      + (v.scoring_fallback_note ? ` (${v.scoring_fallback_note})` : "") : "scored by: (not scored)";
  const contribs = v.contributions||[];
  const contribLines = contribs.length>1 ? contribs.map(c =>
    `  - ${c.scoring_method} [${c.method_version}]: ${c.score==null?"n/a":pct(c.score)}` +
    (c.used_for_decision?" (used for the decision)":"") + (c.why?` -- ${c.why}`:"")).join("\n")+"\n" : "";
  const def = methodDefs[v.method_version];
  const defText = def ? [def.description, def.formula_or_prompt, def.model_note,
      Object.keys(def.parameters||{}).length ? "Parameters: "+Object.entries(def.parameters).map(([k,val])=>`${k} = ${val}`).join("; ") : "",
      def.why_changed ? "Why this version: "+def.why_changed : ""].filter(Boolean).join("\n\n") : "";
  const why = h("details",{}, h("summary",{},"Why this score and risk"),
    h("div",{class:"why"},
      `Relevance score: ${pct(score(a))}  (threshold at scoring time: ${pct(v.relevance_threshold)}, machine decision: ${v.relevance_decision||"n/a"})\n` +
      `${scoredBy}\n${v.relevance_why||"(no keyword to score against)"}\n` + contribLines +
      `${v.contribution_note||""}\n\nSee docs/SCORING.md for how each method works.`),
    def?h("details",{}, h("summary",{},`What does ${v.method_version} do?`), h("div",{class:"why"}, defText)):null,
    h("div",{class:"why"}, `${v.summary||""}\nRisk-rules version: ${v.method||"?"}\nFound by search: "${a.query||""}"`),
    flags.length?h("div",{class:"why"}, flags):null);
  const needsNote = d==="approve" && r==="high";
  const form = labelForms[a.id]||{};
  const dupOptions = [["","(pick the original asset, optional)"], ...job.assets.filter(x=>x.id!==a.id)
      .map(x=>[x.id, (x.title||x.id).slice(0,50)+" ["+x.id+"]"])];
  const labelForm = lbl ? h("div",{class:"labelform"},
    h("select",{onchange:e=>{labelForms[a.id]=Object.assign({},form,{reason:e.target.value});}},
      [["","(no reason given)"],...labelReasons.map(x=>[x,x]),["other","other (see note)"]]
        .map(([val,t])=>h("option",{value:val,selected:val===(form.reason||"")},t))),
    lbl==="duplicate" ? h("select",{onchange:e=>{dupIds[a.id]=e.target.value;}},
      dupOptions.map(([val,t])=>h("option",{value:val,selected:val===(dupIds[a.id]||"")},t))) : null,
    h("input",{type:"text",placeholder:"note (optional)",value:form.note||"",
      oninput:e=>{labelForms[a.id]=Object.assign({},form,{note:e.target.value});}}),
    h("button",{onclick:()=>saveLabel(a)}, labelSaved[a.id]?"saved ✓ -- update":"save reason/note")) : null;
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
          oninput:e=>{notes[a.id]=e.target.value; updateSubmit();}}, notes[a.id]||""):null,
      labelForm),
    h("div",{class:"acts"},
      h("button",{class:"use"+(lbl==="use"?" on":""),disabled:v.usable===false,onclick:()=>setLabel(a,"use")},"Use"),
      h("button",{class:"dup"+(lbl==="duplicate"?" on":""),onclick:()=>setLabel(a,"duplicate")},"Duplicate"),
      h("button",{class:"rej"+(lbl==="irrelevant"?" on":""),onclick:()=>setLabel(a,"irrelevant")},"Irrelevant")));
}

function lightbox(a){
  const el = h("div",{class:"lb",onclick:()=>el.remove()}, h("img",{src:`/jobs/${JOB}/assets/${a.id}/file`}));
  document.body.append(el);
}

function updateSubmit(){
  const b = document.getElementById("submit"); if(!b) return;
  const c = counts();
  const missing = job.assets.filter(a=>decisions[a.id]==="approve" && risk(a)==="high" && !(notes[a.id]||"").trim()).length;
  b.disabled = busy || c.use===0 || missing>0;
  const skipped = c.und + c.hidden;
  b.textContent = c.use===0 ? "Approve at least one" : missing>0 ? `${missing} high-risk need a note`
    : `Save and continue (${c.use} used${skipped?`, ${skipped} skipped`:""})`;
}

async function submit(){
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    const out = {};
    for (const a of job.assets){
      if (a.status!=="pending" && decisions[a.id]===(a.status==="approved"?"approve":"reject")) continue;
      let d = decisions[a.id], note = notes[a.id]||"";
      if (!d){ d="reject"; note = below(a) ? `hidden below the ${pct(minScore)} relevance threshold (score ${pct(score(a))}); not looked at`
                                            : `no decision made in the review page (left undecided, so not used); score ${pct(score(a))}`; }
      if (d==='reject' && !note) note = labels[a.id] ? `marked ${labels[a.id]}` : 'marked irrelevant';
      // `label` is saved separately and immediately via /assets/label (setLabel/saveLabel above) as soon as you
      // click Use/Duplicate/Irrelevant, not batched into this submit -- so it is intentionally NOT sent here.
      out[a.id]={decision:d, note};
    }
    await api("POST",`/jobs/${JOB}/assets/review`,{decisions:out, reviewer});
    await api("POST",`/jobs/${JOB}/assets/approve`,{reviewer});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function missingHighRiskNotes(){
  // Same rule "Save and continue" enforces: a HIGH-risk Use needs a written reason before it can be saved.
  return job.assets.filter(a=>decisions[a.id]==="approve" && risk(a)==="high" && !(notes[a.id]||"").trim()).length;
}
async function persistDecisions(reviewer){
  // Saves every decision made SO FAR (Use/reject clicks on this and any earlier batch) via /assets/review,
  // WITHOUT calling /assets/approve -- so the job stays in asset review and nothing moves to scenes yet.
  // This is what makes a Use survive "Next batch" / "Search again": once saved, an asset's status is no
  // longer "pending", so the next sourcing+vetting round (pipeline/core/orchestrator.py) leaves it alone --
  // only still-pending assets get rescored, and only decided assets keep their approved/rejected status.
  // Anything left undecided is skipped here (not force-rejected), so it's simply still there, still
  // undecided, next round -- exactly like an asset that hasn't been looked at yet.
  const out = {};
  for (const a of job.assets){
    const d = decisions[a.id];
    if (!d) continue;
    if (a.status!=="pending" && d===(a.status==="approved"?"approve":"reject")) continue;   // already saved as-is
    out[a.id] = {decision:d, note: notes[a.id]||""};
  }
  if (Object.keys(out).length) await api("POST",`/jobs/${JOB}/assets/review`,{decisions:out, reviewer});
}
async function searchAgain(){
  const fb = document.getElementById("fb").value, xq = document.getElementById("xq").value;
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before searching again -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    await api("POST",`/jobs/${JOB}/assets/reject`,{feedback:fb, extra_queries:xq.split(",").map(s=>s.trim()).filter(Boolean), reviewer});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function addUrls(){
  // Gate 2's "Add links" box: paste one or more URLs (like RankReel's link import), same "url | note | position"
  // format scripts/poc.py's --urls file uses. Backed by the same /assets/reject endpoint as Search again/Next
  // batch (extra_urls_text), so it saves your picks first and re-runs sourcing -- yt-dlp/direct-download for
  // whatever's new, nothing already pulled gets re-fetched.
  const ta = document.getElementById("urlsbox");
  const text = ta.value;
  if (!text.trim()){ error="Paste at least one URL first."; render(); return; }
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before adding links -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    await api("POST",`/jobs/${JOB}/assets/reject`,{extra_urls_text:text, reviewer});
    ta.value = "";
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function nextBatch(){
  // Just pulls the next `batchSize` keywords that haven't been searched yet -- no new terms, no reason needed.
  // Sets job.providers.options.max_queries for every round from here on (docs/RUNNING.md "Many keywords: batches").
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before pulling more -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    await api("POST",`/jobs/${JOB}/assets/reject`,{max_queries: Math.max(1, Number(batchSize)||10), reviewer});
    busy=false; await load();
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

/* -------------------------------------------------------------- gate 3: scenes/script */
function sceneText(s){ return sceneNarration[s.id]!=null ? sceneNarration[s.id] : s.narration; }
function scriptText(){
  // The FULL SCRIPT as it stands right now: current order, current in-progress edits -- not job.script,
  // which is frozen at whatever the writer produced and goes stale the moment any scene narration is edited.
  return orderedScenes().map((s,i) =>
    `[Scene ${i+1}${s.clip_path ? " -- clip: "+s.clip_path.split("/").pop() : " -- NO CLIP"}]\n${sceneText(s)}`).join("\n\n");
}
function scriptWordsLine(){
  const words = orderedScenes().reduce((n,s) => n + sceneText(s).trim().split(/\s+/).filter(Boolean).length, 0);
  return `${words} word(s), about ${Math.round(words/2.6)}s spoken`;
}
function updateScriptPreview(){
  // Called on every keystroke in a scene's textarea -- patches just the preview text/word-count directly
  // (no render()), so the textarea itself is never rebuilt and the cursor/focus never jumps mid-edit.
  const el = document.getElementById("scriptPreview"); if (el) el.textContent = scriptText();
  const wc = document.getElementById("scriptWords"); if (wc) wc.textContent = scriptWordsLine();
}
function moveScene(id, dir){
  const ids = (sceneOrder || job.scenes.map(s=>s.id)).slice();
  const i = ids.indexOf(id), j = i+dir;
  if (i<0 || j<0 || j>=ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  sceneOrder = ids;
  render();
}
function dropScene(targetId){
  // Drag-and-drop reorder: drag the handle (draggable, fires dragstart -> sets dragSceneId), drop
  // anywhere on another scene's card (that card listens for dragover/drop) to move it there. Same
  // sceneOrder state the ↑/↓ buttons use, so either way works and they never conflict.
  const id = dragSceneId; dragSceneId = null;
  if (!id || id===targetId) { render(); return; }
  const ids = (sceneOrder || job.scenes.map(s=>s.id)).slice();
  const from = ids.indexOf(id), to = ids.indexOf(targetId);
  if (from<0 || to<0) { render(); return; }
  ids.splice(from, 1);
  ids.splice(to, 0, id);
  sceneOrder = ids;
  render();
}
// -------- Gate 3 drag-and-drop onto a scene: an approved asset (assign), a file from your computer
// (upload), or a video/photo URL (extract) -- see onSceneDrop() below for which one a drop turns into.
function onSceneDragOver(e){ e.preventDefault(); e.dataTransfer.dropEffect = (dragSceneId && !dragAssetPath) ? "move" : "copy"; }
async function onSceneDrop(e, sceneId){
  e.preventDefault();
  if (dragAssetPath){ sceneClipOverride[sceneId]=dragAssetPath; dragAssetPath=null; render(); return; }
  if (dragSceneId){ dropScene(sceneId); return; }
  if (e.dataTransfer.files && e.dataTransfer.files.length){ await uploadToScene(sceneId, e.dataTransfer.files[0]); return; }
  const text = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "").trim();
  if (/^https?:\/\//i.test(text)){ await urlToScene(sceneId, text); return; }
}
function noteAfterAssetAdd(sceneId, prevIds, updatedJob){
  for (const a of (updatedJob.assets||[])) if (!prevIds.has(a.id) && a.status==="pending") pendingIntent[a.id]=sceneId;
}
async function uploadToScene(sceneId, file){
  busy=true; error=""; render();
  try{
    const prevIds = new Set(job.assets.map(a=>a.id));
    const fd = new FormData();
    fd.append("file", file);
    fd.append("reviewer", (store.get("reviewer")||"").trim());
    const r = await fetch(`/jobs/${JOB}/scenes/${sceneId}/upload`, {method:"POST", body: fd});
    const j = await r.json().catch(()=>({}));
    if (!r.ok) throw new Error(j.error || r.statusText);
    noteAfterAssetAdd(sceneId, prevIds, j);
    job = j; busy=false; render();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function urlToScene(sceneId, url){
  busy=true; error=""; render();
  try{
    const prevIds = new Set(job.assets.map(a=>a.id));
    const reviewer = (store.get("reviewer")||"").trim();
    const j = await api("POST", `/jobs/${JOB}/scenes/${sceneId}/from-url`, {url, reviewer});
    noteAfterAssetAdd(sceneId, prevIds, j);
    job = j; busy=false; render();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function approvePendingAsset(assetId){
  const sceneId = pendingIntent[assetId];
  if (!sceneId) return;
  const el = document.getElementById("pend-note-"+assetId);
  const note = el ? el.value : "";
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/scenes/${sceneId}/approve-pending`, {asset_id: assetId, note, reviewer});
    delete pendingIntent[assetId];
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function sceneNumberFor(sceneId){
  const i = orderedScenes().findIndex(s=>s.id===sceneId);
  return i<0 ? "?" : i+1;
}
function assetStrip(){
  const approved = job.assets.filter(a=>a.status==="approved");
  if (!approved.length) return null;
  return h("div",{class:"panel"},
    h("b",{},"Drag a clip onto a scene to use it"),
    h("div",{class:"sub"},"Every approved photo/video (sourced or dragged in). A scene card also accepts a file "+
      "dragged straight from your computer, or a video/photo link dropped onto it."),
    h("div",{style:"display:flex;gap:8px;overflow-x:auto;padding:8px 0"},
      approved.map(a => h("div",{draggable:"true", title:a.title||a.id,
          style:"flex:none;width:84px;height:84px;border:2px solid var(--line);border-radius:8px;overflow:hidden;"+
                "cursor:grab;background:#000;display:flex;align-items:center;justify-content:center",
          ondragstart:e=>{dragAssetPath=a.path; e.dataTransfer.effectAllowed="copy"; e.dataTransfer.setData("text/plain",a.path);},
          ondragend:()=>{dragAssetPath=null;}},
        a.kind==="video"
          ? h("video",{src:`/jobs/${JOB}/assets/${a.id}/file`,muted:true,style:"max-width:100%;max-height:100%"})
          : h("img",{src:`/jobs/${JOB}/assets/${a.id}/file`,style:"max-width:100%;max-height:100%;object-fit:cover"})))));
}
function pendingAssetsPanel(){
  const pend = job.assets.filter(a=>a.status==="pending");
  if (!pend.length) return null;
  return h("div",{class:"panel"},
    h("b",{},"Dragged in, needs a decision"),
    h("div",{class:"sub"},"High risk (unknown license, or a platform video someone else uploaded) -- add a note saying "+
      "why it's OK to use, then approve. It isn't assigned to any scene until you do."),
    pend.map(a => h("div",{style:"display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap"},
      h("span",{class:"meta"}, (a.title||a.id) + (pendingIntent[a.id] ? ` -- for scene ${sceneNumberFor(pendingIntent[a.id])}` : " -- no scene picked")),
      h("span",{class:"b hi"}, a.vetting ? a.vetting.risk : "?"),
      h("input",{type:"text",id:"pend-note-"+a.id,placeholder:"why is this OK to use?",style:"flex:1;min-width:160px"}),
      h("button",{disabled: busy || !pendingIntent[a.id], onclick:()=>approvePendingAsset(a.id)}, "Approve & use"))));
}
async function saveSceneEdits(reviewer){
  const edits = {};
  for (const s of job.scenes){
    const changes = {};
    if (sceneNarration[s.id]!=null && sceneNarration[s.id] !== s.narration) changes.narration = sceneNarration[s.id];
    if (sceneClipOverride[s.id]!=null && sceneClipOverride[s.id] !== (s.clip_path||"")) changes.clip_path = sceneClipOverride[s.id];
    if (sceneNotes[s.id]!=null && sceneNotes[s.id] !== (s.note||"")) changes.note = sceneNotes[s.id];
    if (Object.keys(changes).length) edits[s.id] = changes;
  }
  const reordered = !!sceneOrder && sceneOrder.join(",") !== job.scenes.map(s=>s.id).join(",");
  if (!Object.keys(edits).length && !reordered) return;      // nothing changed -- don't bother the API
  const payload = {reviewer};
  if (Object.keys(edits).length) payload.edits = edits;
  if (reordered) payload.order = sceneOrder;
  await api("PATCH", `/jobs/${JOB}/scenes`, payload);
}
async function saveScenesClick(){
  busy=true; error=""; render();
  try{ await saveSceneEdits((store.get("reviewer")||"").trim()); busy=false; await load(); }
  catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function approveScenes(){
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await saveSceneEdits(reviewer);                  // whatever you typed gets saved before it renders, not lost
    await api("POST",`/jobs/${JOB}/scenes/approve`,{reviewer});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function rejectScenes(){
  const fb = document.getElementById("sfb").value;
  if (!fb.trim()){ error="Say what should change before asking for a rewrite."; render(); return; }
  busy=true; error=""; render();
  try{
    await api("POST",`/jobs/${JOB}/scenes/reject`,{feedback:fb, reviewer:(store.get("reviewer")||"").trim()});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function sceneCard(s, i, n){
  const approved = job.assets.filter(a=>a.status==="approved");
  // job.uses_sources is a plain @property on the backend Job model, not a @computed_field, so it's never
  // in the JSON the API sends -- mirror its logic here instead: bool(providers.sources) (pipeline/core/models.py).
  const usesSources = !!(job.providers && job.providers.sources && job.providers.sources.length);
  const curClip = sceneClipOverride[s.id]!=null ? sceneClipOverride[s.id] : (s.clip_path||"");
  const clipPicker = (usesSources && approved.length) ? h("label",{}, "Clip ",
    h("select",{onchange:e=>{sceneClipOverride[s.id]=e.target.value; render();}},
      [["", s.clip_path ? "(keep: "+s.clip_path.split("/").pop()+")" : "(no clip)"],
       ...approved.map(a=>[a.path, (a.title||a.id).slice(0,44)])]
        .map(([v,t])=>h("option",{value:v,selected:v===curClip},t)))) : null;
  const dragging = dragSceneId===s.id;
  return h("div",{class:"panel", style: dragging ? "opacity:.4" : "",
      ondragover:onSceneDragOver, ondrop:e=>onSceneDrop(e,s.id)},
    h("div",{class:"bar"},
      h("span",{draggable:"true",title:"Drag to reorder",style:"cursor:grab;font-size:16px;color:var(--mute);user-select:none;padding:0 4px;",
          ondragstart:e=>{dragSceneId=s.id; e.dataTransfer.effectAllowed="move"; e.dataTransfer.setData("text/plain",s.id);},
          ondragend:()=>{dragSceneId=null; render();}}, "☰"),
      h("b",{}, `Scene ${i+1} of ${n}`),
      h("button",{disabled:i===0,onclick:()=>moveScene(s.id,-1),title:"Move earlier"},"↑"),
      h("button",{disabled:i===n-1,onclick:()=>moveScene(s.id,1),title:"Move later"},"↓"),
      h("span",{class:"meta"}, s.clip_path ? "clip: "+s.clip_path.split("/").pop() : "NO CLIP -- drag one onto this card, or add footage to library/clips"),
      s.clip_reason?h("span",{class:"meta"},"("+s.clip_reason+")"):null,
      clipPicker),
    h("div",{class:"meta",style:"margin-top:6px"}, "Narration (spoken)"),
    h("textarea",{class:"note",style:"min-height:80px",
        oninput:e=>{sceneNarration[s.id]=e.target.value; updateScriptPreview();}}, sceneText(s)),
    h("div",{class:"meta",style:"margin-top:6px"}, "Your note (private -- never spoken, never sent to the renderer)"),
    h("textarea",{class:"note",style:"min-height:44px",placeholder:"e.g. reconsider this clip, double-check this date, come back to this later…",
        oninput:e=>{sceneNotes[s.id]=e.target.value;}}, sceneNotes[s.id]!=null?sceneNotes[s.id]:(s.note||"")));
}

function render(){
  if(!job) return;
  const c = counts(), reviewing = job.state==="assets_review", inScenes = job.state==="scenes_review";
  const sources = [...new Set(job.assets.map(a=>a.source))];
  const opt=(id,cur,vals)=>h("select",{id,onchange:e=>{ if(id==="src")srcFilter=e.target.value; if(id==="kind")kindFilter=e.target.value; if(id==="sort")sortBy=e.target.value; render();}},
      vals.map(([v,t])=>h("option",{value:v,selected:v===cur},t)));
  const xs = visible();
  const warn = (job.source_notes||[]).filter(n=>n.warning).map(n=>h("div",{class:"banner"},"Warning: "+n.warning));
  const top = h("header",{},
    h("div",{}, h("h1",{}, job.subject), h("div",{class:"sub"}, "Job ", job.id, " · state: ", h("span",{class:"chip"}, job.state), " · ",
        h("a",{href:"/review"},"all projects"), " · ", h("a",{href:`/jobs/${JOB}/decisions?format=md`,target:"_blank"},"decision log"))),
    h("div",{class:"bar"},
      inScenes ? h("span",{class:"tally"}, `${job.scenes.length} scene(s)`) : h("span",{class:"tally"}, `${c.use} use · ${c.rej} reject · ${c.und} undecided · ${c.hidden} hidden`),
      h("label",{}, "Your name ", h("input",{type:"text",value:store.get("reviewer")||"",placeholder:"recorded in the log",oninput:e=>store.set("reviewer",e.target.value)})),
      inScenes ? null : h("label",{}, "Min score ", h("input",{type:"range",min:0,max:100,step:5,value:Math.round(minScore*100),
          oninput:e=>{minScore=e.target.value/100;store.set("minScore:"+JOB,String(minScore));render();}}), h("b",{}, pct(minScore))),
      inScenes ? null : h("label",{}, h("input",{type:"checkbox",checked:showHidden,onchange:e=>{showHidden=e.target.checked;render();}}), "show below threshold"),
      inScenes ? null : h("label",{}, "Source ", opt("src",srcFilter,[["","all"],...sources.map(s=>[s,s])])),
      inScenes ? null : h("label",{}, "Type ", opt("kind",kindFilter,[["","all"],["image","photos"],["video","videos"]])),
      inScenes ? null : h("label",{}, "Sort ", opt("sort",sortBy,[["risk","risk (low first), best score first within each"],["score","best score"],["source","source"]])),
      inScenes ? null : h("button",{onclick:()=>bulk("use")},"Use all shown (not high-risk)"),
      inScenes ? null : h("button",{onclick:()=>bulk("rej")},"Mark all shown undecided irrelevant"),
      inScenes ? null : h("button",{id:"submit",class:"primary",disabled:true,onclick:submit},"...")));
  const body = h("main",{},
    (!reviewing && !inScenes)?h("div",{class:"banner"},`This project is in state "${job.state}", not asset or scene review. This page will refresh when it reaches one.`):null,
    warn, error?h("div",{class:"err"},error):null,
    inScenes ? sceneReviewBody() : assetReviewBody(xs),
    h("div",{class:"panel"}, h("details",{open:logOpen,ontoggle:e=>{logOpen=e.target.open; if(logOpen) loadLog();}},
      h("summary",{},"Activity log (what the pipeline did, step by step)"),
      h("div",{class:"bar"}, h("label",{},"Detail ", h("select",{onchange:e=>{logLevel=e.target.value;loadLog();}},
          ["INFO","DEBUG","WARN","ERROR"].map(l=>h("option",{value:l,selected:l===logLevel},l)))),
        h("a",{href:`/jobs/${JOB}/log?level=DEBUG`,target:"_blank"},"open full log")),
      h("pre",{id:"logbox",class:"why",style:"max-height:280px;overflow:auto"}, logLines.join("\n")))),
    reviewing ? h("div",{class:"panel"}, h("b",{},"Not enough good ones? Get more"),
      h("div",{class:"sub"},"Either button saves your Use/Duplicate/Irrelevant picks on this page first, so they're kept -- "+
        "nothing you've already decided gets lost or asked about again. \"Next batch\" just searches more of your "+
        "already-approved keywords, no typing needed. \"Search again\" is for when you want to steer it: add new terms, or say what was wrong."),
      h("div",{class:"bar"},
        h("label",{}, "Batch size ", h("input",{type:"number",id:"bs",min:1,max:200,value:batchSize,style:"width:64px",
            oninput:e=>{batchSize=Math.max(1,Number(e.target.value)||1); store.set("batchSize:"+JOB,String(batchSize)); render();}})),
        h("button",{class:"primary",disabled:busy,onclick:nextBatch,title:"Search the next "+batchSize+" keyword(s) that haven't been searched yet"},
          `Next batch (${batchSize})`)),
      h("div",{class:"bar",style:"margin-top:8px"},
        h("input",{type:"text",id:"xq",placeholder:"e.g. whitechapel 1888, victorian london street",size:44}),
        h("input",{type:"text",id:"fb",placeholder:"what was wrong with these?",size:34}),
        h("button",{disabled:busy,onclick:searchAgain},"Search again")),
      h("div",{style:"margin-top:12px;padding-top:12px;border-top:1px solid var(--line)"},
        h("div",{class:"sub"},"Or add links -- a YouTube/TikTok/X/Vimeo/Instagram/news link (pulled with yt-dlp) or a direct "+
          ".mp4/.jpg/... link, one per line. Optionally \"URL | note | position\" (position: a scene number or intro/end). "+
          "Platform videos are flagged high-risk by vetting -- approving one needs a written reason, same as everywhere else."),
        h("textarea",{id:"urlsbox",class:"note",style:"min-height:70px;font-family:monospace",
            placeholder:"https://www.youtube.com/watch?v=... | crime-scene b-roll | 3\nhttps://example.com/clip.mp4"}),
        h("div",{class:"bar",style:"margin-top:6px"},
          h("button",{class:"primary",disabled:busy,onclick:addUrls},"Add links")))) : null);
  $app.replaceChildren(top, body);
  updateSubmit();
  document.querySelectorAll("video").forEach(v=>v.muted=true);
}
function assetReviewBody(xs){
  return h("div",{},
    h("div",{class:"sub",style:"margin-bottom:10px"},
      `Showing ${xs.length} of ${job.assets.length}. Score = share of a keyword's words found in the item's own title/description/tags (docs/SCORING.md). `+
      `Items under ${pct(minScore)} are hidden. Anything you leave undecided or hidden when you submit is not used (logged as rejected with a note) and is NOT counted as a training label. Only your Use / Duplicate / Irrelevant clicks are saved as labels, immediately, one per click (RELEVANCE_LABELS.jsonl in the project folder) -- separately from "Save and continue", which records the approve/reject decision.`),
    h("div",{class:"grid"}, xs.map(card)));
}
function sceneReviewBody(){
  // Gate 3: the full script (live, from current scene narration -- not the frozen job.script) plus one
  // editable card per scene (docs/REVIEW_UI.md). Matches what scripts/poc.py does in the terminal, just
  // easier to read and edit as a whole instead of one scene at a time on a command line.
  const scenes = orderedScenes();
  return h("div",{},
    h("div",{class:"panel"},
      h("b",{},"Full script"),
      h("div",{class:"sub"},"This reflects your edits below as you type -- it's what will actually be spoken, not what the writer first drafted. "+
        "Edit the narration in each scene's box; reorder scenes with the ↑/↓ buttons."),
      h("div",{id:"scriptWords",class:"meta",style:"margin:4px 0"}, scriptWordsLine()),
      h("pre",{id:"scriptPreview",class:"why",style:"max-height:320px;overflow:auto"}, scriptText())),
    assetStrip(),
    pendingAssetsPanel(),
    h("div",{}, scenes.map((s,i)=>sceneCard(s,i,scenes.length))),
    h("div",{class:"panel"},
      h("div",{class:"bar"},
        h("button",{disabled:busy,onclick:saveScenesClick},"Save changes"),
        h("button",{class:"primary",disabled:busy,onclick:approveScenes},"Approve and render")),
      h("div",{class:"sub",style:"margin-top:6px"},"\"Save changes\" keeps you here so you can keep editing. \"Approve and render\" saves whatever's "+
        "unsaved too, then starts the (slow) render -- your last chance to change anything before it."),
      h("div",{class:"bar",style:"margin-top:10px"},
        h("input",{type:"text",id:"sfb",placeholder:"what should change? (e.g. shorter, different tone, wrong facts)",size:50}),
        h("button",{disabled:busy,onclick:rejectScenes},"Ask for a rewrite instead"))));
}

async function poll(){
  try{
    const j = await api("GET","/jobs/"+JOB);
    const was = job && job.state;
    if (logOpen) loadLog();
    if (j.state!==was || (j.assets||[]).length!==(job.assets||[]).length){ if(!busy) await load(); }
  }catch(e){}
}

(async function(){
  try{
    if(!JOB) return await listPage();
    await Promise.all([loadStatic(), load()]); setInterval(poll, 4000);
  }catch(e){ $app.replaceChildren(h("main",{},h("div",{class:"err"},String(e.message||e)))); }
})();
</script></body></html>
"""
