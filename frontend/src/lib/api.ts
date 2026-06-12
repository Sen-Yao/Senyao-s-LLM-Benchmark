function probeEnabled(){
  try { return localStorage.getItem('benchmarkProbe') === '1'; } catch { return false; }
}
export function probeLog(label:string, fields:Record<string,unknown>={}){
  if(!probeEnabled())return;
  console.info(`[benchmark-probe] ${label}`, fields);
}

export const api = async (path: string, init?: RequestInit) => {
  const start = performance.now();
  let res: Response;
  try {
    res = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...init });
  } catch (error: any) {
    probeLog('api.network_error', {path, method:init?.method||'GET', message:error?.message||String(error)});
    throw new Error('无法连接后端 API：如果你通过 benchmark.senyao.org 打开，请刷新页面并重新通过 Cloudflare Access 登录；如果仍失败，请检查网络或稍后重试。');
  }
  const text = await res.text();
  const elapsed = performance.now() - start;
  const backendMs = res.headers.get('X-Probe-Duration-Ms');
  probeLog('api', {path, method:init?.method||'GET', status:res.status, totalMs:Number(elapsed.toFixed(1)), backendMs:backendMs?Number(backendMs):null, responseBytes:text.length});
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text || '响应不是 JSON' }; }
  if (!res.ok) throw new Error(data?.detail || data?.error || text || `HTTP ${res.status}`);
  return data;
};
