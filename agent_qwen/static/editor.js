"use strict";
const $ = id => document.getElementById(id);
const board = $("drawing"), ink = document.createElement("canvas");
const source = document.createElement("canvas");
let strokes = [], activeStroke = null, pointerId = null, tool = "circle", hasInput = false;
let jobId = "", timer, selected = null, lastState = null, lastRendered = "", noticeTimer;
let submitting = false, showingInput = false;
const toolHints = {
  circle:"拖动圈出要编辑的对象或区域。",
  pen:"在图片上画线或涂写，标出要处理的位置。",
  text:"输入文字后，点按图中要放置文字的位置。",
  arrow:"从要移动的对象拖向目标位置。"
};
const statusNames = {queued:"排队中", running:"执行中", passed:"验收通过", exhausted:"未通过验收",
  needs_clarification:"需要澄清", error:"服务错误"};
const stages = {queued:"排队中", planning:"读取标注", planned:"已确定编辑意图", preparing:"准备输入",
  prepared:"提示词就绪", revising:"根据验收意见修改提示词", uploading:"上传生成输入", submitting:"提交 Qwen",
  generating:"Qwen 生成中", generated:"候选图已保存", reviewing:"独立验收中", reviewed:"验收完成",
  clarification:"等待补充说明", finished:"已完成"};
const checkNames = {target:"目标对象",effect:"编辑效果",neighbors:"相邻对象",marks:"笔迹残留",
  quality:"局部画质",origin:"移动起点",destination:"移动终点"};
const verdictNames = {pass:"通过",fail:"失败",unclear:"看不清"};
const modes = {original:"原始输入",crop:"局部重做",cleanup:"去除笔迹"};
function reviewLabel(a) {
  if (!a.review) return "待验收";
  return Object.values(a.review.checks).every(v=>v.verdict==="pass") ? "验收通过" : "未通过验收";
}

function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
  clearTimeout(noticeTimer);
  if (message) noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 7500);
}
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
function context(layer) { return layer.getContext("2d"); }
function paint(ctx, s) {
  const p = s.points[0], end = s.points[s.points.length-1];
  ctx.save();
  ctx.strokeStyle = ctx.fillStyle = s.color;
  ctx.lineWidth = s.width;
  ctx.lineCap = ctx.lineJoin = "round";
  ctx.beginPath();
  if (s.tool === "circle") {
    ctx.ellipse((p.x+end.x)/2,(p.y+end.y)/2,Math.abs(p.x-end.x)/2,Math.abs(p.y-end.y)/2,0,0,2*Math.PI);
  } else if (s.tool === "text") {
    ctx.font = "600 " + s.fontSize + "px sans-serif";
    ctx.textBaseline = "top";
    ctx.fillText(s.text,p.x,p.y);
  } else {
    ctx.moveTo(p.x,p.y);
    for (const point of s.points.slice(1)) ctx.lineTo(point.x,point.y);
    if (s.tool === "pen" && s.points.length === 1) {
      ctx.arc(p.x,p.y,s.width/2,0,2*Math.PI);
      ctx.fill();
    }
    if (s.tool === "arrow") {
      const angle = Math.atan2(end.y-p.y,end.x-p.x);
      const head = Math.min(Math.hypot(end.x-p.x,end.y-p.y)*0.45,s.width*6);
      for (const a of [angle-0.48,angle+0.48]) {
        ctx.moveTo(end.x,end.y);
        ctx.lineTo(end.x-head*Math.cos(a),end.y-head*Math.sin(a));
      }
    }
  }
  ctx.stroke();
  ctx.restore();
}
function redraw() {
  const layer = context(ink);
  layer.clearRect(0,0,ink.width,ink.height);
  for (const s of strokes) paint(layer,s);
  if (activeStroke && activeStroke.tool !== "text") paint(layer,activeStroke);
  context(board).drawImage(source,0,0);
  context(board).drawImage(ink,0,0);
  $("undo").disabled = !strokes.length;
}
function point(event) {
  const rect = board.getBoundingClientRect();
  const mapped = (n, start, width, pixels) =>
    Math.round(Math.max(0,Math.min(pixels,(n-start)*pixels/width)));
  return {x:mapped(event.clientX,rect.left,rect.width,board.width),
          y:mapped(event.clientY,rect.top,rect.height,board.height)};
}
board.addEventListener("pointerdown", event => {
  if (!hasInput || submitting || pointerId !== null || !event.isPrimary || event.button !== 0) return;
  const p = point(event);
  const width = Math.max(2,Math.round(Math.min(board.width,board.height)*0.006));
  const s = {tool, color:$("color").value, width, points:[p]};
  if (tool === "text") {
    s.text = $("mark-text").value.trim();
    if (!s.text) { $("mark-text").focus(); return; }
    s.fontSize = Math.max(14,Math.round(width*6));
  } else if (tool !== "pen") {
    s.points.push(p);
  }
  pointerId = event.pointerId;
  board.setPointerCapture(pointerId);
  activeStroke = s;
  redraw();
});
function move(event) {
  if (!activeStroke || event.pointerId !== pointerId || activeStroke.tool === "text") return;
  const p = point(event);
  if (activeStroke.tool === "pen") activeStroke.points.push(p);
  else activeStroke.points[1] = p;
  redraw();
}
board.addEventListener("pointermove", move);
board.addEventListener("pointerup", event => {
  if (!activeStroke || event.pointerId !== pointerId) return;
  move(event);
  strokes.push(activeStroke);
  pointerId = activeStroke = null;
  redraw();
});
function cancelStroke(event) {
  if (event.pointerId !== pointerId) return;
  pointerId = activeStroke = null;
  redraw();
}
board.addEventListener("pointercancel", cancelStroke);
board.addEventListener("lostpointercapture", cancelStroke);
const toolButtons = document.querySelectorAll("[data-tool]");
function selectTool(name) {
  tool = name;
  for (const button of toolButtons) button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
  $("text-row").hidden = tool !== "text";
  $("tool-hint").textContent = toolHints[tool];
}
for (const button of toolButtons) {
  button.onclick = () => {
    selectTool(button.dataset.tool);
    if (tool === "text") $("mark-text").focus();
  };
}
$("mark-text").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.isComposing) event.target.blur();
});
$("undo").onclick = () => { if (hasInput && !submitting) { strokes.pop(); redraw(); } };
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z"
      && !["INPUT","TEXTAREA"].includes(event.target.tagName) && hasInput) {
    event.preventDefault(); $("undo").click();
  }
});
function saveJobLink(id) {
  history.replaceState(null,"",id ? "#"+id : location.pathname);
  try {
    if (id) localStorage.setItem("agent-qwen-job",id);
    else localStorage.removeItem("agent-qwen-job");
  } catch { /* The URL still restores the job when local storage is blocked. */ }
}
function detach() {
  clearTimeout(timer);
  jobId = ""; selected = lastState = null; lastRendered = ""; showingInput = false;
  saveJobLink("");
  $("job").hidden = true; $("idle").hidden = false;
  $("status").textContent = "等待提交"; $("status").dataset.kind = "";
}
async function loadImage(file) {
  if (!file || submitting) return;
  const url = URL.createObjectURL(file), img = new Image();
  try {
    img.src = url; await img.decode();
    if (submitting) return;
    if (hasInput || jobId) {
      $("note").value = $("mark-text").value = "";
      selectTool("circle");
    }
    detach();
    for (const layer of [board,source,ink]) { layer.width=img.naturalWidth; layer.height=img.naturalHeight; }
    board.style.setProperty("--image-ratio", board.width/board.height);
    context(source).drawImage(img,0,0);
    strokes=[]; pointerId=activeStroke=null; hasInput=true;
    $("saved-canvas").hidden=$("empty").hidden=true;
    board.hidden=false; $("generate").disabled=false;
    $("replace-image").hidden=$("drawing-controls").hidden=$("edit-controls").hidden=false;
    $("editor-heading").textContent = "编辑输入";
    $("dimensions").textContent=board.width + " × " + board.height;
    redraw();
  } catch (err) { notice("无法读取图像：" + err.message); }
  finally { URL.revokeObjectURL(url); }
}
$("upload").onchange = event => { loadImage(event.target.files[0]); event.target.value=""; };
const surface = $("surface");
surface.ondragover = e => { e.preventDefault(); surface.classList.add("drag"); };
surface.ondragleave = () => surface.classList.remove("drag");
surface.ondrop = e => { e.preventDefault(); surface.classList.remove("drag"); loadImage(e.dataTransfer.files[0]); };
function png(layer) {
  return new Promise((resolve,reject) => layer.toBlob(blob =>
    blob ? resolve(blob) : reject(new Error("PNG 编码失败")),"image/png"));
}
$("generate").onclick = async () => {
  if (!hasInput || submitting || pointerId !== null || !$("max-tries").reportValidity()) return;
  submitting = true;
  $("generate").disabled = $("upload").disabled = true;
  $("generate-label").textContent = "正在提交…";
  const clean = $("input-kind").value === "clean";
  const note = $("note").value, maxTries = $("max-tries").value;
  try {
    const data=new FormData();
    data.append("canvas",await png(board),"canvas.png");
    data.append("ink",await png(ink),"ink.png");
    if (clean) data.append("source",await png(source),"source.png");
    data.append("strokes",JSON.stringify({width:board.width,height:board.height,strokes}));
    data.append("note",note);
    data.append("max_tries",maxTries);
    const job=await api("/api/jobs",{method:"POST",body:data});
    rememberJob(job.id);
    hasInput = false;
    await poll(job.id);
  } catch (err) {
    notice(err.message);
  } finally {
    submitting = false;
    $("generate").disabled = !hasInput;
    $("upload").disabled = false;
    $("generate-label").textContent = "开始编辑";
  }
};
function renderPreview() {
  const base = "/api/jobs/"+lastState.id;
  const path = showingInput ? base+"/canvas" : base+"/candidate/"+$("attempt").value;
  const description = showingInput ? "该任务的完整标注输入" : "Qwen 实际生成的完整候选图";
  const image = $("candidate"), preview = $("candidate-preview");
  if (image.getAttribute("src") !== path) image.src = path;
  image.alt = description;
  preview.href = path;
  preview.setAttribute("aria-label", "在新标签页查看"+description);
  $("show-candidate").setAttribute("aria-pressed", String(!showingInput));
  $("show-input").setAttribute("aria-pressed", String(showingInput));
}
function showCandidate() {
  const s=lastState, ready=s.attempts.filter(a=>a.candidate);
  $("result").hidden=!ready.length;
  if (!ready.length) return;
  const finished=["passed","exhausted","error"].includes(s.status);
  const a=ready.find(v=>v.number===selected)
    || (finished && ready.find(v=>v.number===s.best)) || ready[ready.length-1];
  const select=$("attempt");
  const labels=ready.map(candidate=>"第 "+candidate.number+" 次 · "+modes[candidate.mode]+" · "+reviewLabel(candidate));
  if (labels.length !== select.options.length || labels.some((label,index)=>label !== select.options[index].text)) {
    select.replaceChildren(...ready.map((candidate,index)=>new Option(labels[index],String(candidate.number))));
  }
  select.value=String(a.number);
  renderPreview();
  $("download").href="/api/jobs/"+s.id+"/candidate/"+a.number+"?download=1";
  $("choice").textContent=(a.number===s.best ? "当前最佳 · " : "") + reviewLabel(a)+" · 种子 "+a.seed+" · "+a.steps+" 步";
  $("reason").hidden=a.number===1;
  $("reason").textContent=(a.regressed?"本轮验收退步。 ":"") + a.reason;
  $("checks").replaceChildren();
  for (const key of Object.keys(checkNames)) {
    const item=a.review?.checks[key];
    if (!item) continue;
    const li=document.createElement("li"), label=document.createElement("strong"), detail=document.createElement("p");
    label.className=item.verdict;
    label.textContent=verdictNames[item.verdict]+" · "+checkNames[key];
    detail.textContent=item.location+" — "+item.evidence;
    li.append(label,detail); $("checks").append(li);
  }
}
function render(s) {
  lastState=s; $("job").hidden=false; $("idle").hidden=true;
  $("status").textContent=statusNames[s.status]; $("status").dataset.kind=s.status;
  $("progress").textContent=stages[s.stage]+" · "+(s.attempts.length ?
    "第 "+s.attempts.length+" / "+s.max_tries+" 次" : "已生成 0 / "+s.max_tries+" 次");
  $("copy").textContent=s.id.slice(0,9)+"…"; $("job-id").value=s.id;
  $("clarify").hidden=s.status!=="needs_clarification"; $("question").textContent=s.question || "";
  $("reconnect").hidden=s.status!=="error"; $("issue").hidden=!s.error; $("issue").textContent=s.error || "";
  showCandidate();
}
async function poll(id) {
  if (id!==jobId) return;
  try {
    const state=await api("/api/jobs/"+id);
    if (id!==jobId) return;
    updateJob(state);
  } catch (err) {
    if (id===jobId) { notice(err.message); timer=setTimeout(()=>poll(id),4000); }
  }
}
function rememberJob(id) {
  clearTimeout(timer);
  jobId=id; selected=null; lastRendered=""; showingInput=false;
  saveJobLink(id);
}
function updateJob(s) {
  const entering = !lastRendered;
  const serialized = JSON.stringify(s);
  if (serialized !== lastRendered) {
    if (entering) showSavedCanvas(s);
    render(s);
    lastRendered = serialized;
  }
  if (["queued","running"].includes(s.status)) timer=setTimeout(()=>poll(s.id),1500);
  if (entering && getComputedStyle($("review-panel")).gridColumnStart === "1") {
    const panel = $("review-panel");
    panel.focus({preventScroll:true});
    panel.scrollIntoView({block:"start"});
  }
}
function showSavedCanvas(s) {
  hasInput=false; board.hidden=true; $("empty").hidden=true; $("generate").disabled=true; $("undo").disabled=true;
  $("replace-image").hidden=false;
  $("drawing-controls").hidden=$("edit-controls").hidden=true;
  $("editor-heading").textContent = "已提交的标注";
  const saved = $("saved-canvas");
  [saved.width,saved.height] = s.canvas_size || s.size;
  saved.style.setProperty("--image-ratio", saved.width/saved.height);
  saved.src="/api/jobs/"+s.id+"/canvas"; saved.hidden=false;
  $("dimensions").textContent=s.size.join(" × ");
}
async function openJob(id) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("请输入完整任务 ID。");
  const state=await api("/api/jobs/"+id);
  rememberJob(id);
  updateJob(state);
}
$("attempt").onchange=()=>{ selected=Number($("attempt").value); showingInput=false; showCandidate(); };
$("show-candidate").onclick=()=>{ showingInput=false; renderPreview(); };
$("show-input").onclick=()=>{ showingInput=true; renderPreview(); };
$("copy").onclick=async ()=>{
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(jobId); notice("已复制任务 ID。"); return; }
    catch { /* Browsers may require a secure origin for clipboard access. */ }
  }
  document.querySelector(".recover").open=true;
  $("job-id").value=jobId; $("job-id").focus(); $("job-id").select();
  notice("已选中完整任务 ID，可直接复制。");
};
$("restore").onsubmit=async e=>{
  e.preventDefault();
  try { await openJob($("job-id").value.trim()); document.querySelector(".recover").open=false; }
  catch (err) { notice(err.message); }
};
async function resume(answer) {
  const data=new FormData();
  if (answer) data.append("answer",answer);
  await api("/api/jobs/"+jobId+"/continue",{method:"POST",body:data});
  clearTimeout(timer); timer=setTimeout(()=>poll(jobId),500); $("answer").value="";
}
$("clarify").onsubmit=e=>{e.preventDefault(); resume($("answer").value).catch(err=>notice(err.message));};
$("reconnect").onclick=()=>resume("").catch(err=>notice(err.message));
window.addEventListener("hashchange", () => {
  const id = location.hash.slice(1);
  if (id && id !== jobId) openJob(id).catch(err=>notice(err.message));
});
let previous = location.hash.slice(1);
try { previous ||= localStorage.getItem("agent-qwen-job"); }
catch { /* Task links still work without browser storage. */ }
if (previous) openJob(previous).catch(err=>notice(err.message));
