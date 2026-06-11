import { useEffect, useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { RefreshCw } from 'lucide-react';
import Header from '../components/Header';
import { api } from '../lib/api';
import type { Leaderboard } from '../lib/types';
import { dimensionLabel } from '../lib/utils';

export default function LeaderboardPage(){
  const [data,setData]=useState<Leaderboard>({dimensions:[],rows:[]});
  const [query,setQuery]=useState(''); const [onlyComplete,setOnlyComplete]=useState(false); const [sort,setSort]=useState('overall');
  const load=()=>api('/leaderboard').then(setData);
  useEffect(()=>{load()},[]);
  useEffect(()=>{const handler=()=>load(); window.addEventListener('leaderboard-changed',handler as EventListener); window.addEventListener('models-changed',handler as EventListener); return()=>{window.removeEventListener('leaderboard-changed',handler as EventListener); window.removeEventListener('models-changed',handler as EventListener)}},[]);
  const rows=data.rows.filter(r=>r.model.toLowerCase().includes(query.toLowerCase())&&(!onlyComplete||r.coverage.status==='complete')).sort((a,b)=>((sort==='overall'?b.overall:b.dimensions[sort])??-1)-((sort==='overall'?a.overall:a.dimensions[sort])??-1));
  const chartOption=useMemo(()=>({tooltip:{},legend:{top:0},radar:{indicator:data.dimensions.map(d=>({name:dimensionLabel(d),max:100}))},series:[{type:'radar',data:rows.slice(0,5).map(r=>({name:r.model,value:data.dimensions.map(d=>r.dimensions[d]??0)}))}]}),[data,rows]);
  return <section><Header title="排行榜" sub="能力矩阵 + 完整性筛选。Partial/Stale 模型不会被静默混进同一结论。" action={<button onClick={load}><RefreshCw size={16}/>刷新</button>}/>
    <div className="toolbar"><input placeholder="筛选模型" value={query} onChange={e=>setQuery(e.target.value)}/><select value={sort} onChange={e=>setSort(e.target.value)}><option value="overall">总分</option>{data.dimensions.map(d=><option key={d} value={d}>{dimensionLabel(d)}</option>)}</select><label><input type="checkbox" checked={onlyComplete} onChange={e=>setOnlyComplete(e.target.checked)}/> 只看完整评测</label><span>{rows.length} models</span></div>
    <div className="matrix"><table><thead><tr><th>模型</th><th>总分</th>{data.dimensions.map(d=><th key={d}>{dimensionLabel(d)}</th>)}<th>覆盖</th></tr></thead><tbody>{rows.map(r=><tr key={r.model_id}><td><b>{r.model}</b></td><td className="score">{r.overall??'—'}</td>{data.dimensions.map(d=><td key={d}>{r.dimensions[d]??'—'}</td>)}<td><span className={r.coverage.status==='complete'?'pill ok':'pill warn'}>{r.coverage.status} · {r.coverage.current}/{r.coverage.total}</span></td></tr>)}</tbody></table></div>
    <div className="panel"><h3>Top 模型能力雷达</h3><ReactECharts option={chartOption} style={{height:360}}/></div>
  </section>;
}
