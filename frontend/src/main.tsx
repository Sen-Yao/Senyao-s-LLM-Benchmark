import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactECharts from 'echarts-for-react';
import { Activity, Copy, Database, Eye, EyeOff, Play, RefreshCw, Settings, TestTube2, Trash2, Trophy } from 'lucide-react';
import './styles.css';

const api = async (path: string, init?: RequestInit) => {
  const res = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...init });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
};

type Leaderboard = { dimensions: string[]; rows: Array<{model_id:number; model:string; provider:string; overall:number|null; dimensions:Record<string,number|null>; coverage:{current:number; total:number; status:string}}> };
type TaskChange = {id:number; task_slug:string; change_type:string; old_hash:string; new_hash:string; requires_rerun:boolean; created_at:string};
type Task = {id:number; slug:string; title:string; category:string; dimension:string; evaluator_type:string; content_hash:string; active:boolean; covered_models:number; stale_models:number; pending_models:number; total_models:number; latest_change?:TaskChange|null};
type Provider = {id:number; name:string; api_base:string; api_key_saved:boolean; api_key_fingerprint:string; enabled:boolean; notes:string};
type Model = {id:number; provider_id:number; provider_name?:string; display_name:string; model_id:string; enabled:boolean};
type Judge = {id:number; provider_id:number; provider_name?:string; name:string; model_id:string; temperature:number; enabled:boolean};

type RunResult = {task_id:number; score:number|null; status:string; response:string; judge_reason:string; error:string};

function App(){
  const [tab,setTab]=useState('leaderboard');
  return <div className="shell">
    <aside className="rail"><div className="brand"><span className="mark">Σ</span><div><b>Senyao Benchmark</b><small>LLM 审计控制台</small></div></div>
      <button onClick={()=>setTab('leaderboard')} className={tab==='leaderboard'?'on':''}><Trophy size={18}/>排行榜</button>
      <button onClick={()=>setTab('models')} className={tab==='models'?'on':''}><Activity size={18}/>模型审计</button>
      <button onClick={()=>setTab('tasks')} className={tab==='tasks'?'on':''}><Database size={18}/>题库</button>
      <button onClick={()=>setTab('settings')} className={tab==='settings'?'on':''}><Settings size={18}/>设置</button>
    </aside>
    <main>{tab==='leaderboard'&&<LeaderboardPage/>}{tab==='models'&&<ModelsPage/>}{tab==='tasks'&&<TasksPage/>}{tab==='settings'&&<SettingsPage/>}</main>
  </div>;
}

function LeaderboardPage(){
  const [data,setData]=useState<Leaderboard>({dimensions:[],rows:[]});
  const [query,setQuery]=useState(''); const [onlyComplete,setOnlyComplete]=useState(false); const [sort,setSort]=useState('overall');
  const load=()=>api('/leaderboard').then(setData);
  useEffect(()=>{load()},[]);
  const rows=data.rows.filter(r=>(r.model.toLowerCase().includes(query.toLowerCase())||r.provider.toLowerCase().includes(query.toLowerCase()))&&(!onlyComplete||r.coverage.status==='complete')).sort((a,b)=>((sort==='overall'?b.overall:b.dimensions[sort])??-1)-((sort==='overall'?a.overall:a.dimensions[sort])??-1));
  const chartOption=useMemo(()=>({tooltip:{},legend:{top:0},radar:{indicator:data.dimensions.map(d=>({name:d,max:100}))},series:[{type:'radar',data:rows.slice(0,5).map(r=>({name:r.model,value:data.dimensions.map(d=>r.dimensions[d]??0)}))}]}),[data,rows]);
  return <section><Header title="排行榜" sub="能力矩阵 + 完整性筛选。Partial/Stale 模型不会被静默混进同一结论。" action={<button onClick={load}><RefreshCw size={16}/>刷新</button>}/>
    <div className="toolbar"><input placeholder="筛选模型/供应商" value={query} onChange={e=>setQuery(e.target.value)}/><select value={sort} onChange={e=>setSort(e.target.value)}><option value="overall">总分</option>{data.dimensions.map(d=><option key={d} value={d}>{d}</option>)}</select><label><input type="checkbox" checked={onlyComplete} onChange={e=>setOnlyComplete(e.target.checked)}/> 只看完整评测</label><span>{rows.length} models</span></div>
    <div className="matrix"><table><thead><tr><th>模型</th><th>供应商</th><th>总分</th>{data.dimensions.map(d=><th key={d}>{d}</th>)}<th>覆盖</th></tr></thead><tbody>{rows.map(r=><tr key={r.model_id}><td><b>{r.model}</b></td><td>{r.provider}</td><td className="score">{r.overall??'—'}</td>{data.dimensions.map(d=><td key={d}>{r.dimensions[d]??'—'}</td>)}<td><span className={r.coverage.status==='complete'?'pill ok':'pill warn'}>{r.coverage.status} · {r.coverage.current}/{r.coverage.total}</span></td></tr>)}</tbody></table></div>
    <div className="panel"><h3>Top 模型能力雷达</h3><ReactECharts option={chartOption} style={{height:360}}/></div>
  </section>;
}

function ModelsPage(){
  const [models,setModels]=useState<Model[]>([]); const [judges,setJudges]=useState<Judge[]>([]); const [selected,setSelected]=useState<number|null>(null); const [results,setResults]=useState<RunResult[]>([]); const [judge,setJudge]=useState<number|''>(''); const [busy,setBusy]=useState('');
  const load=()=>{api('/models').then(setModels); api('/judges').then(setJudges)}; useEffect(load,[]);
  const loadRuns=async(m:Model)=>{setSelected(m.id); const runs=await api('/runs'); const run=runs.find((x:any)=>x.model_id===m.id); setResults(run? await api(`/runs/${run.run_id}/results`):[])};
  const runSelected=async()=>{if(!selected)return; setBusy('已加入运行队列'); await api('/runs',{method:'POST',body:JSON.stringify({model_ids:[selected],judge_profile_id:judge||null})});};
  return <section><Header title="模型审计" sub="单模型运行、最近结果审计、prompt/response/judge reason 渐进式披露。" action={<div className="inline"><select value={judge} onChange={e=>setJudge(e.target.value?Number(e.target.value):'')}><option value="">不使用裁判/仅确定性题</option>{judges.map(j=><option key={j.id} value={j.id}>{j.name}</option>)}</select><button onClick={runSelected} disabled={!selected}><Play size={16}/>运行选中模型</button></div>}/>
    {busy&&<p className="notice">{busy}</p>}<div className="split"><div className="list">{models.map(m=><button className={selected===m.id?'row on':'row'} onClick={()=>loadRuns(m)} key={m.id}>{m.display_name}<small>{m.provider_name} · {m.model_id}</small></button>)}</div>
    <div className="panel grow">{results.length?results.map((r,i)=><details key={i} className="audit"><summary>题目 #{r.task_id} · {r.status} · 分数 {r.score??'—'}</summary><h4>裁判理由</h4><p>{r.judge_reason||'—'}</p><h4>模型回答</h4><pre>{r.response||r.error||'—'}</pre></details>):<p className="muted">选择模型查看最近一次运行详情。若无结果，可点击右上角运行。</p>}</div></div>
  </section>;
}

function TasksPage(){
  const [tasks,setTasks]=useState<Task[]>([]); const [changes,setChanges]=useState<TaskChange[]>([]); const [models,setModels]=useState<Model[]>([]); const [selectedModels,setSelectedModels]=useState<number[]>([]); const [onlyChanged,setOnlyChanged]=useState(false);
  const load=()=>{api('/tasks').then(setTasks); api('/tasks/changes').then(setChanges); api('/models').then(setModels)};
  useEffect(()=>{load()},[]);
  const sync=async()=>{await api('/tasks/sync',{method:'POST'}); load()};
  const rerun=async(slug:string)=>{await api(`/runs/incremental/task/${slug}`,{method:'POST',body:JSON.stringify({model_ids:selectedModels})}); alert('已加入增量重跑队列：'+slug)};
  const visible=tasks.filter(t=>!onlyChanged || t.stale_models>0 || t.latest_change?.requires_rerun);
  return <section><Header title="题库" sub="YAML 维护；hash 变化后暴露 stale 覆盖，并可按题触发已有模型增量重跑。" action={<button onClick={sync}>同步题库</button>}/>
  <div className="toolbar"><label><input type="checkbox" checked={onlyChanged} onChange={e=>setOnlyChanged(e.target.checked)}/> 只看变更/待重跑</label><span>选择用于增量重跑的模型：</span>{models.map(m=><label key={m.id}><input type="checkbox" checked={selectedModels.includes(m.id)} onChange={e=>setSelectedModels(e.target.checked?[...selectedModels,m.id]:selectedModels.filter(x=>x!==m.id))}/> {m.display_name}</label>)}</div>
  <div className="matrix"><table><thead><tr><th>ID</th><th>标题</th><th>维度</th><th>评估</th><th>Hash</th><th>覆盖</th><th>状态</th><th>操作</th></tr></thead><tbody>{visible.map(t=><tr key={t.slug}><td>{t.slug}</td><td>{t.title}</td><td>{t.dimension}</td><td>{t.evaluator_type}</td><td><code>{t.content_hash.slice(0,10)}</code></td><td>fresh {t.covered_models}/{t.total_models} · stale {t.stale_models}</td><td>{t.latest_change?<span className={t.latest_change.requires_rerun?'pill warn':'pill ok'}>{t.latest_change.change_type}</span>:<span className="pill ok">stable</span>}</td><td><button disabled={!selectedModels.length} onClick={()=>rerun(t.slug)}>补跑此题</button></td></tr>)}</tbody></table></div>
  <div className="panel"><h3>最近题库变更</h3>{changes.length?changes.slice(0,8).map(c=><div className="provider" key={c.id}><b>{c.task_slug}</b><small>{c.change_type} · rerun={String(c.requires_rerun)}</small><code>{(c.old_hash||'—').slice(0,10)} → {(c.new_hash||'—').slice(0,10)}</code></div>):<p className="muted">暂无变更事件。</p>}</div></section>;
}

function SettingsPage(){
  const [providers,setProviders]=useState<Provider[]>([]); const [models,setModels]=useState<Model[]>([]); const [judges,setJudges]=useState<Judge[]>([]); const [show,setShow]=useState(false); const [msg,setMsg]=useState('');
  const [providerForm,setProviderForm]=useState({name:'',api_base:'',api_key:''});
  const [modelForm,setModelForm]=useState({provider_id:'',display_name:'',model_id:''});
  const [judgeForm,setJudgeForm]=useState({provider_id:'',name:'',model_id:'',temperature:0});
  const load=()=>{api('/providers').then(setProviders); api('/models').then(setModels); api('/judges').then(setJudges)}; useEffect(load,[]);
  const copy=async(v:string)=>navigator.clipboard.writeText(v);
  const saveProvider=async()=>{await api('/providers',{method:'POST',body:JSON.stringify(providerForm)}); setProviderForm({name:'',api_base:'',api_key:''}); load()};
  const saveModel=async()=>{await api('/models',{method:'POST',body:JSON.stringify({...modelForm,provider_id:Number(modelForm.provider_id)})}); setModelForm({provider_id:'',display_name:'',model_id:''}); load()};
  const saveJudge=async()=>{await api('/judges',{method:'POST',body:JSON.stringify({...judgeForm,provider_id:Number(judgeForm.provider_id)})}); setJudgeForm({provider_id:'',name:'',model_id:'',temperature:0}); load()};
  const testProvider=async(p:Provider)=>{const r=await api(`/providers/${p.id}/test`,{method:'POST',body:JSON.stringify({model_id:models.find(m=>m.provider_id===p.id)?.model_id})}); setMsg(`${p.name}: ${r.ok?'OK '+r.latency+'s':'失败 '+r.error}`)};
  return <section><Header title="设置" sub="管理 OpenAI-compatible 供应商、模型与裁判模型。密钥加密保存，后端不回显明文。" />{msg&&<p className="notice">{msg}</p>}
    <div className="settings-grid"><div className="panel"><h3>添加供应商</h3><input placeholder="名称" value={providerForm.name} onChange={e=>setProviderForm({...providerForm,name:e.target.value})}/><input placeholder="API Endpoint" value={providerForm.api_base} onChange={e=>setProviderForm({...providerForm,api_base:e.target.value})}/><div className="secret"><input type={show?'text':'password'} placeholder="API Key" value={providerForm.api_key} onChange={e=>setProviderForm({...providerForm,api_key:e.target.value})}/><button onClick={()=>setShow(!show)}>{show?<EyeOff/>:<Eye/>}</button><button onClick={()=>copy(providerForm.api_key)}><Copy/></button></div><button onClick={saveProvider}>保存供应商</button></div>
    <div className="panel"><h3>添加模型</h3><select value={modelForm.provider_id} onChange={e=>setModelForm({...modelForm,provider_id:e.target.value})}><option value="">选择供应商</option>{providers.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><input placeholder="显示名称" value={modelForm.display_name} onChange={e=>setModelForm({...modelForm,display_name:e.target.value})}/><input placeholder="OpenAI-compatible model id" value={modelForm.model_id} onChange={e=>setModelForm({...modelForm,model_id:e.target.value})}/><button onClick={saveModel}>保存模型</button></div>
    <div className="panel"><h3>添加裁判模型</h3><select value={judgeForm.provider_id} onChange={e=>setJudgeForm({...judgeForm,provider_id:e.target.value})}><option value="">选择供应商</option>{providers.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><input placeholder="裁判名称" value={judgeForm.name} onChange={e=>setJudgeForm({...judgeForm,name:e.target.value})}/><input placeholder="model id" value={judgeForm.model_id} onChange={e=>setJudgeForm({...judgeForm,model_id:e.target.value})}/><input type="number" step="0.1" value={judgeForm.temperature} onChange={e=>setJudgeForm({...judgeForm,temperature:Number(e.target.value)})}/><button onClick={saveJudge}>保存裁判</button></div>
    <div className="panel"><h3>供应商</h3>{providers.map(p=><div className="provider" key={p.id}><b>{p.name}</b><small>{p.api_base}</small><span>Key 指纹：{p.api_key_fingerprint||'未保存'}</span><div className="inline"><button onClick={()=>testProvider(p)}><TestTube2 size={15}/>测试</button><button onClick={async()=>{await api(`/providers/${p.id}`,{method:'DELETE'});load()}}><Trash2 size={15}/>删除</button></div></div>)}</div>
    <div className="panel"><h3>模型</h3>{models.map(m=><div className="provider" key={m.id}><b>{m.display_name}</b><small>{m.provider_name} · {m.model_id}</small><button onClick={async()=>{await api(`/models/${m.id}`,{method:'DELETE'});load()}}><Trash2 size={15}/>删除</button></div>)}</div>
    <div className="panel"><h3>裁判</h3>{judges.map(j=><div className="provider" key={j.id}><b>{j.name}</b><small>{j.provider_name} · {j.model_id} · temp={j.temperature}</small><button onClick={async()=>{await api(`/judges/${j.id}`,{method:'DELETE'});load()}}><Trash2 size={15}/>删除</button></div>)}</div></div>
  </section>;
}

function Header({title,sub,action}:{title:string;sub:string;action?:React.ReactNode}){return <div className="header"><div><h1>{title}</h1><p>{sub}</p></div>{action}</div>}

createRoot(document.getElementById('root')!).render(<App/>);
