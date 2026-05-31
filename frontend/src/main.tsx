import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactECharts from 'echarts-for-react';
import { Copy, Eye, EyeOff, RefreshCw, Settings, Trophy, Database, Activity } from 'lucide-react';
import './styles.css';

const api = async (path: string, init?: RequestInit) => {
  const res = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...init });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
};

type Leaderboard = { dimensions: string[]; rows: Array<{model_id:number; model:string; provider:string; overall:number|null; dimensions:Record<string,number|null>; coverage:{current:number; total:number; status:string}}> };
type Task = {id:number; slug:string; title:string; category:string; dimension:string; evaluator_type:string; content_hash:string; active:boolean; covered_models:number; total_models:number};
type Provider = {id:number; name:string; api_base:string; api_key_saved:boolean; api_key_fingerprint:string; enabled:boolean; notes:string};
type Model = {id:number; provider_id:number; display_name:string; model_id:string; enabled:boolean};

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
  </div>
}

function LeaderboardPage(){
  const [data,setData]=useState<Leaderboard>({dimensions:[],rows:[]});
  const [query,setQuery]=useState('');
  const load=()=>api('/leaderboard').then(setData);
  useEffect(()=>{load()},[]);
  const rows=data.rows.filter(r=>r.model.toLowerCase().includes(query.toLowerCase())||r.provider.toLowerCase().includes(query.toLowerCase()));
  const chartOption=useMemo(()=>({tooltip:{},radar:{indicator:data.dimensions.map(d=>({name:d,max:100}))},series:rows.slice(0,5).map(r=>({type:'radar',name:r.model,data:[data.dimensions.map(d=>r.dimensions[d]??0)]}))}),[data,rows]);
  return <section><Header title="排行榜" sub="横轴为能力维度，纵轴为模型；支持筛选、完整性状态与审计入口。" action={<button onClick={load}><RefreshCw size={16}/>刷新</button>}/>
    <div className="toolbar"><input placeholder="筛选模型/供应商" value={query} onChange={e=>setQuery(e.target.value)}/><span>{rows.length} models</span></div>
    <div className="matrix"><table><thead><tr><th>模型</th><th>供应商</th><th>总分</th>{data.dimensions.map(d=><th key={d}>{d}</th>)}<th>覆盖</th></tr></thead><tbody>{rows.map(r=><tr key={r.model_id}><td><b>{r.model}</b></td><td>{r.provider}</td><td className="score">{r.overall??'—'}</td>{data.dimensions.map(d=><td key={d}>{r.dimensions[d]??'—'}</td>)}<td><span className={r.coverage.status==='complete'?'pill ok':'pill warn'}>{r.coverage.current}/{r.coverage.total}</span></td></tr>)}</tbody></table></div>
    <div className="panel"><h3>Top 模型能力雷达</h3><ReactECharts option={chartOption} style={{height:360}}/></div>
  </section>
}

function ModelsPage(){
  const [models,setModels]=useState<Model[]>([]); const [selected,setSelected]=useState<number|null>(null); const [results,setResults]=useState<any[]>([]);
  useEffect(()=>{api('/models').then(setModels)},[]);
  const loadRuns=async(m:Model)=>{setSelected(m.id); const runs=await api('/runs'); const run=runs.find((x:any)=>x.model_id===m.id); setResults(run? await api(`/runs/${run.run_id}/results`):[])};
  return <section><Header title="模型审计" sub="每个模型保留具体题目回答、分数与裁判理由；原始信息默认折叠。" />
    <div className="split"><div className="list">{models.map(m=><button className={selected===m.id?'row on':'row'} onClick={()=>loadRuns(m)} key={m.id}>{m.display_name}<small>{m.model_id}</small></button>)}</div>
    <div className="panel grow">{results.length?results.map((r,i)=><details key={i} className="audit"><summary>题目 #{r.task_id} · {r.status} · 分数 {r.score??'—'}</summary><h4>裁判理由</h4><p>{r.judge_reason||'—'}</p><h4>模型回答</h4><pre>{r.response||r.error||'—'}</pre></details>):<p className="muted">选择模型查看最近一次运行详情。</p>}</div></div>
  </section>
}

function TasksPage(){
  const [tasks,setTasks]=useState<Task[]>([]); const load=()=>api('/tasks').then(setTasks);
  useEffect(()=>{load()},[]);
  const sync=async()=>{await api('/tasks/sync',{method:'POST'}); load()};
  return <section><Header title="题库" sub="题目由 YAML 维护；hash 变化会标记已有模型需要重跑该题。" action={<button onClick={sync}>同步题库</button>}/>
  <div className="matrix"><table><thead><tr><th>ID</th><th>标题</th><th>维度</th><th>评估</th><th>Hash</th><th>覆盖</th></tr></thead><tbody>{tasks.map(t=><tr key={t.slug}><td>{t.slug}</td><td>{t.title}</td><td>{t.dimension}</td><td>{t.evaluator_type}</td><td><code>{t.content_hash.slice(0,10)}</code></td><td>{t.covered_models}/{t.total_models}</td></tr>)}</tbody></table></div></section>
}

function SettingsPage(){
  const [providers,setProviders]=useState<Provider[]>([]); const [models,setModels]=useState<Model[]>([]); const [show,setShow]=useState(false); const [form,setForm]=useState({name:'',api_base:'',api_key:''});
  const load=()=>{api('/providers').then(setProviders); api('/models').then(setModels)}; useEffect(load,[]);
  const save=async()=>{await api('/providers',{method:'POST',body:JSON.stringify(form)}); setForm({name:'',api_base:'',api_key:''}); load()};
  const copy=async(v:string)=>navigator.clipboard.writeText(v);
  return <section><Header title="设置" sub="管理 OpenAI-compatible 供应商、模型与裁判模型。密钥仅加密保存，后端不回显明文。" />
    <div className="settings-grid"><div className="panel"><h3>添加供应商</h3><input placeholder="名称" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/><input placeholder="API Endpoint" value={form.api_base} onChange={e=>setForm({...form,api_base:e.target.value})}/><div className="secret"><input type={show?'text':'password'} placeholder="API Key" value={form.api_key} onChange={e=>setForm({...form,api_key:e.target.value})}/><button onClick={()=>setShow(!show)}>{show?<EyeOff/>:<Eye/>}</button><button onClick={()=>copy(form.api_key)}><Copy/></button></div><button onClick={save}>保存供应商</button></div>
    <div className="panel"><h3>供应商</h3>{providers.map(p=><div className="provider" key={p.id}><b>{p.name}</b><small>{p.api_base}</small><span>Key 指纹：{p.api_key_fingerprint||'未保存'}</span></div>)}</div>
    <div className="panel"><h3>模型</h3>{models.map(m=><div className="provider" key={m.id}><b>{m.display_name}</b><small>{m.model_id}</small></div>)}<p className="muted">模型/裁判新增 API 已预留，下一步会补齐表单。</p></div></div>
  </section>
}

function Header({title,sub,action}:{title:string;sub:string;action?:React.ReactNode}){return <div className="header"><div><h1>{title}</h1><p>{sub}</p></div>{action}</div>}

createRoot(document.getElementById('root')!).render(<App/>);
