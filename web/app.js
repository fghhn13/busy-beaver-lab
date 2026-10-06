"use strict";
const $ = id => document.getElementById(id);
const labels = {QUEUED:"Queued",RUNNING:"Running",PAUSED:"Paused",INTERRUPTED:"Interrupted / recoverable",COMPLETED:"Completed",CANCELLED:"Stopped",ERROR:"Error",HALTED:"Halted",NON_HALTING:"Proven non-halting",UNKNOWN:"Undecided"};
const reasons = {halt:"Entered halt state H",exact_cycle:"Exact configuration cycle; evidence verified",step_budget:"Step budget reached",time_budget:"Time budget reached",tape_budget:"Tape budget reached",cancelled:"Stopped by user",error:"Execution error",evidence_verification_failed:"Evidence verification failed"};
let machine, selected, detail, frame, samples = [], traceInfo, center = 0n, chartBounds, timer, pollBusy = false, selectionGeneration = 0, lastTraceFetch = 0;
let replaying = false, replayToken = 0;

function notice(message, error = false) {
  $("notice").textContent = message; $("notice").className = error ? "error" : ""; $("notice").hidden = false;
  clearTimeout(timer); timer = setTimeout(() => $("notice").hidden = true, 4500);
}
async function api(path, data) {
  const response = await fetch("/api" + path, data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
  if (!response.ok) { let error; try { error = await response.json(); } catch { error = {}; } throw new Error(englishText(error.detail,`Request failed (${response.status})`)); }
  return response.json();
}
function safely(handler) { return async (...args) => { try { await handler(...args); } catch (error) { notice(error.message, true); } }; }
function base() { if (!selected) throw new Error("Select or create an experiment first"); return `/runs/${encodeURIComponent(selected.machine_id)}/${encodeURIComponent(selected.run_id)}`; }
function badge(element, status) { element.textContent = labels[status] || status; element.className = "badge " + status; }
function englishText(text, fallback) { return typeof text === "string" && !/[\u3400-\u9fff]/.test(text) ? text : fallback; }
function tapeCells(snapshot) { return snapshot.cells || (snapshot.nonzero_cells || []).map(position => ({position,symbol:1})); }
function symbolColor(symbol) { if (symbol === 0) return "#eef2e8"; if (symbol === 1) return "#315e48"; return `hsl(${(symbol*137.508+195)%360} 48% 44%)`; }
function symbolLegend(definition) {
  $("symbol-legend").replaceChildren();
  for (const symbol of definition.symbols) { const item=document.createElement("span"), dot=document.createElement("i"); dot.className="dot"; dot.style.background=symbolColor(symbol); item.append(dot,document.createTextNode(String(symbol))); $("symbol-legend").append(item); }
  const head=document.createElement("span"); head.textContent="◇ Head"; $("symbol-legend").append(head);
}
function transitionTable(definition, snapshot) {
  const table = document.createElement("table"), header = table.insertRow();
  for (const text of ["State", ...definition.symbols.map(s=>`Read ${s}`)]) { const th = document.createElement("th"); th.textContent = text; header.append(th); }
  for (const state of definition.states) {
    const row = table.insertRow(); row.insertCell().textContent = state;
    for (const symbol of definition.symbols) { const cell = row.insertCell(), action = definition.transitions[state][symbol]; cell.textContent = `${action.write}${action.move}${action.next}`;
      if (snapshot && snapshot.state === state && (tapeCells(snapshot).find(c=>c.position===snapshot.head)?.symbol ?? 0) === symbol) cell.className = "active";
    }
  }
  $("transition-table").replaceChildren(table);
}
async function loadMachines(preferred) {
  const list = await api("/machines"); $("machine").replaceChildren();
  for (const item of list) { const option = document.createElement("option"); option.value = item.id; option.textContent = item.error ? `${item.id} · Invalid configuration` : `${englishText(item.name,item.id)} · ${item.symbols} symbols`; option.disabled = !!item.error; $("machine").append(option); }
  if (preferred && list.some(x => x.id === preferred)) $("machine").value = preferred;
  else if (list.some(x => x.id === "bb2_champion")) $("machine").value = "bb2_champion";
  await chooseMachine();
}
async function chooseMachine() {
  machine = await api(`/machines/${encodeURIComponent($("machine").value)}`);
  $("description").textContent = englishText(machine.description,"Imported machine configuration.");
  const editor = {...machine,name:englishText(machine.name,machine.id),description:englishText(machine.description,"Imported machine configuration.")};
  $("machine-json").value = JSON.stringify(editor, null, 2); transitionTable(machine); symbolLegend(machine);
}
function options(startPaused) {
  return {max_steps:Number($("max-steps").value),max_seconds:Number($("max-seconds").value),max_nonzero_cells:Number($("max-cells").value),detect_cycles:$("cycles").checked,start_paused:startPaused};
}
async function createRun() {
  const metadata = await api("/runs", {machine_id:$("machine").value,options:options($("start-paused").checked)});
  await selectRun(metadata); await refreshRuns(); notice("Experiment created in a separate run directory");
}
async function refreshRuns() {
  const runs = await api("/runs"), filter = $("filter").value; $("runs").replaceChildren();
  for (const run of runs) {
    if (filter && run.result?.conclusion !== filter) continue;
    const button = document.createElement("button"); button.className = "run-card" + (selected?.run_id === run.run_id ? " selected" : "");
    const title = document.createElement("strong"); title.textContent = run.machine_id;
    const time = document.createElement("small"); time.textContent = new Date(run.created_at).toLocaleString("en-GB");
    const stats = document.createElement("small"); stats.textContent = `${run.snapshot?.steps ?? 0} steps · ${run.snapshot?.nonblank_count ?? run.snapshot?.ones ?? 0} nonblank`;
    const status = document.createElement("span"); badge(status,run.result?.conclusion || run.status);
    button.append(title,time,stats,status); button.onclick = safely(() => selectRun(run)); $("runs").append(button);
  }
  if (!$("runs").children.length) { const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "No matching experiments."; $("runs").append(empty); }
}
async function selectRun(run) {
  stopPlayback(); selected = run; detail = null; frame = null; samples = []; chartBounds = null; lastTraceFetch = 0; selectionGeneration++;
  $("follow-live").checked = true; $("timeline").value = 0; await updateDetail(true); await refreshRuns();
}
async function updateDetail(forceTrace = false) {
  if (!selected) return;
  const generation = selectionGeneration, url = base(), result = await api(url);
  if (generation !== selectionGeneration) return;
  detail = result; $("run-title").textContent = englishText(result.machine.name,result.machine.id); symbolLegend(result.machine);
  $("run-subtitle").textContent = `${result.machine.states.length} working states · ${result.machine.symbols.length} symbols · independent run`;
  badge($("status"), result.result?.conclusion || result.run.status);
  $("result-path").textContent = `results/${selected.machine_id}/${selected.run_id}/`;
  const completed = !!result.result;
  for (const button of document.querySelectorAll("[data-action]")) {
    button.disabled = completed || result.run.status === "ERROR" || (result.run.status === "INTERRUPTED" && !["resume","step"].includes(button.dataset.action));
  }
  $("conclusion").textContent = completed ? `${labels[result.result.conclusion]} · ${reasons[result.result.reason] || result.result.reason}${result.result.message ? ": "+englishText(result.result.message,"Execution error; see the saved result.") : ""}` : `${labels[result.run.status] || result.run.status}. No final conclusion yet.`;
  const verification = result.result?.verification;
  $("verification").textContent = verification ? englishText(verification.reason,verification.valid===true ? "Independent replay verified" : "Evidence has not been verified") : (result.run.cycle_detection_active === false && result.run.options.detect_cycles ? "Cycle cache limit reached. Simulation continues; this does not prove non-halting." : "Exact cycle detection. Undecided results carry no non-halting proof.");
  $("verify").disabled = !completed || !["halt","exact_cycle"].includes(result.result.reason);
  $("download").hidden = !completed; $("download").href = "/api" + url + "/download";
  $("timeline").max = result.snapshot.steps;
  if ($("follow-live").checked || !frame) displayFrame(result.snapshot);
  if (forceTrace || Date.now() - lastTraceFetch > 1500 || (completed && samples.at(-1)?.steps !== result.snapshot.steps)) {
    const trace = await api(url + "/trace"); if (generation !== selectionGeneration) return;
    samples = trace.samples; traceInfo = trace; lastTraceFetch = Date.now(); drawSpacetime();
    $("trace-label").textContent = `X: absolute position · Y: actual step · stored interval ${trace.stored_interval} steps · display stride ${trace.response_stride} · click to replay; gaps indicate unsampled time`;
  }
}
function canvasContext(id) {
  const canvas = $(id); canvas.dataset.logicalHeight ||= canvas.getAttribute("height");
  const width = Math.max(250,canvas.clientWidth), height = Number(canvas.dataset.logicalHeight), ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio; canvas.height = height * ratio; canvas.style.height = height + "px";
  const ctx = canvas.getContext("2d"); ctx.scale(ratio,ratio); return {ctx,width,height};
}
function displayFrame(snapshot) {
  frame = snapshot;
  for (const [id,key] of [["stat-steps","steps"],["stat-state","state"],["stat-head","head"]]) $(id).textContent = snapshot[key];
  $("stat-ones").textContent = snapshot.nonblank_count ?? snapshot.ones;
  const counts = snapshot.symbol_counts || {"1":snapshot.ones};
  $("symbol-counts").textContent = "Nonblank symbol counts: " + Object.entries(counts).map(([s,n])=>`${s}: ${n}`).join(" · ");
  $("timeline").value = snapshot.steps; $("seek-step").value = snapshot.steps;
  $("replay-label").textContent = `Viewing step ${snapshot.steps}${$("follow-live").checked ? " · live" : " · exact replay"}`;
  const rule = snapshot.last_transition;
  $("rule").textContent = rule ? `Step ${snapshot.steps}: ${rule.state} read ${rule.read} → write ${rule.write} → ${rule.move === "R" ? "right" : "left"} → ${rule.next} · write position ${rule.head}` : "Initial configuration · no transition executed";
  transitionTable(detail?.machine || machine,snapshot); drawTape();
}
function drawTape() {
  const {ctx,width,height} = canvasContext("tape"); ctx.fillStyle = "#fafbf8"; ctx.fillRect(0,0,width,height);
  if (!frame) { ctx.fillStyle = "#77857b"; ctx.font = "13px sans-serif"; ctx.fillText("The tape starts at position 0",20,85); return; }
  if ($("follow-head").checked) center = BigInt(frame.head);
  const size = Number($("cell-size").value), count = Math.ceil(width / size) + 2, start = center - BigInt(Math.floor(count / 2)), cells = new Map(tapeCells(frame).map(c=>[c.position,c.symbol]));
  for (let i = 0; i < count; i++) {
    const position = start + BigInt(i), x = width/2 + (i - Math.floor(count/2))*size - size/2, key = position.toString(), symbol = cells.get(key) ?? 0;
    ctx.fillStyle = symbolColor(symbol); ctx.fillRect(x+2,62,size-4,46);
    ctx.fillStyle = symbol ? "white" : "#a0aca0"; ctx.font = "18px Consolas, monospace"; ctx.textAlign = "center"; ctx.fillText(String(symbol), x+size/2,91);
    ctx.fillStyle = "#77857b"; ctx.font = "10px Consolas, monospace"; ctx.fillText(key,x+size/2,128);
    if (key === frame.head) { ctx.strokeStyle = "#d79447"; ctx.lineWidth = 2; ctx.strokeRect(x+1,61,size-2,48); ctx.fillStyle = "#d79447"; ctx.beginPath(); ctx.moveTo(x+size/2,57); ctx.lineTo(x+size/2-6,49); ctx.lineTo(x+size/2+6,49); ctx.fill(); ctx.font = "bold 14px sans-serif"; ctx.fillText(frame.state,x+size/2,38); }
    if (key === frame.last_transition?.head) { ctx.fillStyle = "#277454"; ctx.fillRect(x+7,140,size-14,3); }
  }
}
function drawSpacetime() {
  const {ctx,width,height} = canvasContext("spacetime"); ctx.fillStyle = "#fafbf8"; ctx.fillRect(0,0,width,height);
  if (!samples.length) { ctx.fillStyle = "#77857b"; ctx.font = "12px sans-serif"; ctx.fillText("Run a machine to view tape history and the head trail",20,45); return; }
  let min = BigInt(samples[0].head), max = min;
  for (const s of samples) { for (const x of [s.head,s.min_head,s.max_head,...tapeCells(s).map(c=>c.position)]) { const p = BigInt(x); if (p < min) min = p; if (p > max) max = p; } }
  min -= 2n; max += 2n;
  const lastStep = BigInt(samples.at(-1).steps), plot = {left:57,top:27,width:width-75,height:height-56};
  const span = max-min+1n, denominator = lastStep+1n;
  const px = position => plot.left + Number((BigInt(position)-min)*1000000n/span)/1000000*plot.width;
  const py = step => plot.top + Number(BigInt(step)*1000000n/denominator)/1000000*plot.height;
  ctx.fillStyle = "#edf1e8"; ctx.fillRect(plot.left,plot.top,plot.width,plot.height);
  const cellWidth = Math.max(1,plot.width / Number(span)), rowHeight = Math.max(1,plot.height/(Number(lastStep)+1));
  for (const s of samples) { const y = py(s.steps); for (const c of tapeCells(s)) { ctx.fillStyle=symbolColor(c.symbol); ctx.fillRect(px(c.position),y,cellWidth,rowHeight); }
    ctx.strokeStyle="#d79447"; ctx.lineWidth=2; ctx.strokeRect(px(s.head)+1,y+1,Math.max(3,cellWidth-2),Math.max(3,rowHeight-2)); }
  ctx.fillStyle = "#77857b"; ctx.font = "10px Consolas, monospace"; ctx.textAlign = "right"; ctx.fillText("0",plot.left-9,plot.top+5); ctx.fillText(lastStep.toString(),plot.left-9,plot.top+plot.height); ctx.textAlign = "left"; ctx.fillText(min.toString(),plot.left,height-9); ctx.textAlign = "right"; ctx.fillText(max.toString(),width-18,height-9);
  ctx.textAlign = "left"; ctx.fillText(span > BigInt(Math.floor(plot.width)) ? "Compressed: pixels may represent several cells" : "Each sample shows the actual tape contents",plot.left,14);
  chartBounds = {...plot,lastStep};
}
async function seek(step, generation = selectionGeneration) {
  if (!selected || !detail) return;
  const requested = BigInt(step), max = BigInt(detail.snapshot.steps);
  if (requested < 0n || requested > max) throw new Error("Step is outside the executed range");
  const snapshot = await api(base() + "/frame?step=" + requested);
  if (generation !== selectionGeneration) return;
  $("follow-live").checked = false; displayFrame(snapshot);
}
function stopPlayback() { replaying = false; replayToken++; $("replay-play").textContent = "Play replay"; }
async function playback() {
  if (replaying) { stopPlayback(); return; }
  if (!detail) throw new Error("Create an experiment first");
  replaying = true; const token = ++replayToken, generation = selectionGeneration;
  $("follow-live").checked = false; $("replay-play").textContent = "Pause replay";
  try {
    if (BigInt(frame.steps) >= BigInt(detail.snapshot.steps)) await seek(0);
    while (replaying && token === replayToken && generation === selectionGeneration) {
      const next = BigInt(frame.steps)+1n; if (next > BigInt(detail.snapshot.steps)) break;
      await seek(next,generation); await new Promise(resolve => setTimeout(resolve,Number($("replay-speed").value)));
    }
  } finally { if (token === replayToken) stopPlayback(); }
}

$("machine").onchange = safely(chooseMachine);
$("new-template").onclick = safely(() => {
  const n=Number($("new-states").value), symbols=$("new-symbols").value.split(",").map(s=>Number(s.trim()));
  if (!Number.isInteger(n) || n<1 || n>32 || symbols.length<2 || symbols.length>32 || new Set(symbols).size!==symbols.length || !symbols.includes(0) || symbols.some(s=>!Number.isInteger(s) || s<0 || s>255)) throw new Error("Use 1–32 states and 2–32 unique integer symbols (0–255), including blank 0.");
  const states=[..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"].filter(s=>s!=="H").concat(Array.from({length:7},(_,i)=>`S${i+1}`)).slice(0,n);
  const data={schema_version:2,id:"new_multi_symbol_machine",name:"New multi-symbol machine",description:"Edit the transitions before saving.",semantics:"multi-symbol-two-way-write-move-halt-v1",states,symbols,blank_symbol:0,halt_state:"H",initial:{state:"A",head:0,cells:[]},transitions:Object.fromEntries(states.map(state=>[state,Object.fromEntries(symbols.map(s=>[String(s),{write:s,move:"R",next:"H"}]))]))};
  $("machine-json").value=JSON.stringify(data,null,2); $("configuration-editor").open=true; notice("Template ready. Edit its ID and transition table, then save.");
});
$("save-machine").onclick = safely(async () => { const data = JSON.parse($("machine-json").value); await api("/machines",data); await loadMachines(data.id); notice("New machine saved to its own JSON file"); });
$("start").onclick = safely(async () => { $("start").disabled = true; try { await createRun(); } finally { $("start").disabled = false; } });
$("batch").onclick = safely(async () => { const list = await api("/runs"); if (list.filter(r => ["RUNNING","PAUSED","QUEUED"].includes(r.status)).length) throw new Error("Finish or stop active experiments before running benchmarks"); let latest; for (const id of ["halt_immediately","two_step_cycle","right_drifter","bb2_champion"]) latest = await api("/runs",{machine_id:id,options:options(false)}); await selectRun(latest); notice("Four benchmark experiments submitted"); });
for (const button of document.querySelectorAll("[data-action]")) button.onclick = safely(async () => { await api(base()+"/control",{action:button.dataset.action}); if (["resume","step"].includes(button.dataset.action)) { stopPlayback(); $("follow-live").checked=true; } await updateDetail(true); });
$("refresh").onclick = safely(refreshRuns); $("filter").onchange = safely(refreshRuns);
$("verify").onclick = safely(async () => { const result = await api(base()+"/verify",{}); $("verification").textContent=result.reason; notice(result.reason,result.valid===false); });
$("seek").onclick = safely(async () => { stopPlayback(); await seek($("seek-step").value); });
$("timeline").onchange = safely(async () => { stopPlayback(); await seek($("timeline").value); });
$("previous").onclick = safely(async () => { stopPlayback(); await seek(BigInt(frame.steps)-1n); });
$("next").onclick = safely(async () => { stopPlayback(); await seek(BigInt(frame.steps)+1n); });
$("replay-play").onclick = safely(playback);
$("follow-live").onchange = () => { stopPlayback(); if ($("follow-live").checked && detail) displayFrame(detail.snapshot); };
$("follow-head").onchange = drawTape; $("cell-size").onchange = drawTape;
$("pan-left").onclick = () => { $("follow-head").checked=false; center -= 10n; drawTape(); }; $("pan-right").onclick = () => { $("follow-head").checked=false; center += 10n; drawTape(); };
$("spacetime").onclick = safely(async event => { if (!chartBounds) return; const rect=$("spacetime").getBoundingClientRect(), fraction=(event.clientY-rect.top-chartBounds.top)/chartBounds.height; if (fraction<0 || fraction>1) return; stopPlayback(); await seek(chartBounds.lastStep*BigInt(Math.round(fraction*1000000))/1000000n); });
$("export-chart").onclick = safely(async () => { if (!selected || !samples.length) throw new Error("Run an experiment before exporting its spacetime chart");
  const png = $("spacetime").toDataURL("image/png");
  await api(base()+"/visualization",{png,frame_step:frame?.steps,sample_steps:samples.map(s=>s.steps),stored_interval:traceInfo?.stored_interval,response_stride:traceInfo?.response_stride});
  const link = document.createElement("a"); link.download=`${selected.machine_id}_spacetime.png`; link.href=png; link.click(); notice("PNG saved to this experiment folder and downloaded"); });
window.addEventListener("resize",()=>{ drawTape(); drawSpacetime(); });
async function poll() {
  if (pollBusy) return; pollBusy=true;
  try { await updateDetail(); await refreshRuns(); $("connection").textContent="Local service connected"; } catch { $("connection").textContent="Connection lost · retrying"; } finally { pollBusy=false; }
}
safely(async()=>{ await loadMachines(); await refreshRuns(); $("connection").textContent="Local service connected"; drawTape(); drawSpacetime(); setInterval(poll,750); })();
