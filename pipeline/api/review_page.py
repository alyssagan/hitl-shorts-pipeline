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
.ksec{margin:22px 0 10px}.ksec:first-child{margin-top:0}
.ksec .khead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:8px 16px;
  border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:10px}
.ksec .khead b{font-size:14.5px}
.ksec .kempty{color:var(--mute);font-size:13px;padding:2px 0 4px}
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
// "Suggest more search terms" (Gate 2, reuses the same LLM keyword-writing stage the auto-approved
// keyword step -- "Gate 1½", docs/PIPELINE_STAGES.md -- uses: config/query_groups.toml has the shared
// group vocabulary): suggestIds/suggestChecked hold the LAST /keywords/
// suggest call's result until the reviewer approves or dismisses it -- transient, not persisted, since an
// unapproved suggestion sitting unreviewed in job.keywords is already harmless on its own (never searched).
let suggestFeedback="", suggestBusy=false, suggestError="", suggestIds=null, suggestChecked={};
let sceneNarration={}, sceneOrder=null, sceneClipOverride={}, sceneNotes={}, dragSceneId=null;
let dragAssetPath=null, pendingIntent={};   // pendingIntent: assetId -> scene id it was dragged onto, while it's still high-risk/pending
let methodDefs={}, labelReasons=[];
let folderFiles=null, folderFilesOpen=false, folderSelection={};   // path -> note draft, own-footage panel (#10)
let catForms={}, idForms={}, rightsForms={};   // per-asset draft values for the "Case connection & rights" editor (#13)
let assetReport=null, assetReportOpen=false;   // per-job/per-source summary panel (#13)
let coverage=null, coverageLoadedFor=null, coverageOverrideNote="", checklistForms={};   // pre-render visual coverage check (#12)
let checklistOpen=false, newChecklistItem={label:"",group:"case",linked_keyword_term:""};   // adding items (#6/#12)
let setAsideOpen=false;   // "Set aside" panel (Irrelevant/Duplicate) -- collapsed by default, since the whole
                          // point is keeping them out of the way; one click reopens it to reconsider any of them.
let renderSettings={aspect:"9:16"}, cropOpen={}, cropDraft={};   // Gate 3 manual crop tool
let findQuery = null;   // "Find more" panel -- null means "not touched yet, show job.subject"
let whyOpen = {};        // asset id -> bool. Open by default (requested directly) so the score/risk reasons
                          // are visible without a click; explicit false once a reviewer collapses one, so a
                          // later re-render doesn't silently pop it back open under them.
let ytCount = 5, ytResults = null, ytLoading = false, ytError = "", ytLastQuery = "";   // "Find more" YouTube search
let srcSearch = {};      // "Find more": per-source on-demand search state for archive/chronicling_america/commons,
                          // lazily created by srcState() below -- same idea as the yt* variables above, just keyed
                          // by source since there are three of these instead of one.
let findPasteUrl = "", findPasteBusy = false, findPasteError = "", findPasteOk = "";
                          // "Find more"'s own quick paste-back box (requested directly: "i need a way to
                          // streamline this") -- same /assets/add-url "Add links" below already calls, just
                          // reachable right where the plain link-out buttons (Google Images/FindAGrave/TikTok/
                          // Facebook) are, so finding something on one of those sites doesn't mean scrolling
                          // away to paste its link in.
let stockLimitDraft = "", deferRelevanceDraft = false;   // "stop go limits" (requested directly) -- drafts for
                          // the stock photo budget/defer-scoring inputs in "Get more", synced from the job's
                          // current options each load() so the fields always start showing what's actually set.
let scoreRelevanceBusy = false, scoreRelevanceError = "";   // "Score relevance now" (requested directly,
                          // alongside the budget: "pull stock photos first and score relevance later")
let scriptDraft = "", scriptDraftKey = null;   // Gate 1 (script_review): editable draft of job.script, kept
                          // across poll-driven reloads the same way sceneNarration is -- reset only when the
                          // underlying job.script itself changes (a fresh draft, or a rewrite after reject),
                          // never on an unrelated poll tick while the reviewer is mid-edit.
const CATEGORY_LABELS = {verified_case:"verified case", unverified_case_candidate:"unverified case candidate",
  historical_context:"historical context", illustrative_stock:"illustrative stock", reconstruction:"reconstruction"};
const IDENTITY_LABELS = {unverified:"unverified", verified:"verified", disputed:"disputed"};
const RIGHTS_LABELS = {public_domain:"public domain", cc0:"CC0", open_license:"open license",
  paid_license:"paid license", unresolved:"unresolved"};
const GROUP_LABELS = {research:"research", case:"case", historical:"historical", stock:"stock"};
const IMPORT_METHOD_LABELS = {search:"an automated search", manual_url:"a pasted link (\"Add links\")",
  search_pick:"a specific pick from a \"Find more\" search", local_folder:"your own footage folder",
  scene_upload:"a Gate 3 upload/drop"};
// Must match pipeline/sources/groups.py's STOCK_SOURCES exactly (tests/test_review_page_stock_budget.py
// cross-checks the two, same pattern tests/test_review_page_source_search.py uses for SEARCHABLE_SOURCES) --
// only used here to show how much of the stock photo budget has been used, never to decide routing itself.
const STOCK_SOURCES = new Set(["pexels","pixabay","unsplash","nasa"]);
// Must match pipeline/niches.py's NICHE_LABELS exactly (tests/test_review_page_niches.py cross-checks the
// two) -- display text only, purely cosmetic; the niche value itself always comes from the job.
const NICHE_LABELS = {true_crime:"True Crime", conspiracy:"Conspiracy", science:"Science & Astronomy",
  pet_product:"Pet Product", food_bakery:"Food / Bakery"};
// Must match pipeline/stages/scenes/script_styles.py's STYLE_LABELS exactly (tests/test_review_page_niches.py
// cross-checks the two) -- display text only; independent of NICHE_LABELS above, see that module's docstring.
const SCRIPT_STYLE_LABELS = {true_crime_mystery:"History, Conspiracy & True Crime",
  stem_science:"STEM (Science, Anatomy, Biology, Chemistry)", dtc_marketing:"DTC, Pet & Food Marketing",
  math_cs:"Math, Statistics & Computer Science"};

async function loadStatic(){
  // Job-independent, small and unchanging within a session -- fetched once (docs/EVALUATION.md, docs/REVIEW_UI.md).
  try{ methodDefs = await api("GET","/methods"); }catch(e){}
  try{ const r = await api("GET","/label-reasons"); labelReasons = r.suggested_reasons||[]; }catch(e){}
  try{ renderSettings = await api("GET","/render-settings"); }catch(e){}
}

async function load(){
  job = await api("GET","/jobs/"+JOB);
  const opt = (job.providers&&job.providers.options)||{};
  if (store.get("minScore:"+JOB)==null) minScore = opt.min_relevance!=null ? Number(opt.min_relevance) : 0.5;
  else minScore = Number(store.get("minScore:"+JOB));
  if (store.get("batchSize:"+JOB)==null) batchSize = opt.max_queries!=null ? Number(opt.max_queries) : 10;
  else batchSize = Number(store.get("batchSize:"+JOB));
  stockLimitDraft = opt.stock_limit ? String(opt.stock_limit) : "";
  deferRelevanceDraft = !!opt.defer_relevance;
  for (const a of job.assets){
    if (a.status!=="pending" && decisions[a.id]==null){ decisions[a.id]=a.status==="approved"?"approve":"reject"; notes[a.id]=a.decision_note||""; }
  }
  syncScenes();
  syncScriptDraft();
  if (job.state==="scenes_review" && coverageLoadedFor!==job.id){ coverageLoadedFor=job.id; loadCoverage(); }
  render();
}
function syncScriptDraft(){
  const key = job.id+"|"+(job.script||"");
  if (scriptDraftKey !== key){ scriptDraftKey = key; scriptDraft = job.script||""; }
}
async function loadCoverage(){
  // #12: fetched once per job as soon as it reaches scenes_review, so the coverage check is visible before
  // the reviewer even reaches "Approve and render" -- not just when the render is actually attempted.
  try{ coverage = await api("GET", `/jobs/${JOB}/visual-coverage`); }
  catch(e){ coverage = null; }
  render();
}
function syncScenes(){
  // Keeps any in-progress narration/order edits across a poll-driven reload -- but only for the SAME set of
  // scenes. A rewrite ("Search again"/"Search again" at gate 3, i.e. scenes/reject) replaces job.scenes
  // entirely with new ids, so a changed id set means start fresh rather than mixing old drafts into it.
  const ids = new Set((job.scenes||[]).map(s=>s.id));
  const known = new Set(Object.keys(sceneNarration));
  const sameSet = ids.size===known.size && [...ids].every(id=>known.has(id));
  if (!sameSet){ sceneNarration={}; sceneOrder=null; sceneClipOverride={}; sceneNotes={}; coverageLoadedFor=null; }
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
const notUsed = a => {
  // "Set aside" -- Irrelevant/Duplicate both set decisions[a.id]="reject" (see the comment on setLabel()
  // below); once that's true, the item should leave the main grid right away, not wait for a round-trip to
  // the server (requested directly: they were cluttering the grid with nothing to actually decide anymore).
  // An unsaved LOCAL decision always wins over the last-saved server status -- clicking Use on something
  // that's saved as "rejected" from an earlier session must bring it back immediately, not leave it stuck
  // in "Set aside" until the click is saved.
  const d = decisions[a.id];
  return d ? d==="reject" : a.status==="rejected";
};
function manualUrlAssets(){
  // Two ways an asset gets added by a person choosing ONE specific item, rather than an automated batch
  // round keeping whatever it found: pasted into Gate 2's "Add links" (import_method="manual_url",
  // pipeline/sources/urls.py), or picked from a "Find more" search result (import_method="search_pick",
  // pipeline/api/app.py's assets_add_candidate -- covers the archive/chronicling_america/commons "Search
  // ... here" blocks; a YouTube search pick already comes back as "manual_url" since adding one reuses
  // /assets/add-url itself). Both are always pending on arrival (add_reviewable_asset() never
  // auto-approves), and both are easy to lose in the main grid: a platform link is auto-flagged high risk
  // (sorts last by default), and any hand-picked item's title/description often won't text-match the
  // approved keywords well, so it commonly scores under the relevance threshold too -- hidden by default,
  // with nothing on screen to say the add even worked. Kept out of visible()'s main grid and shown in
  // their own always-visible panel instead (manualLinksPanel()) for exactly as long as they're pending AND
  // undecided; once decided (including Irrelevant/Duplicate -- see notUsed() above), it leaves this panel
  // immediately for either the main grid (Use) or the "Set aside" panel (Irrelevant/Duplicate) below.
  return job.assets.filter(a => (a.import_method==="manual_url" || a.import_method==="search_pick") && a.status==="pending" && !notUsed(a));
}
function setAsideAssets(){
  // Everything marked Irrelevant or Duplicate (requested directly: they were staying in the main grid
  // forever with nothing left to do about them, just clutter). Kept out of visible()'s main grid and shown
  // in their own collapsed-by-default panel instead (setAsidePanel()) so a second look is always one click
  // away without the main grid having to carry them permanently.
  return job.assets.filter(notUsed);
}
function visible(){
  const manualPendingIds = new Set(manualUrlAssets().map(a=>a.id));
  const setAsideIds = new Set(setAsideAssets().map(a=>a.id));
  let xs = job.assets.filter(a => !manualPendingIds.has(a.id) && !setAsideIds.has(a.id) &&
      (showHidden || !below(a)) && (!srcFilter||a.source===srcFilter) && (!kindFilter||a.kind===kindFilter));
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

async function saveCategory(a){
  const cat = (catForms[a.id]||{}).value || a.category;
  if (!cat) return;
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/assets/${a.id}/category`, {category:cat, reviewer});
    delete catForms[a.id];
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function saveIdentity(a){
  const f = idForms[a.id]||{};
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/assets/${a.id}/identity`, {
      status: f.status || a.identity_status || "unverified",
      depicts: f.depicts!=null?f.depicts:(a.depicts||""), case_connection: f.case_connection!=null?f.case_connection:(a.case_connection||""),
      identity_evidence: f.identity_evidence!=null?f.identity_evidence:(a.identity_evidence||""),
      notes: f.notes||"", reviewer});
    delete idForms[a.id];
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function saveRights(a){
  const f = rightsForms[a.id]||{};
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/assets/${a.id}/rights`, {
      status: f.status || a.rights_status || "unresolved",
      evidence: f.evidence!=null?f.evidence:(a.rights_evidence||""), notes: f.notes||"", reviewer});
    delete rightsForms[a.id];
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function caseRightsPanel(a){
  // #7/#8/#13: identity and rights are independent axes from relevance/decision, and NEVER move except
  // through this explicit save -- a keyword match or AI score never sets them by itself.
  const catForm = catForms[a.id]||{}, idForm = idForms[a.id]||{}, rForm = rightsForms[a.id]||{};
  return h("details",{}, h("summary",{},"Case connection & rights"),
    h("div",{class:"why"},
      `Category: ${a.category?(CATEGORY_LABELS[a.category]||a.category):"(not categorized)"}\n`+
      `Identity: ${IDENTITY_LABELS[a.identity_status]||a.identity_status}` +
        (a.identity_reviewer?` -- set by ${a.identity_reviewer} (${a.identity_reviewed_at})`:" -- not yet reviewed") + "\n" +
      (a.depicts?`  depicts: ${a.depicts}\n`:"") + (a.case_connection?`  case connection: ${a.case_connection}\n`:"") +
      (a.identity_evidence?`  evidence: ${a.identity_evidence}\n`:"") + (a.identity_notes?`  notes: ${a.identity_notes}\n`:"") +
      `Rights: ${RIGHTS_LABELS[a.rights_status]||a.rights_status}` +
        (a.rights_reviewer?` -- set by ${a.rights_reviewer} (${a.rights_reviewed_at})`:" -- not yet reviewed") + "\n" +
      (a.rights_evidence?`  evidence: ${a.rights_evidence}\n`:"") + (a.rights_notes?`  notes: ${a.rights_notes}\n`:"") +
      `Entered this job via: ${IMPORT_METHOD_LABELS[a.import_method]||a.import_method||"search"}` +
        (a.owner_submitted?` (your own material${a.owner_note?": "+a.owner_note:""})`:"")),
    h("div",{class:"labelform"}, h("div",{class:"meta"},"Set category"),
      h("select",{onchange:e=>{catForms[a.id]=Object.assign({},catForm,{value:e.target.value});}},
        [["","(choose)"],...Object.entries(CATEGORY_LABELS)]
          .map(([val,t])=>h("option",{value:val,selected:val===(catForm.value||a.category||"")},t))),
      h("button",{disabled:busy,onclick:()=>saveCategory(a)},"Save category")),
    h("div",{class:"labelform"}, h("div",{class:"meta"},"Set identity"),
      h("select",{onchange:e=>{idForms[a.id]=Object.assign({},idForm,{status:e.target.value});}},
        Object.entries(IDENTITY_LABELS).map(([val,t])=>h("option",{value:val,selected:val===(idForm.status||a.identity_status||"unverified")},t))),
      h("input",{type:"text",placeholder:"depicts (who/what this shows)",value:idForm.depicts!=null?idForm.depicts:(a.depicts||""),
        oninput:e=>{idForms[a.id]=Object.assign({},idForm,{depicts:e.target.value});}}),
      h("input",{type:"text",placeholder:"case connection",value:idForm.case_connection!=null?idForm.case_connection:(a.case_connection||""),
        oninput:e=>{idForms[a.id]=Object.assign({},idForm,{case_connection:e.target.value});}}),
      h("input",{type:"text",placeholder:"evidence (the source's own caption/record text)",
        value:idForm.identity_evidence!=null?idForm.identity_evidence:(a.identity_evidence||""),
        oninput:e=>{idForms[a.id]=Object.assign({},idForm,{identity_evidence:e.target.value});}}),
      h("input",{type:"text",placeholder:"notes",value:idForm.notes!=null?idForm.notes:"",
        oninput:e=>{idForms[a.id]=Object.assign({},idForm,{notes:e.target.value});}}),
      h("button",{disabled:busy,onclick:()=>saveIdentity(a)},"Save identity")),
    h("div",{class:"labelform"}, h("div",{class:"meta"},"Set rights"),
      h("select",{onchange:e=>{rightsForms[a.id]=Object.assign({},rForm,{status:e.target.value});}},
        Object.entries(RIGHTS_LABELS).map(([val,t])=>h("option",{value:val,selected:val===(rForm.status||a.rights_status||"unresolved")},t))),
      h("input",{type:"text",placeholder:"evidence",value:rForm.evidence!=null?rForm.evidence:(a.rights_evidence||""),
        oninput:e=>{rightsForms[a.id]=Object.assign({},rForm,{evidence:e.target.value});}}),
      h("input",{type:"text",placeholder:"notes",value:rForm.notes!=null?rForm.notes:"",
        oninput:e=>{rightsForms[a.id]=Object.assign({},rForm,{notes:e.target.value});}}),
      h("button",{disabled:busy,onclick:()=>saveRights(a)},"Save rights")));
}
function assetReportPanel(){
  // "Sources & rights so far" (#13): per-source photo/video/research counts, plus how much of the pool is
  // categorized/identity-checked/rights-cleared so far, and the LLM labeling cost estimate -- reusing the
  // existing usage rollup (usage_summary) rather than a second cost calculation.
  const r = assetReport;
  const bd = (obj, labelMap) => Object.entries(obj||{}).map(([k,v]) =>
      h("div",{class:"meta"}, `${(labelMap&&labelMap[k])||k}: ${v}`));
  return h("div",{class:"panel"},
    h("details",{open:assetReportOpen,ontoggle:e=>{assetReportOpen=e.target.open; if(assetReportOpen) loadAssetReport();}},
      h("summary",{},"Sources & rights so far"),
      !assetReportOpen ? null :
      !r ? h("div",{class:"sub"},"Loading…") :
      h("div",{style:"margin-top:8px;display:flex;flex-direction:column;gap:10px"},
        h("div",{},
          h("div",{class:"meta"}, h("b",{},`${r.total_assets} asset(s), ${r.total_references} research reference(s)`)),
          h("table",{style:"border-collapse:collapse;margin-top:4px;font-size:12.5px"},
            h("tr",{}, ["source","photos","videos","research"].map(t=>h("th",{style:"text-align:left;padding:2px 10px 2px 0;color:var(--mute)"},t))),
            Object.entries(r.by_source||{}).map(([src,row])=>
              h("tr",{}, [src,row.photos,row.videos,row.research].map(t=>h("td",{style:"padding:2px 10px 2px 0"},String(t))))))),
        h("div",{}, h("div",{class:"meta"}, h("b",{},"By category")), bd(r.by_category, CATEGORY_LABELS)),
        h("div",{}, h("div",{class:"meta"}, h("b",{},"By identity status")), bd(r.by_identity_status, IDENTITY_LABELS)),
        h("div",{}, h("div",{class:"meta"}, h("b",{},"By rights status")), bd(r.by_rights_status, RIGHTS_LABELS)),
        h("div",{}, h("div",{class:"meta"}, h("b",{},"Labeling cost so far")),
          h("div",{class:"meta"}, `${(r.usage||{}).calls||0} call(s), ~$${((r.usage||{}).cost_usd||0).toFixed(4)}`)))));
}
async function generateChecklist(){
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/visual-checklist/generate`, {reviewer});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function addChecklistItem(){
  if (!newChecklistItem.label.trim()){ error="A label is required (what's needed on screen)."; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/visual-checklist`, {label: newChecklistItem.label.trim(),
      group: newChecklistItem.group, linked_keyword_term: newChecklistItem.linked_keyword_term.trim(), reviewer});
    newChecklistItem = {label:"", group:"case", linked_keyword_term:""};
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function checklistPanel(){
  // #6/#12: the only way items get onto the visual checklist in the first place -- without this, the
  // pre-render coverage check (visualCoveragePanel, Gate 3) never has anything to check, since nothing
  // creates items automatically. Lives at Gate 2 because "what visual are we still missing" is naturally
  // noticed while looking at what's been found so far, but the checklist itself isn't gated to any state.
  const items = job.visual_checklist||[];
  return h("div",{class:"panel"},
    h("details",{open:checklistOpen,ontoggle:e=>{checklistOpen=e.target.open;}},
      h("summary",{}, `Visual checklist -- what this video still needs (${items.length})`),
      !checklistOpen ? null : h("div",{style:"margin-top:8px;display:flex;flex-direction:column;gap:8px"},
        h("div",{class:"sub"},"Things the video needs a real visual for -- especially case-specific ones (person, place, "+
          "document). Nothing here is ever set to fulfilled/not available automatically; the pre-render check (Gate 3) "+
          "refuses to render past anything still sitting at needed/candidates_found."),
        items.length ? h("div",{style:"display:flex;flex-direction:column;gap:4px"},
          items.map(it => h("div",{class:"meta"}, `${it.label} -- ${GROUP_LABELS[it.group]||it.group} -- ${it.status}` +
            (it.note?` (${it.note})`:"")))) : h("div",{class:"sub"},"Nothing on the checklist yet."),
        h("div",{class:"bar"},
          h("button",{disabled:busy, onclick:generateChecklist}, "Generate from approved keywords")),
        h("div",{class:"labelform"},
          h("input",{type:"text",placeholder:"what's needed on screen, e.g. a period photo of the victim",
            value:newChecklistItem.label, oninput:e=>{newChecklistItem=Object.assign({},newChecklistItem,{label:e.target.value});}}),
          h("select",{onchange:e=>{newChecklistItem=Object.assign({},newChecklistItem,{group:e.target.value});}},
            Object.entries(GROUP_LABELS).map(([val,t])=>h("option",{value:val,selected:val===newChecklistItem.group},t))),
          h("input",{type:"text",placeholder:"linked keyword (optional)", value:newChecklistItem.linked_keyword_term,
            oninput:e=>{newChecklistItem=Object.assign({},newChecklistItem,{linked_keyword_term:e.target.value});}}),
          h("button",{disabled:busy, onclick:addChecklistItem}, "Add item")))));
}
// "Find more" (requested directly): the pipeline's own search only reaches free, openly-licensed archive
// APIs (Internet Archive, LOC, Wikimedia Commons, and similar, see docs/SEARCH_PLANNING.md) -- it has no
// way to see platform videos, press photo archives, or public records, which is often exactly what a
// specific case needs. This never fetches or adds anything itself; it just opens a real search on other
// free sites, prefilled from a term you pick or type, so you're not retyping case details into six
// different search boxes by hand. Whatever you find still comes back in through "Add links" or a scene
// drop, same as always, and still goes through the normal vetting/decision flow.
const FIND_SITES = [
  {label:"Internet Archive", note:"this site's own full public search, with facets this job's automated search doesn't expose -- for a quicker look without leaving this page, use \"Search Internet Archive here\" below instead",
   url:q=>`https://archive.org/search?query=${encodeURIComponent(q)}`},
  {label:"Chronicling America", note:"the Library of Congress's own search page for this collection, broader than the automated source's query -- or use \"Search Chronicling America here\" below to stay on this page",
   url:q=>`https://chroniclingamerica.loc.gov/search/pages/results/?andtext=${encodeURIComponent(q)}`},
  {label:"Wikimedia Commons", note:"this site's own search UI, with filters this job's automated search doesn't expose -- or use \"Search Wikimedia Commons here\" below to stay on this page",
   url:q=>`https://commons.wikimedia.org/w/index.php?search=${encodeURIComponent(q)}&title=Special:MediaSearch&type=image`},
  {label:"YouTube", note:"documentaries and news retrospectives -- or use \"Search YouTube here\" below to stay on this page; either way, paste its URL into \"Add links\" or the box below rather than screen-recording, and it's auto-flagged high risk either way so you decide before it's used",
   url:q=>`https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`},
  {label:"Google Images", note:"a discovery tool, not a rights source -- use it to find where a photo actually lives, then check that source's own license before adding it",
   url:q=>`https://www.google.com/search?tbm=isch&q=${encodeURIComponent(q)}`},
  {label:"FindAGrave", note:"often has family or school photos for named individuals, sometimes with clearer permission than press photos",
   url:q=>`https://www.findagrave.com/memorial/search?q=${encodeURIComponent(q)}`},
  {label:"TikTok", note:"short clips and retrospectives, including for older cases -- same rule as YouTube: paste the URL into \"Add links\" rather than screen-recording, and it's auto-flagged high risk either way",
   url:q=>`https://www.tiktok.com/search?q=${encodeURIComponent(q)}`},
  {label:"Facebook", note:"local news pages and community/history groups sometimes carry period photos -- this almost always needs you to already be logged into Facebook in this browser to show anything",
   url:q=>`https://www.facebook.com/search/top/?q=${encodeURIComponent(q)}`},
  // Instagram deliberately not offered here -- unlike the others, it has no plain query-string search URL
  // to link to at all (its search is a logged-in, JS-driven experience), so a button would just be a dead
  // link to Instagram's own login page every time, not a shortcut to anything.
];
function findMoreSuggestions(){
  const seen = new Set(), out = [];
  const add = t => { t=(t||"").trim(); if(t && !seen.has(t.toLowerCase())){ seen.add(t.toLowerCase()); out.push(t); } };
  add(job.subject);
  (job.keywords||[]).filter(k=>k.approved).forEach(k=>{ add(k.term); add(k.entity); (k.aliases||[]).forEach(add); });
  (job.visual_checklist||[]).forEach(it=>add(it.label));
  return out;
}
function findMoreQuery(){ return findQuery===null ? (job.subject||"") : findQuery; }
// Sources with a free, no-key API that are ALREADY automated sources elsewhere in the pipeline
// (pipeline/sources/groups.py's ARCHIVE_SOURCES) -- unlike YouTube (below), searching these on demand here
// just exposes search() that already exists, rather than adding a new capability. Matches the backend's
// INLINE_SEARCH_SOURCES (pipeline/api/app.py) name-for-name; Google Images/FindAGrave/TikTok/Facebook have
// no free API (the latter two also carry the platform-download risk PLATFORM_SOURCE flags) and stay plain
// link-outs in FIND_SITES above, with the quick paste-back box below for whatever you find there.
const SEARCHABLE_SOURCES = [
  {name:"archive", label:"Internet Archive"},
  {name:"chronicling_america", label:"Chronicling America"},
  {name:"commons", label:"Wikimedia Commons"},
];
function srcState(name){
  return srcSearch[name] || (srcSearch[name] = {results:null, loading:false, error:"", lastQuery:"", count:5});
}
async function searchSource(name){
  const st = srcState(name);
  if (job.state !== "assets_review"){
    st.error = `Search becomes available once this job reaches asset review (it's currently "${job.state}").`;
    render(); return;
  }
  const q = findMoreQuery().trim();
  if (!q){ st.error="Type or pick a search term above first."; render(); return; }
  st.loading=true; st.error=""; st.results=null; st.lastQuery=q; render();
  try{
    st.results = await api("POST", `/jobs/${JOB}/source-search`, {source:name, query:q, count:st.count});
  }catch(e){ st.error=String(e.message||e); }
  st.loading=false; render();
}
async function addSourceCandidate(name, label, c){
  // Downloads just this one item and adds it pending (pipeline/api/app.py's assets_add_candidate) --
  // unlike a YouTube pick, these keep the license/author/attribution the search already found rather than
  // re-deriving them from a bare URL, and aren't auto-flagged PLATFORM_SOURCE, but still go through the
  // normal vetting/relevance scoring and review below like anything else.
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before adding an item -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    const st = srcState(name);
    await api("POST", `/jobs/${JOB}/assets/add-candidate`, {source:name, query: st.lastQuery, candidate:c, reviewer});
    if (st.results) st.results = st.results.filter(x=>x.id!==c.id);
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function sourceResultCard(name, label, c){
  const thumb = c.kind==="image" ? c.url : "";
  return h("div",{class:"card", style:"max-width:240px"},
    thumb ? h("div",{class:"media"}, h("img",{src:thumb,loading:"lazy",alt:c.title||""})) : null,
    h("div",{class:"body"},
      h("div",{class:"title"}, c.title||"(untitled)"),
      h("div",{class:"meta"}, c.author||"unknown author"),
      h("div",{class:"meta"}, c.license || "no license info in the record -- check the item page before use"),
      c.description ? h("div",{class:"meta"}, c.description.slice(0,160)) : null,
      c.page_url ? h("div",{class:"meta"}, h("a",{href:c.page_url,target:"_blank",rel:"noopener"},"open item page")) : null),
    h("div",{class:"acts"},
      h("button",{disabled:busy, onclick:()=>addSourceCandidate(name,label,c)}, "Add this one")));
}
async function searchYoutube(){
  // The one site in FIND_SITES that's actually automatable (requested directly): yt-dlp's own search
  // syntax, no paid API key. Metadata only -- nothing downloads until a specific result is added below.
  // The backend only allows this during assets_review (same guard as "Add links"); findMorePanel() disables
  // the button outside that state, but this is a second check in case something still reaches this function.
  if (job.state !== "assets_review"){
    ytError = `Search becomes available once this job reaches asset review (it's currently "${job.state}").`;
    render(); return;
  }
  const q = findMoreQuery().trim();
  if (!q){ ytError="Type or pick a search term above first."; render(); return; }
  ytLoading=true; ytError=""; ytResults=null; ytLastQuery=q; render();
  try{
    ytResults = await api("POST", `/jobs/${JOB}/youtube-search`, {query:q, count:ytCount});
  }catch(e){ ytError=String(e.message||e); }
  ytLoading=false; render();
}
async function addYoutubeCandidate(c){
  // Reuses the exact same /assets/add-url endpoint "Add links" below already calls -- a YouTube search
  // result is not treated any differently from a link you found and pasted yourself: still downloaded
  // synchronously, still lands pending, still auto-flagged high risk (PLATFORM_SOURCE) and needs a note.
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before adding a video -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    const note = `Found via Gate 2's YouTube search for "${ytLastQuery}"` + (c.uploader?` (uploader: ${c.uploader})`:"") + ".";
    await api("POST", `/jobs/${JOB}/assets/add-url`, {url:c.url, note, reviewer});
    if (ytResults) ytResults = ytResults.filter(x=>x.id!==c.id);
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function ytResultCard(c){
  return h("div",{class:"card", style:"max-width:240px"},
    c.thumbnail ? h("div",{class:"media"}, h("img",{src:c.thumbnail,loading:"lazy",alt:c.title||""})) : null,
    h("div",{class:"body"},
      h("div",{class:"title"}, c.title||"(untitled)"),
      h("div",{class:"meta"}, (c.uploader||"unknown uploader") + (c.duration?` · ${Math.round(c.duration)}s`:"")),
      c.description ? h("div",{class:"meta"}, c.description.slice(0,160)) : null,
      h("div",{class:"meta"}, h("a",{href:c.url,target:"_blank",rel:"noopener"},"open on YouTube"))),
    h("div",{class:"acts"},
      h("button",{disabled:busy, onclick:()=>addYoutubeCandidate(c)}, "Add this one")));
}
async function findQuickAdd(){
  // One-click paste-back for whatever you found on one of the plain link-out buttons above (Google
  // Images/FindAGrave/TikTok/Facebook, or any of the others) -- the exact same /assets/add-url "Add
  // links" below already calls, just reachable right here instead of scrolling down to paste it there.
  const url = findPasteUrl.trim();
  if (!url){ findPasteError="Paste a URL first."; render(); return; }
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before adding a link -- add the note, or un-pick them.`; render(); return; }
  findPasteBusy=true; findPasteError=""; findPasteOk=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    await api("POST", `/jobs/${JOB}/assets/add-url`, {url, note:"Found via Gate 2's \"Find more\" panel.", reviewer});
    findPasteUrl=""; findPasteOk="Added -- pending review below."; findPasteBusy=false; await load();
  }catch(e){ findPasteBusy=false; findPasteError=String(e.message||e); render(); }
}
function inlineSearchBlock(ready, loading, error, results, lastQuery, count, onSearch, onCount, label, resultCard, unit){
  // Shared render shape for YouTube's search block and each of SEARCHABLE_SOURCES' below -- same
  // button/select/status/grid layout, just parameterized so the YouTube block (already shipped, already
  // tested) didn't need touching to add the other three.
  return h("div",{},
    h("div",{class:"bar",style:"margin-top:10px"},
      h("button",{class:"primary",disabled:busy||loading||!ready,
        title: ready?"":`Only available during asset review -- this job is currently "${job.state}"`,
        onclick:onSearch}, loading?`Searching ${label}...`:`Search ${label} here (metadata only, nothing downloads yet)`),
      h("select",{onchange:e=>onCount(Number(e.target.value))},
        [3,5,10].map(n=>h("option",{value:n,selected:n===count},`${n} results`)))),
    !ready ? h("div",{class:"sub",style:"margin-top:2px"},
      `Becomes available once this job reaches asset review -- it's currently "${job.state}".`) : null,
    error?h("div",{class:"err"}, error):null,
    results ? (results.length
      ? h("div",{},
          h("div",{class:"sub",style:"margin-top:6px"},`${results.length} result(s) for "${lastQuery}" -- nothing downloaded yet; `+
            `"Add this one" downloads just that ${unit} and adds it pending${label==="YouTube"?" (still high-risk, still needs a note)":""}. `+
            `It'll show up in the "Added by hand" panel below (scroll down), not necessarily here in this list.`),
          h("div",{class:"grid"}, results.map(resultCard)))
      : h("div",{class:"sub",style:"margin-top:6px"},`No ${label} results for "${lastQuery}".`)) : null);
}
function findMorePanel(){
  const suggestions = findMoreSuggestions();
  const ytReady = job.state === "assets_review";
  return h("div",{class:"panel"},
    h("b",{},"Find more -- search outside the automated sources"),
    h("div",{class:"sub"},"The pipeline's own search only reaches free, openly-licensed archives -- it can't see platform "+
      "videos, press photo archives, or public records, which is often exactly what a specific case needs. Internet "+
      "Archive, Chronicling America, Wikimedia Commons and YouTube can be searched right here, metadata only, nothing "+
      "downloaded until you pick a result; the rest open a real search on their own site instead. Whatever you bring "+
      "back still goes through the normal vetting/decision flow below, same as always."),
    h("div",{class:"bar"},
      h("input",{type:"text",value:findMoreQuery(),size:44,placeholder:"what to search for",
        oninput:e=>{findQuery=e.target.value;}})),
    suggestions.length ? h("div",{class:"bar",style:"flex-wrap:wrap;margin-top:2px"},
      suggestions.map(s=>h("button",{class:"chip",style:"cursor:pointer",onclick:()=>{findQuery=s;}}, s))) : null,
    h("div",{class:"bar",style:"flex-wrap:wrap;margin-top:2px"},
      FIND_SITES.map(site=>h("button",{title:site.note,
        onclick:()=>{window.open(site.url(findMoreQuery()), "_blank", "noopener");}},
        site.label))),
    h("div",{class:"bar",style:"margin-top:6px"},
      h("input",{type:"text",value:findPasteUrl,size:44,placeholder:"found something on one of those? paste its link here",
        oninput:e=>{findPasteUrl=e.target.value;}}),
      h("button",{disabled:busy||findPasteBusy||job.state!=="assets_review",
        title: job.state==="assets_review"?"":`Only available during asset review -- this job is currently "${job.state}"`,
        onclick:findQuickAdd}, findPasteBusy?"Adding...":"Add this link")),
    findPasteError?h("div",{class:"err"}, findPasteError):null,
    findPasteOk?h("div",{class:"sub"}, findPasteOk):null,
    inlineSearchBlock(ytReady, ytLoading, ytError, ytResults, ytLastQuery, ytCount,
      searchYoutube, n=>{ytCount=n;}, "YouTube", ytResultCard, "video"),
    SEARCHABLE_SOURCES.map(({name,label})=>{
      const st = srcState(name);
      return inlineSearchBlock(job.state==="assets_review", st.loading, st.error, st.results, st.lastQuery, st.count,
        ()=>searchSource(name), n=>{st.count=n;}, label, c=>sourceResultCard(name,label,c), "item");
    }));
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
  // A pending asset can be null-relevance for two different reasons -- no keywords were ever approved to
  // score against (rare, legacy), or scoring was deliberately deferred for this round (defer_relevance,
  // requested directly: "pull stock photos first, score relevance later"). scoring_method=="" alongside no
  // relevance_why is the deferred case in practice (a real "no keywords" job still runs the keyword-match
  // fallback and sets relevance_why); say so explicitly rather than the generic, easy-to-misread fallback.
  const deferredLooking = score(a)==null && a.status==="pending" && !v.scoring_method && !v.relevance_why;
  const relevanceWhy = deferredLooking
    ? "(not yet scored -- relevance scoring was deferred for this round; use \"Score relevance now\" below the grid when you're ready)"
    : (v.relevance_why || "(no keyword to score against)");
  const why = h("details",{open: whyOpen[a.id]!==false, ontoggle:e=>{whyOpen[a.id]=e.target.open;}},
    h("summary",{},"Why this score and risk"),
    h("div",{class:"why"},
      `Relevance score: ${pct(score(a))}  (threshold at scoring time: ${pct(v.relevance_threshold)}, machine decision: ${v.relevance_decision||"n/a"})\n` +
      `${scoredBy}\n${relevanceWhy}\n` + contribLines +
      `${v.contribution_note||""}\n\nSee docs/SCORING.md for how each method works.`),
    def?h("details",{}, h("summary",{},`What does ${v.method_version} do?`), h("div",{class:"why"}, defText)):null,
    h("div",{class:"why"}, `${v.summary||""}\nRisk-rules version: ${v.method||"?"}\nFound by search: "${a.query||""}"`),
    v.niche_evaluation?h("div",{class:"why"},
      `Niche evaluation (${v.niche_evaluation.niche_evaluated}): aesthetic fit ${v.niche_evaluation.aesthetic_fit}, `+
      `suggested action "${v.niche_evaluation.action}" -- this is a SUGGESTION only, it never changes your `+
      `decision below.\n${v.niche_evaluation.reasoning}`):null,
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
        score(a)==null && a.status==="pending"
          ? h("span",{class:"b med"},"not yet scored")
          : h("span",{class:"b score"}, "score "+pct(score(a))),
        h("span",{class:"b "+({high:"hi",medium:"med",low:"lo"}[r])}, "risk "+r),
        h("span",{class:"b"}, a.source), h("span",{class:"b"}, a.kind),
        below(a)?h("span",{class:"b"},"below threshold"):null,
        v.usable===false?h("span",{class:"b hi"},"can't be used (too small)"):null,
        v.niche_evaluation?h("span",{class:"b"+(v.niche_evaluation.aesthetic_fit==="Jarring"?" med":v.niche_evaluation.aesthetic_fit==="Excellent"?" lo":"")},
          "aesthetic: "+v.niche_evaluation.aesthetic_fit):null,
        a.category?h("span",{class:"b"}, CATEGORY_LABELS[a.category]||a.category):h("span",{class:"b"},"not categorized"),
        h("span",{class:"b"+(a.identity_status==="verified"?" lo":a.identity_status==="disputed"?" hi":"")},
          "identity: "+(IDENTITY_LABELS[a.identity_status]||a.identity_status)),
        h("span",{class:"b"+(["public_domain","cc0","open_license"].includes(a.rights_status)?" lo":a.rights_status==="unresolved"?" med":"")},
          "rights: "+(RIGHTS_LABELS[a.rights_status]||a.rights_status)),
        lbl==="duplicate"?h("span",{class:"b med"},"marked duplicate"):null),
      h("div",{class:"title"}, a.title||a.id),
      h("div",{class:"meta"}, "license: "+(a.license||"(none found)")+" · by: "+(a.author||"(unknown)")),
      h("div",{class:"meta"}, a.page_url||a.source_url ? h("a",{href:a.page_url||a.source_url,target:"_blank",rel:"noopener"},"open original page") : "no source page recorded",
         a.width?` · ${a.width}×${a.height}`:"", a.duration?` · ${Math.round(a.duration)}s`:""),
      a.description?h("div",{class:"meta"}, a.description.slice(0,260)):null,
      why,
      caseRightsPanel(a),
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
function stockDeferBody(){
  // Shared by nextBatch/searchAgain -- stockLimitDraft/deferRelevanceDraft (requested directly: "stop go
  // limits depending how much we've pulled already" / "pull stock photos first, score relevance later")
  // mirror batchSize's own pattern: blank stock_limit means "don't touch it" (reject_assets only ever
  // changes a field it's actually given), so leaving the box empty never silently resets an existing limit.
  const out = {};
  if (stockLimitDraft!=="" && stockLimitDraft!=null) out.stock_limit = Math.max(0, Number(stockLimitDraft)||0);
  out.defer_relevance = !!deferRelevanceDraft;
  return out;
}
async function searchAgain(){
  const fb = document.getElementById("fb").value, xq = document.getElementById("xq").value;
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before searching again -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    await api("POST",`/jobs/${JOB}/assets/reject`,Object.assign({feedback:fb,
        extra_queries:xq.split(",").map(s=>s.trim()).filter(Boolean), reviewer}, stockDeferBody()));
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
let urlAddStatus = [];   // {url, ok, message} for the most recent "Add links" batch -- one per pasted line
function parseUrlLines(text){
  // Same "url | note | position" format scripts/poc.py's --urls file uses. Blank lines and # comments skipped.
  return text.split("\n").map(l=>l.trim()).filter(l=>l && !l.startsWith("#")).map(line=>{
    const parts = line.split("|").map(s=>s.trim());
    return {line, url: parts[0], note: parts[1]||"", position: parts[2]||""};
  });
}
async function addUrls(){
  // Gate 2's "Add links" box: paste one or more URLs (like RankReel's link import). Each line is downloaded
  // one at a time via /assets/add-url (#9) -- the same synchronous yt-dlp/direct pattern Gate 3's "drop a
  // link on a scene" uses -- so every link gets its own immediate success/failure result as it happens,
  // instead of being queued for a later sourcing round with no feedback at all.
  const ta = document.getElementById("urlsbox");
  const entries = parseUrlLines(ta.value);
  if (!entries.length){ error="Paste at least one URL first."; render(); return; }
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before adding links -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; urlAddStatus=[]; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    for (const e of entries){
      try{
        await api("POST", `/jobs/${JOB}/assets/add-url`, {url:e.url, note:e.note, position:e.position, reviewer});
        urlAddStatus.push({url:e.url, line:e.line, ok:true, message:"added -- pending review below"});
      }catch(err){
        urlAddStatus.push({url:e.url, line:e.line, ok:false, message:String(err.message||err)});
      }
      render();   // one line at a time, so progress shows up as each link finishes rather than all at once
    }
    const failed = urlAddStatus.filter(s=>!s.ok).map(s=>s.line);
    ta.value = failed.join("\n");   // keep only the failed lines, so a fix-and-retry doesn't re-add the rest
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
    await api("POST",`/jobs/${JOB}/assets/reject`,Object.assign({max_queries: Math.max(1, Number(batchSize)||10), reviewer}, stockDeferBody()));
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function suggestKeywords(){
  // Asks the SAME LLM keyword-writing stage the auto-approved keyword step ("Gate 1½") used for a fresh batch of group-tagged phrases, mid
  // asset review -- an alternative to hand-typing terms into "Search again" (those have no group of their
  // own, so routing sends them to every configured source regardless of fit; these come back with a real
  // research/case/historical/stock classification and get routed correctly once approved below).
  suggestBusy=true; suggestError=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    const resp = await api("POST", `/jobs/${JOB}/keywords/suggest`, {feedback: suggestFeedback, reviewer});
    job = resp;   // already the full job JSON (kw_suggest's response), no separate load() needed
    suggestIds = resp.suggested_ids || [];
    suggestChecked = {}; suggestIds.forEach(id => { suggestChecked[id] = true; });
    suggestBusy=false; render();
  }catch(e){ suggestBusy=false; suggestError=String(e.message||e); render(); }
}
function dismissSuggestions(){
  // No API call needed -- an unapproved suggestion left sitting in job.keywords is already inert (never
  // searched, never shown as its own section in the grid below), same as a Gate-1 keyword rejected by
  // omission. This just clears the reviewer's own local checklist of them.
  suggestIds = null; suggestChecked = {}; suggestFeedback = ""; render();
}
async function addSuggested(){
  const ids = (suggestIds||[]).filter(id => suggestChecked[id]);
  if (!ids.length){ suggestError = "Check at least one suggestion first, or Dismiss to clear them."; render(); return; }
  suggestBusy=true; suggestError=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/keywords/approve-suggested`, {keyword_ids: ids, reviewer});
    suggestIds = null; suggestChecked = {}; suggestFeedback = "";
    suggestBusy=false; await load();   // new approved keyword(s) now show up as their own (empty, "not searched
                                        // yet") section below -- "Next batch" is what actually searches them.
  }catch(e){ suggestBusy=false; suggestError=String(e.message||e); render(); }
}
async function scoreRelevanceNow(){
  // "Score relevance now" (requested directly) -- scores whatever's pending against the real approved
  // keywords right now, regardless of defer_relevance; the only thing that ever scores a deferred round.
  scoreRelevanceBusy=true; scoreRelevanceError=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await api("POST",`/jobs/${JOB}/assets/score-relevance`,{reviewer});
    scoreRelevanceBusy=false; await load();
  }catch(e){ scoreRelevanceBusy=false; scoreRelevanceError=String(e.message||e); render(); }
}
async function loadAssetReport(){
  // Per-job/per-source summary (#13) -- photo/video/research counts, category/identity/rights breakdowns,
  // and the labeling cost estimate (reusing the existing usage rollup, not a separate cost calculation).
  // Fetched lazily, same pattern as the activity log and the own-footage folder listing above.
  try{ assetReport = await api("GET", `/jobs/${JOB}/asset-report`); }
  catch(e){ error=String(e.message||e); }
  render();
}
async function loadFolderFiles(){
  // What's currently sitting in the server's own-footage folder (#10) -- read-only, imports nothing by
  // itself. Fetched lazily (only once the panel is opened), same pattern as the activity log below.
  try{ folderFiles = await api("GET", `/jobs/${JOB}/folder-files`); }
  catch(e){ folderFiles = {folder:"", files:[]}; error=String(e.message||e); }
  render();
}
function toggleFolderFile(path, checked){
  if (checked) folderSelection[path] = folderSelection[path] || "";
  else delete folderSelection[path];
  render();
}
async function addFolderFiles(){
  // Picking a file here IS the explicit "this is my own material" action (#10) -- nothing in the server's
  // folder is ever added to a job without this. Queued via the same /assets/reject path Next batch/Search
  // again use (not downloaded synchronously like Add links' /assets/add-url): these are already local files
  // with no network failure mode, so there's nothing that needs immediate per-file feedback.
  const paths = Object.keys(folderSelection);
  if (!paths.length){ error="Pick at least one file first."; render(); return; }
  const missing = missingHighRiskNotes();
  if (missing){ error = `${missing} high-risk Use pick(s) need a note before adding footage -- add the note, or un-pick them.`; render(); return; }
  busy=true; error=""; render();
  try{
    const reviewer = (store.get("reviewer")||"").trim();
    await persistDecisions(reviewer);
    const folder_files = paths.map(p => ({path:p, note: folderSelection[p]||""}));
    await api("POST", `/jobs/${JOB}/assets/reject`, {folder_files, reviewer});
    folderSelection = {};
    folderFiles = null;      // refetched next time the panel opens, so it reflects the new "already selected" state
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

/* ---------------------------------------------------------------- gate 1: script review */
function scriptWordsLineFor(text){
  const words = (text||"").trim().split(/\s+/).filter(Boolean).length;
  return `${words} word(s), about ${Math.round(words/2.6)}s spoken`;
}
function updateScriptDraftWords(){
  // Same idea as updateScriptPreview() below for Gate 3 -- patches the word count in place on every
  // keystroke instead of calling render(), so the textarea is never rebuilt and the cursor never jumps.
  const el = document.getElementById("scriptDraftWords"); if (el) el.textContent = scriptWordsLineFor(scriptDraft);
}
function scriptReviewBody(){
  // Gate 1 (docs/PIPELINE_STAGES.md): the freshly-written narration, before anything else has run --
  // no keywords, no sourcing, no scenes with clips yet. Approving with an edited draft re-splits it into
  // fresh scenes (Orchestrator.approve_script) -- there's nothing scene-specific to lose at this point.
  // "Ask for a rewrite instead" sends written feedback back to the writer and starts over from a blank
  // script (Orchestrator.reject_script clears job.script/job.scenes and loops back to SCRIPT_RUNNING).
  return h("div",{},
    h("div",{class:"panel"},
      h("b",{},"Script"),
      h("div",{class:"sub"},"Edit the narration below if you want to change anything, then approve -- this becomes the script "+
        "everything downstream (keywords, sourcing, scenes) works from."),
      h("div",{id:"scriptDraftWords",class:"meta",style:"margin:4px 0"}, scriptWordsLineFor(scriptDraft)),
      h("textarea",{class:"note",style:"min-height:280px;width:100%;font-family:inherit",
          oninput:e=>{scriptDraft=e.target.value; updateScriptDraftWords();}}, scriptDraft)),
    h("div",{class:"panel"},
      h("div",{class:"bar"},
        h("button",{class:"primary",disabled:busy,onclick:approveScript},"Approve script")),
      h("div",{class:"bar",style:"margin-top:10px"},
        h("input",{type:"text",id:"scriptfb",placeholder:"what should change? (e.g. wrong facts, wrong tone, too long/short)",size:50}),
        h("button",{disabled:busy,onclick:rejectScript},"Ask for a rewrite instead"))));
}
async function approveScript(){
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await api("POST",`/jobs/${JOB}/script/approve`,{reviewer, edited_script: scriptDraft||""});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function rejectScript(){
  const fb = document.getElementById("scriptfb").value;
  if (!fb.trim()){ error="Say what should change before asking for a rewrite."; render(); return; }
  busy=true; error=""; render();
  try{
    await api("POST",`/jobs/${JOB}/script/reject`,{feedback:fb, reviewer:(store.get("reviewer")||"").trim()});
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
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
  // #12: never silently substitute -- if the coverage check found unresolved visual_checklist items, this
  // needs an explicit override note before it will even try (the server enforces the same thing regardless;
  // this just avoids a round trip for the obvious case and makes clear the note is what's missing).
  if (coverage && !coverage.ready && !coverageOverrideNote.trim()){
    const n = coverage.unresolved.length + (coverage.category_mismatches||[]).length;
    error = `${n} visual checklist item(s) aren't resolved yet -- resolve them below, or explain in the override note why it's OK to render without them.`;
    render();
    return;
  }
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await saveSceneEdits(reviewer);                  // whatever you typed gets saved before it renders, not lost
    const payload = {reviewer};
    if (coverage && !coverage.ready) payload.override_note = coverageOverrideNote.trim();
    await api("POST",`/jobs/${JOB}/scenes/approve`,payload);
    coverageOverrideNote = "";
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function resolveChecklistItem(itemId, status, extra){
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await api("PATCH", `/jobs/${JOB}/visual-checklist/${itemId}`, Object.assign({status, reviewer}, extra||{}));
    delete checklistForms[itemId];
    busy=false;
    await loadCoverage();     // re-checks coverage right away, not just on the next poll
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function visualCoveragePanel(){
  // #12: the pre-render check itself -- what's still unresolved on the visual checklist, and the concrete
  // things a reviewer can do about each one (VISUAL_COVERAGE_REMEDIATION_OPTIONS in the orchestrator). Only
  // shown once a report has actually loaded; has_checklist=false (the job never used this feature) or
  // ready=true (everything's resolved) both mean there's nothing to show here.
  if (!coverage || !coverage.has_checklist || coverage.ready) return null;
  const approved = job.assets.filter(a=>a.status==="approved");
  const mismatches = coverage.category_mismatches||[];
  const sceneNumberOf = id => { const i = job.scenes.findIndex(s=>s.id===id); return i<0 ? "?" : i+1; };
  const itemRow = (item, mismatchNote) => {
    const form = checklistForms[item.id]||{};
    return h("div",{style:"margin-top:10px;padding-top:10px;border-top:1px solid var(--line)"},
      h("div",{}, h("b",{},item.label), " ", h("span",{class:"b med"}, item.status||"fulfilled"),
        item.linked_scene_ids.length ? h("span",{class:"meta"}, " -- referenced by scene(s) "+
          item.linked_scene_ids.map(sceneNumberOf).join(", ")) : null),
      mismatchNote ? h("div",{class:"meta"}, mismatchNote) : null,
      h("div",{class:"labelform"},
        h("select",{onchange:e=>{checklistForms[item.id]=Object.assign({},form,{assetId:e.target.value});}},
          [["","(pick an approved asset)"], ...approved.map(a=>[a.id,(a.title||a.id).slice(0,50)])]
            .map(([val,t])=>h("option",{value:val,selected:val===(form.assetId||item.asset_id||"")},t))),
        h("button",{disabled:busy || !(form.assetId||item.asset_id), onclick:()=>resolveChecklistItem(item.id,"fulfilled",{asset_id: form.assetId||item.asset_id})}, mismatchNote?"Re-pick fulfilling asset":"Mark fulfilled")),
      h("div",{class:"labelform"},
        h("input",{type:"text",placeholder:"why isn't there one? (required)",value:form.note||"",
          oninput:e=>{checklistForms[item.id]=Object.assign({},form,{note:e.target.value});}}),
        h("button",{disabled:busy || !(form.note||"").trim(), onclick:()=>resolveChecklistItem(item.id,"not_available",{note:(form.note||"").trim()})}, "Mark not available"),
        h("button",{disabled:busy || !(form.note||"").trim(), onclick:()=>resolveChecklistItem(item.id,"skipped",{note:(form.note||"").trim()})}, "Mark skipped")),
      h("div",{class:"meta"},"Need more first? Drag a clip from the strip below onto a scene, or drop a link/file "+
        "straight onto one -- no need to leave this page."));
  };
  return h("div",{class:"panel", style:"border-color:var(--med)"},
    h("b",{},`Visual coverage: ${coverage.unresolved.length + mismatches.length} need(s) not resolved yet`),
    h("div",{class:"sub"},"These came from the visual checklist (approved keywords from \"Gate 1½\", or added by hand). "+
      "Nothing renders past them silently -- either resolve each one below, or approve with an explicit override "+
      "note explaining why it's OK to go ahead without them."),
    coverage.unresolved.map(item => itemRow(item, null)),
    mismatches.length ? h("div",{style:"margin-top:10px;padding-top:10px;border-top:2px solid var(--hi)"},
      h("div",{class:"meta"}, h("b",{},"Marked fulfilled, but not with case material")),
      h("div",{class:"sub"},"These are \"fulfilled\" on the checklist, but the asset attached to them isn't categorized "+
        "verified_case or unverified_case_candidate -- exactly the silent substitution this check exists to catch. "+
        "Re-pick a real case asset, or explicitly accept the stand-in with not available / skipped.")) : null,
    mismatches.map(item => itemRow(item, `Currently fulfilled with a ${item.asset_category||"uncategorized"} asset, not case material.`)),
    h("div",{style:"margin-top:12px;padding-top:12px;border-top:1px solid var(--line)"},
      h("div",{class:"meta"},"Or approve anyway, with a reason (recorded in the decision log alongside exactly which items were left unresolved):"),
      h("textarea",{class:"note",placeholder:"why is it OK to render without these? (required to proceed)",
        oninput:e=>{coverageOverrideNote=e.target.value;}}, coverageOverrideNote)));
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
// -------- Gate 3 manual crop (MoneyPrinterTurbo always auto-center-crops to the render's aspect ratio,
// with no way to choose what stays in frame -- see pipeline/stages/render/crop.py's module docstring).
// Same crop_box() math as that module, reimplemented here (there's no way to share Python with browser
// JS) so the preview box matches exactly what the render will actually do.
function aspectRatioOf(aspect){
  const parts = String(aspect||"").split(":");
  const w = Number(parts[0]), h = Number(parts[1]);
  return (w>0 && h>0) ? w/h : 9/16;
}
function cropBox(sw, sh, targetAspect, cx, cy, zoom){
  zoom = Math.max(zoom, 1.0);
  let baseW, baseH;
  if (sw/sh > targetAspect){ baseH = sh; baseW = sh*targetAspect; }
  else { baseW = sw; baseH = sw/targetAspect; }
  const w = Math.max(1, Math.min(sw, Math.round(baseW/zoom)));
  const h = Math.max(1, Math.min(sh, Math.round(baseH/zoom)));
  let x = Math.round(cx*sw - w/2), y = Math.round(cy*sh - h/2);
  x = Math.max(0, Math.min(sw-w, x));
  y = Math.max(0, Math.min(sh-h, y));
  return {x,y,w,h};
}
function assetForScene(s){ return job.assets.find(a=>a.id===s.asset_id) || null; }
function cropDraftFor(s){
  if (!cropDraft[s.id]) cropDraft[s.id] = s.crop
    ? {center_x:s.crop.center_x, center_y:s.crop.center_y, zoom:s.crop.zoom}
    : {center_x:0.5, center_y:0.5, zoom:1.0};
  return cropDraft[s.id];
}
async function saveCrop(sceneId){
  const d = cropDraft[sceneId]; if (!d) return;
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await api("PATCH", `/jobs/${JOB}/scenes/${sceneId}/crop`, Object.assign({reviewer}, d));
    delete cropDraft[sceneId];
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
async function resetCrop(sceneId){
  busy=true; error=""; render();
  try{
    const reviewer=(store.get("reviewer")||"").trim();
    await api("POST", `/jobs/${JOB}/scenes/${sceneId}/crop/remove`, {reviewer});
    delete cropDraft[sceneId];
    busy=false; await load();
  }catch(e){ busy=false; error=String(e.message||e); render(); }
}
function startCropDrag(e, sceneId, asset, targetAspect, boxW, d0){
  e.preventDefault();
  const mediaEl = e.currentTarget.querySelector("img,video");
  const startX = e.clientX, startY = e.clientY;
  const box0 = cropBox(asset.width, asset.height, targetAspect, d0.center_x, d0.center_y, d0.zoom);
  const scale = boxW / box0.w;
  function onMove(ev){
    const dxSrc = (ev.clientX-startX)/scale, dySrc = (ev.clientY-startY)/scale;
    let cx = Math.max(0, Math.min(1, d0.center_x - dxSrc/asset.width));
    let cy = Math.max(0, Math.min(1, d0.center_y - dySrc/asset.height));
    cropDraft[sceneId] = Object.assign({}, d0, {center_x:cx, center_y:cy});
    if (mediaEl){
      const box = cropBox(asset.width, asset.height, targetAspect, cx, cy, d0.zoom);
      mediaEl.style.left = (-box.x*scale)+"px"; mediaEl.style.top = (-box.y*scale)+"px";
    }
  }
  function onUp(){ window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); render(); }
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}
function cropEditor(s, asset){
  const d = cropDraftFor(s);
  const targetAspect = aspectRatioOf(renderSettings.aspect);
  const box = cropBox(asset.width, asset.height, targetAspect, d.center_x, d.center_y, d.zoom);
  const boxW = 160, boxH = Math.round(boxW/targetAspect), scale = boxW/box.w;
  const mediaStyle = `position:absolute;left:${-box.x*scale}px;top:${-box.y*scale}px;`+
      `width:${asset.width*scale}px;height:${asset.height*scale}px;pointer-events:none`;
  const mediaEl = asset.kind==="video"
    ? h("video",{src:`/jobs/${JOB}/assets/${asset.id}/file`,muted:true,style:mediaStyle})
    : h("img",{src:`/jobs/${JOB}/assets/${asset.id}/file`,style:mediaStyle});
  return h("div",{style:"margin-top:8px;display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap"},
    h("div",{style:`position:relative;width:${boxW}px;height:${boxH}px;overflow:hidden;border:2px solid var(--acc);`+
        "border-radius:6px;cursor:grab;background:#000",
        onmousedown:e=>startCropDrag(e, s.id, asset, targetAspect, boxW, d)}, mediaEl),
    h("div",{style:"display:flex;flex-direction:column;gap:6px;min-width:180px"},
      h("div",{class:"meta"},"Drag the preview to reposition it; zoom tightens the crop."),
      h("label",{}, "Zoom ", h("input",{type:"range",min:100,max:400,step:5,value:Math.round(d.zoom*100),
          oninput:e=>{cropDraft[s.id]=Object.assign({},d,{zoom:Number(e.target.value)/100}); render();}}),
        h("b",{}, d.zoom.toFixed(2)+"x")),
      h("div",{class:"bar"},
        h("button",{disabled:busy, onclick:()=>saveCrop(s.id)}, "Save crop"),
        s.crop ? h("button",{disabled:busy, onclick:()=>resetCrop(s.id)}, "Reset to auto-crop") : null)));
}
function cropWidget(s){
  if (!s.clip_path) return null;
  const pendingClipChange = sceneClipOverride[s.id]!=null && sceneClipOverride[s.id] !== (s.clip_path||"");
  const asset = assetForScene(s);
  const open = !!cropOpen[s.id];
  return h("div",{style:"margin-top:6px"},
    h("details",{open, ontoggle:e=>{cropOpen[s.id]=e.target.open; render();}},
      h("summary",{}, s.crop ? "Crop: adjusted" : "Crop: automatic (MoneyPrinterTurbo's own center-crop)"),
      !open ? null :
      pendingClipChange ? h("div",{class:"sub",style:"margin-top:6px"},"Save your clip change above first, then you can crop it.") :
      (!asset || !asset.width || !asset.height) ? h("div",{class:"sub",style:"margin-top:6px"},
          "Can't crop this clip -- its width/height aren't known, so it'll use MoneyPrinterTurbo's own automatic center-crop.") :
      cropEditor(s, asset)));
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
    cropWidget(s),
    h("div",{class:"meta",style:"margin-top:6px"}, "Narration (spoken)"),
    h("textarea",{class:"note",style:"min-height:80px",
        oninput:e=>{sceneNarration[s.id]=e.target.value; updateScriptPreview();}}, sceneText(s)),
    h("div",{class:"meta",style:"margin-top:6px"}, "Your note (private -- never spoken, never sent to the renderer)"),
    h("textarea",{class:"note",style:"min-height:44px",placeholder:"e.g. reconsider this clip, double-check this date, come back to this later…",
        oninput:e=>{sceneNotes[s.id]=e.target.value;}}, sceneNotes[s.id]!=null?sceneNotes[s.id]:(s.note||"")));
}

function suggestKeywordsPanel(){
  // Gate 2's "Suggest more search terms" (requested directly: an alternative to typing terms into "Search
  // again" by hand or running a separate outside prompt -- reuses the SAME LLM keyword-writing stage Gate
  // 1 uses, so a suggestion carries a real group, correctly routed once approved, unlike "Search again"'s
  // ungrouped extra_queries). Only offered for jobs using the "llm" keyword provider (job.providers.keywords)
  // -- "manual" has no model behind it to ask.
  const isLlm = (job.providers && job.providers.keywords) === "llm";
  const reviewing = (suggestIds||[]).map(id => (job.keywords||[]).find(k => k.id === id)).filter(Boolean);
  return h("div",{style:"margin-top:10px;padding-top:10px;border-top:1px solid var(--line)"},
    h("div",{class:"bar"},
      h("input",{type:"text",value:suggestFeedback,size:44,placeholder:"what's missing? (optional, e.g. a specific person's name)",
        disabled:!isLlm, oninput:e=>{suggestFeedback=e.target.value;}}),
      h("button",{disabled:busy||suggestBusy||!isLlm,
        title: isLlm ? "Asks the same LLM that wrote your first batch of keywords for more, tagged with their own group"
                     : "This job's keyword provider isn't \"llm\" -- there's no model to ask for more",
        onclick:suggestKeywords}, suggestBusy?"Asking...":"Suggest more search terms")),
    suggestError?h("div",{class:"err"}, suggestError):null,
    reviewing.length ? h("div",{style:"margin-top:8px"},
      h("div",{class:"sub"}, `${reviewing.length} suggestion(s) -- check the ones worth searching, then Add. Nothing is `+
        `searched until you do; "Next batch" below will pick up whatever you add next.`),
      h("div",{style:"display:flex;flex-direction:column;gap:6px;margin-top:6px"},
        reviewing.map(k => h("label",{style:"display:flex;gap:8px;align-items:baseline"},
          h("input",{type:"checkbox",checked:!!suggestChecked[k.id],
            onchange:e=>{suggestChecked[k.id]=e.target.checked;render();}}),
          h("span",{}, h("b",{},k.term), " ", h("span",{class:"chip"}, GROUP_LABELS[k.group]||k.group),
            k.meta&&k.meta.why ? h("span",{class:"meta"}, " -- "+k.meta.why) : null)))),
      h("div",{class:"bar",style:"margin-top:8px"},
        h("button",{class:"primary",disabled:busy||suggestBusy,onclick:addSuggested},"Add checked"),
        h("button",{disabled:busy||suggestBusy,onclick:dismissSuggestions},"Dismiss"))) : null);
}
function stockAndDeferPanel(){
  // "Stop go limits" + "pull stock photos first, score relevance later" (both requested directly, same
  // message). stockUsed counts against STOCK_SOURCES regardless of whether a limit is even set, so the
  // number is there to inform the decision of whether to set one, not just to explain one already active.
  const stockUsed = job.assets.filter(a=>STOCK_SOURCES.has(a.source)).length;
  const limit = (job.providers&&job.providers.options&&job.providers.options.stock_limit) || 0;
  const unscored = job.assets.filter(a=>a.status==="pending" && score(a)==null).length;
  return h("div",{style:"margin-top:12px;padding-top:12px;border-top:1px solid var(--line)"},
    h("div",{class:"sub"},"Stock photos (pexels/pixabay/unsplash/nasa): "+stockUsed+
        (limit ? ` of your ${limit}-photo limit` : " pulled so far, no limit set") +
        ". Set a limit to stop pulling more once you hit it -- raise it any time (including after seeing how "+
        "many scored well) to pick up where it left off, no repeats. Deferring relevance scoring lets you pull "+
        "a batch first and decide when to actually score it -- risk/license checks still run either way, "+
        "nothing unsafe goes unflagged."),
    h("div",{class:"bar"},
      h("label",{}, "Stock photo limit ", h("input",{type:"number",min:0,step:1,
          placeholder:"no limit", value:stockLimitDraft, style:"width:84px",
          oninput:e=>{stockLimitDraft=e.target.value;}})),
      h("label",{}, h("input",{type:"checkbox",checked:deferRelevanceDraft,
          onchange:e=>{deferRelevanceDraft=e.target.checked;}}), "Defer relevance scoring for the next round"),
      h("span",{class:"meta"},"(applies next time you click Next batch or Search again above)")),
    unscored ? h("div",{class:"bar",style:"margin-top:8px"},
      h("span",{class:"meta"}, unscored+" pending asset(s) not yet scored for relevance."),
      h("button",{class:"primary",disabled:scoreRelevanceBusy,onclick:scoreRelevanceNow},
        scoreRelevanceBusy?"Scoring…":"Score relevance now"),
      scoreRelevanceError?h("span",{class:"meta",style:"color:var(--no)"},scoreRelevanceError):null) : null);
}
function folderFilesPanel(){
  // "Add your own footage" (#10): a file here is only ever added to THIS job because you explicitly ticked
  // it -- nothing in the server's own-footage folder reaches any job on its own.
  return h("div",{style:"margin-top:12px;padding-top:12px;border-top:1px solid var(--line)"},
    h("details",{open:folderFilesOpen,ontoggle:e=>{folderFilesOpen=e.target.open; if(folderFilesOpen) loadFolderFiles();}},
      h("summary",{},"Or add your own footage from the server's folder"),
      h("div",{class:"sub",style:"margin-top:6px"},"Files your own scraper (or you) dropped on the server (e.g. "+
        "library/scraped/) -- nothing here is ever added to a job automatically; tick specific files for THIS "+
        "job. Queued for the next sourcing round, like Next batch, rather than added immediately -- these are "+
        "already local files, so there's no download that can fail."),
      !folderFilesOpen ? null :
      !folderFiles ? h("div",{class:"sub"},"Loading…") :
      folderFiles.files.length===0 ? h("div",{class:"sub"},`No files found in ${folderFiles.folder}.`) :
      h("div",{},
        h("div",{style:"display:flex;flex-direction:column;gap:4px;margin-top:6px"},
          folderFiles.files.map(f => h("div",{style:"display:flex;gap:8px;align-items:center;flex-wrap:wrap"},
            h("input",{type:"checkbox", disabled: f.selected_for_this_job || !f.usable,
                checked: f.selected_for_this_job || (f.path in folderSelection),
                onchange:e=>toggleFolderFile(f.path, e.target.checked)}),
            h("span",{class:"meta"}, f.path + (f.title?` (${f.title})`:"") +
                (f.selected_for_this_job?" — already selected for this job":"") +
                (!f.usable?" — unsupported format":"")),
            (f.path in folderSelection) ? h("input",{type:"text", placeholder:"note (optional, e.g. why it's yours to use)",
                style:"flex:1;min-width:160px", oninput:e=>{folderSelection[f.path]=e.target.value;}}) : null))),
        h("div",{class:"bar",style:"margin-top:8px"},
          h("button",{class:"primary",disabled:busy,onclick:addFolderFiles},"Add selected files")))));
}
function render(){
  if(!job) return;
  const c = counts(), reviewing = job.state==="assets_review", inScenes = job.state==="scenes_review", inScript = job.state==="script_review";
  const hideGrid = inScenes || inScript;
  const sources = [...new Set(job.assets.map(a=>a.source))];
  const opt=(id,cur,vals)=>h("select",{id,onchange:e=>{ if(id==="src")srcFilter=e.target.value; if(id==="kind")kindFilter=e.target.value; if(id==="sort")sortBy=e.target.value; render();}},
      vals.map(([v,t])=>h("option",{value:v,selected:v===cur},t)));
  const xs = visible();
  const warn = (job.source_notes||[]).filter(n=>n.warning).map(n=>h("div",{class:"banner"},"Warning: "+n.warning));
  const top = h("header",{},
    h("div",{}, h("h1",{}, job.subject), h("div",{class:"sub"}, "Job ", job.id, " · state: ", h("span",{class:"chip"}, job.state),
        job.niche ? [" · niche: ", h("span",{class:"chip"}, NICHE_LABELS[job.niche]||job.niche)] : null,
        job.script_style ? [" · script style: ", h("span",{class:"chip"}, SCRIPT_STYLE_LABELS[job.script_style]||job.script_style)] : null, " · ",
        h("a",{href:"/review"},"all projects"), " · ", h("a",{href:`/jobs/${JOB}/decisions?format=md`,target:"_blank"},"decision log"))),
    h("div",{class:"bar"},
      inScript ? h("span",{class:"tally"}, scriptWordsLineFor(scriptDraft)) :
        (inScenes ? h("span",{class:"tally"}, `${job.scenes.length} scene(s)`) : h("span",{class:"tally"}, `${c.use} use · ${c.rej} reject · ${c.und} undecided · ${c.hidden} hidden`)),
      h("label",{}, "Your name ", h("input",{type:"text",value:store.get("reviewer")||"",placeholder:"recorded in the log",oninput:e=>store.set("reviewer",e.target.value)})),
      hideGrid ? null : h("label",{}, "Min score ", h("input",{type:"range",min:0,max:100,step:5,value:Math.round(minScore*100),
          oninput:e=>{minScore=e.target.value/100;store.set("minScore:"+JOB,String(minScore));render();}}), h("b",{}, pct(minScore))),
      hideGrid ? null : h("label",{}, h("input",{type:"checkbox",checked:showHidden,onchange:e=>{showHidden=e.target.checked;render();}}), "show below threshold"),
      hideGrid ? null : h("label",{}, "Source ", opt("src",srcFilter,[["","all"],...sources.map(s=>[s,s])])),
      hideGrid ? null : h("label",{}, "Type ", opt("kind",kindFilter,[["","all"],["image","photos"],["video","videos"]])),
      hideGrid ? null : h("label",{}, "Sort ", opt("sort",sortBy,[["risk","risk (low first), best score first within each"],["score","best score"],["source","source"]])),
      hideGrid ? null : h("button",{onclick:()=>bulk("use")},"Use all shown (not high-risk)"),
      hideGrid ? null : h("button",{onclick:()=>bulk("rej")},"Mark all shown undecided irrelevant"),
      hideGrid ? null : h("button",{id:"submit",class:"primary",disabled:true,onclick:submit},"...")));
  const body = h("main",{},
    (!reviewing && !inScenes && !inScript)?h("div",{class:"banner"},`This project is in state "${job.state}", not script, asset, or scene review. This page will refresh when it reaches one.`):null,
    warn, error?h("div",{class:"err"},error):null,
    inScript ? scriptReviewBody() : (inScenes ? sceneReviewBody() : assetReviewBody(xs)),
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
      suggestKeywordsPanel(),
      stockAndDeferPanel(),
      h("div",{style:"margin-top:12px;padding-top:12px;border-top:1px solid var(--line)"},
        h("div",{class:"sub"},"Or add links -- a YouTube/TikTok/X/Vimeo/Instagram/news link (pulled with yt-dlp) or a direct "+
          ".mp4/.jpg/... link, one per line. Optionally \"URL | note | position\" (position: a scene number or intro/end). "+
          "Each link downloads right away and shows its own result below. Platform videos are flagged high-risk by "+
          "vetting -- approving one needs a written reason, same as everywhere else. No login/paywall is ever bypassed: "+
          "a link behind one will just fail here, same as it would for yt-dlp on its own."),
        h("textarea",{id:"urlsbox",class:"note",style:"min-height:70px;font-family:monospace",
            placeholder:"https://www.youtube.com/watch?v=... | crime-scene b-roll | 3\nhttps://example.com/clip.mp4"}),
        h("div",{class:"bar",style:"margin-top:6px"},
          h("button",{class:"primary",disabled:busy,onclick:addUrls},"Add links")),
        urlAddStatus.length ? h("div",{style:"margin-top:8px;display:flex;flex-direction:column;gap:3px"},
          urlAddStatus.map(s => h("div",{class:"meta", style:"color:"+(s.ok?"var(--ok)":"var(--no)")},
            (s.ok?"✓ ":"✗ ")+s.url+" — "+s.message))) : null),
      folderFilesPanel()) : null);
  $app.replaceChildren(top, body);
  updateSubmit();
  document.querySelectorAll("video").forEach(v=>v.muted=true);
}
function manualLinksPanel(){
  const xs = manualUrlAssets();
  if (!xs.length) return null;
  return h("div",{class:"panel", style:"border-color:var(--acc)"},
    h("b",{}, `Added by hand -- ${xs.length} awaiting a decision`),
    h("div",{class:"sub"},"Pasted in through \"Add links\", or picked one at a time from a \"Find more\" search -- "+
      "either way, one specific item YOU chose, not something an automated round kept on its own. Always shown "+
      "here regardless of the score/hidden filters or sort order below: a platform link is auto-flagged high risk "+
      "(would otherwise sort last), and any hand-picked item's title/description often doesn't text-match your "+
      "keywords well, so it commonly scores under the relevance threshold too -- either way, it could otherwise "+
      "disappear from view the moment it's added, with nothing on screen to show it worked. Use / Duplicate / "+
      "Irrelevant here work exactly like any other card; once you've decided, it moves to the main grid (Use) "+
      "or the \"Set aside\" panel below (Duplicate/Irrelevant)."),
    h("div",{class:"grid"}, xs.map(card)));
}
function setAsidePanel(){
  // Everything marked Irrelevant or Duplicate (requested directly, after they piled up staying visible in
  // the main grid forever with nothing left to decide about them). Collapsed by default -- unlike the
  // always-open "Added by hand" panel above, these are meant to stay out of the way, not demand attention
  // -- but never gone: one click reopens the list, and Use/Duplicate/Irrelevant here work exactly like any
  // other card, so changing your mind on one just needs a different button, not digging through the log.
  const xs = setAsideAssets();
  if (!xs.length) return null;
  return h("div",{class:"panel"},
    h("details",{open:setAsideOpen,ontoggle:e=>{setAsideOpen=e.target.open;}},
      h("summary",{}, `Set aside -- ${xs.length} marked Irrelevant/Duplicate`),
      !setAsideOpen ? null : h("div",{style:"margin-top:8px"},
        h("div",{class:"sub"},"Kept out of the main grid and its score/hidden filters and sort order, same idea as "+
          "\"Added by hand\" above -- just collapsed, since there's nothing left to decide here unless you want to "+
          "look again. Click Use on any card to bring it back into the main grid."),
        h("div",{class:"grid",style:"margin-top:8px"}, xs.map(card)))));
}
// Groups the already-filtered/sorted `xs` (visible()'s output) into one section per approved keyword,
// requested directly: "different keywords should have a different section, if not enough is pulled up, a
// next section should definitely be easy to do." Every approved keyword gets a section even if nothing in
// `xs` matched it -- an empty section next to a full one is exactly the signal that keyword needs another
// round -- and any term that produced items but isn't (or is no longer) an approved keyword (custom "Search
// again" terms, or old rounds before an approval changed) still gets its own trailing section rather than
// silently vanishing. Sort order within each section is whatever visible() already chose (Map insertion
// preserves first-seen order from `xs`); only which section an item lands in changes here.
function keywordSections(xs){
  const byTerm = new Map();
  xs.forEach(a=>{
    const key = a.query || "(no search term recorded)";
    if (!byTerm.has(key)) byTerm.set(key, []);
    byTerm.get(key).push(a);
  });
  // job.source_notes (pipeline/stages/sourcing.py) has one entry per query actually sent to a source --
  // success or failure -- so it's the only place that can tell "zero results" apart from "never searched".
  const notesByTerm = new Map();
  (job.source_notes||[]).forEach(n=>{
    if (!n.query) return;   // source-level notes (credentials/rate-limit/etc.) carry no query
    if (!notesByTerm.has(n.query)) notesByTerm.set(n.query, []);
    notesByTerm.get(n.query).push(n);
  });
  const sections = [], seen = new Set();
  (job.keywords||[]).filter(k=>k.approved).forEach(k=>{
    seen.add(k.term);
    sections.push({term:k.term, group:k.group, items:byTerm.get(k.term)||[], notes:notesByTerm.get(k.term)||[]});
  });
  byTerm.forEach((items,term)=>{
    if (seen.has(term)) return;
    sections.push({term, group:null, items, notes:notesByTerm.get(term)||[]});
  });
  return sections;
}
// One line per section header: what searching that term actually turned up, per source, or "not searched
// yet" so an unsearched approved keyword doesn't read the same as one that came back empty.
function sectionSummary(notes){
  if (!notes.length) return "not searched yet";
  const bySource = new Map();
  notes.forEach(n=>{
    if (!bySource.has(n.source)) bySource.set(n.source, {found:0, kept:0, errors:0});
    const c = bySource.get(n.source);
    if (n.error || n.outcome==="parse_error" || n.outcome==="unknown_error") c.errors++;
    else { c.found += n.found||0; c.kept += n.kept||0; }
  });
  return [...bySource.entries()].map(([src,c]) =>
    c.errors && !c.found && !c.kept ? `${src}: search failed` : `${src}: ${c.kept} kept of ${c.found} found`).join(" · ");
}
function assetReviewBody(xs){
  const sections = keywordSections(xs);
  return h("div",{},
    h("div",{class:"sub",style:"margin-bottom:10px"},
      `Showing ${xs.length} of ${job.assets.length}. Score = share of a keyword's words found in the item's own title/description/tags (docs/SCORING.md). `+
      `Items under ${pct(minScore)} are hidden. Anything you leave undecided or hidden when you submit is not used (logged as rejected with a note) and is NOT counted as a training label. Only your Use / Duplicate / Irrelevant clicks are saved as labels, immediately, one per click (RELEVANCE_LABELS.jsonl in the project folder) -- separately from "Save and continue", which records the approve/reject decision.`),
    assetReportPanel(),
    checklistPanel(),
    findMorePanel(),
    manualLinksPanel(),
    setAsidePanel(),
    sections.map(sec => h("div",{class:"ksec"},
      h("div",{class:"khead"},
        h("div",{}, h("b",{}, sec.term||"(unlabeled)"),
          sec.group ? h("span",{class:"chip",style:"margin-left:8px"}, GROUP_LABELS[sec.group]||sec.group) : null),
        h("span",{class:"sub"}, `${sec.items.length} shown -- ${sectionSummary(sec.notes)}`)),
      sec.items.length ? h("div",{class:"grid"}, sec.items.map(card))
        : h("div",{class:"kempty"}, sec.notes.length
            ? "Nothing from this keyword is showing right now (filtered out above, or nothing was kept)."
            : "Not searched yet -- see \"Next batch\" below to search it."))));
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
    visualCoveragePanel(),
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
