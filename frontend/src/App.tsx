import { useEffect, useMemo, useState } from 'react';
import { Activity, Database, Settings, Trophy } from 'lucide-react';
import { probeLog } from './lib/api';
import LeaderboardPage from './pages/LeaderboardPage';
import RunsPage from './pages/RunsPage';
import TasksPage from './pages/TasksPage';
import SettingsPage from './pages/SettingsPage';

type RouteKey = 'leaderboard' | 'runs' | 'tasks' | 'settings';

const routes: Record<RouteKey, string> = {
  leaderboard: '/',
  runs: '/runs',
  tasks: '/tasks',
  settings: '/settings',
};

function routeFromPath(pathname:string):RouteKey{
  if(pathname.startsWith('/runs'))return 'runs';
  if(pathname.startsWith('/tasks'))return 'tasks';
  if(pathname.startsWith('/settings'))return 'settings';
  return 'leaderboard';
}

export function navigateTo(route:RouteKey, detail?:Record<string,unknown>){
  const url=detail?.slug&&route==='tasks'?`/tasks/${detail.slug}`:routes[route];
  window.history.pushState({},'',url);
  window.dispatchEvent(new CustomEvent('app-route-change',{detail:{route,...detail}}));
}

function visitedFromRoute(route:RouteKey):Record<RouteKey,boolean>{
  return {leaderboard:route==='leaderboard',runs:route==='runs',tasks:route==='tasks',settings:route==='settings'};
}

export default function App(){
  const initialRoute=routeFromPath(window.location.pathname);
  const [route,setRoute]=useState<RouteKey>(()=>initialRoute);
  const [visited,setVisited]=useState<Record<RouteKey,boolean>>(()=>visitedFromRoute(initialRoute));
  const activePath=useMemo(()=>routes[route],[route]);
  const setActiveRoute=(next:RouteKey, replace=false)=>{
    const start=performance.now();
    probeLog('route.click', {from:route,to:next});
    setRoute(next);
    setVisited(prev=>({...prev,[next]:true}));
    if(!replace && window.location.pathname!==routes[next]) window.history.pushState({},'',routes[next]);
    window.setTimeout(()=>probeLog('route.paint', {route:next, ms:Number((performance.now()-start).toFixed(1))}),0);
  };
  useEffect(()=>{
    const sync=()=>{const next=routeFromPath(window.location.pathname); setRoute(next); setVisited(prev=>({...prev,[next]:true}))};
    const routeHandler=(event:any)=>{const next=(event.detail?.route||routeFromPath(window.location.pathname)) as RouteKey; setRoute(next); setVisited(prev=>({...prev,[next]:true}))};
    const taskHandler=(event:any)=>{navigateTo('tasks',{slug:event.detail?.slug})};
    const modelsHandler=()=>navigateTo('runs');
    window.addEventListener('popstate',sync);
    window.addEventListener('app-route-change',routeHandler as EventListener);
    window.addEventListener('open-task-anchor',taskHandler as EventListener);
    window.addEventListener('open-models-anchor',modelsHandler as EventListener);
    sync();
    return()=>{window.removeEventListener('popstate',sync); window.removeEventListener('app-route-change',routeHandler as EventListener); window.removeEventListener('open-task-anchor',taskHandler as EventListener); window.removeEventListener('open-models-anchor',modelsHandler as EventListener)};
  },[]);
  return <div className="shell">
    <aside className="rail"><button className="brand brand-button" onClick={()=>setActiveRoute('leaderboard')} aria-label="回到首页"><img className="mark logo-mark" src="/benchmark-logo.svg" alt="Senyao's LLM Benchmark"/><div><b>Senyao's LLM Benchmark</b><small>大模型评测运行台</small></div></button>
      <button onClick={()=>setActiveRoute('leaderboard')} className={route==='leaderboard'?'on':''}><Trophy size={18}/>排行榜</button>
      <button onClick={()=>setActiveRoute('runs')} className={route==='runs'?'on':''}><Activity size={18}/>运行</button>
      <button onClick={()=>setActiveRoute('tasks')} className={route==='tasks'?'on':''}><Database size={18}/>题库</button>
      <button onClick={()=>setActiveRoute('settings')} className={route==='settings'?'on':''}><Settings size={18}/>设置</button>
    </aside>
    <main data-active-path={activePath}>
      <div hidden={route!=='leaderboard'}>{visited.leaderboard&&<LeaderboardPage/>}</div>
      <div hidden={route!=='runs'}>{visited.runs&&<RunsPage/>}</div>
      <div hidden={route!=='tasks'}>{visited.tasks&&<TasksPage/>}</div>
      <div hidden={route!=='settings'}>{visited.settings&&<SettingsPage/>}</div>
    </main>
  </div>;
}
