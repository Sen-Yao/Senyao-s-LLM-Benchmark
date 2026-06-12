export function asPositiveInt(value:string){
  if(!value.trim())return null;
  if(!/^\d+$/.test(value.trim()))return null;
  return Number(value);
}
export function runConfigErrors(concurrency:string, retries:string){
  const parsedConcurrency=asPositiveInt(concurrency);
  const parsedRetries=asPositiveInt(retries);
  return {
    concurrency: parsedConcurrency===null?'请输入 1–16 的整数':(parsedConcurrency<1||parsedConcurrency>16?'并发数必须在 1–16 之间':''),
    retries: parsedRetries===null?'请输入 0–10 的整数':(parsedRetries<0||parsedRetries>10?'重试次数必须在 0–10 之间':''),
  };
}

export function dimensionLabel(dimension:string){
  const key=dimension.trim().toLowerCase().replace(/\s+/g,'_');
  const labels:Record<string,string>={
    accuracy:'准确性',
    reasoning:'推理能力',
    math:'数学能力',
    code:'代码能力',
    coding:'代码能力',
    knowledge:'知识掌握',
    language:'语言能力',
    language_proficiency:'语言熟练度',
    instruction_following:'指令遵循',
    safety:'安全性',
    robustness:'鲁棒性',
    creativity:'创造力',
    tool_use:'工具使用',
    agent_capability:'Agent 能力',
    subject_knowledge:'领域知识',
    chinese:'中文能力',
    long_context:'长上下文',
  };
  return labels[key]||dimension.trim().replace(/_/g,' ');
}

export function statusLabel(status:string){
  const labels:Record<string,string>={success:'成功',failed:'失败',pending:'待运行',queued:'等待中',waiting:'等待中',running:'运行中',cancelled:'已终止'};
  return labels[status]||status||'未知';
}
export function scoreText(score:number|null){
  if(score===null||score===undefined)return '—';
  const pct=score<=1?Math.round(score*100):Math.round(score);
  return `${pct}/100`;
}
export function openTaskAnchor(slug?:string){
  if(!slug)return;
  const path=`/tasks/${encodeURIComponent(slug)}`;
  if(window.location.pathname!==path) window.history.pushState({},'',path);
  window.dispatchEvent(new CustomEvent('app-route-change',{detail:{route:'tasks',slug}}));
}

export function broadcastModelsChanged(){
  window.dispatchEvent(new CustomEvent('models-changed'));
}

export function broadcastLeaderboardChanged(){
  window.dispatchEvent(new CustomEvent('leaderboard-changed'));
}

export function fmtTime(value?: string | null){
  if(!value)return '—';
  const date=new Date(value.endsWith('Z')?value:value+'Z');
  if(Number.isNaN(date.getTime()))return value;
  return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(date);
}

export function fmtSeconds(value?: number | null){
  if(value===null||value===undefined)return '—';
  if(value===0)return '0ms';
  if(value<1)return `${Math.round(value*1000)}ms`;
  return `${value.toFixed(value<10?2:1)}s`;
}

export function fmtDuration(start?: string | null, end?: string | null){
  if(!start)return '—';
  const startMs=new Date(start.endsWith('Z')?start:start+'Z').getTime();
  const endMs=end?new Date(end.endsWith('Z')?end:end+'Z').getTime():Date.now();
  if(Number.isNaN(startMs)||Number.isNaN(endMs)||endMs<startMs)return '—';
  const ms=endMs-startMs;
  if(ms<1000)return `${ms}ms`;
  const seconds=Math.round(ms/1000);
  if(seconds<60)return `${seconds}s`;
  const minutes=Math.floor(seconds/60);
  const rest=seconds%60;
  return `${minutes}m ${rest}s`;
}

export function inferToolProtocol(modelId:string){
  return /claude|opus|sonnet|haiku/i.test(modelId) ? 'anthropic_tool' : 'openai_function';
}
