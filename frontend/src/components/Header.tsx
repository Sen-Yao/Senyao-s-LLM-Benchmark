import React from 'react';

export default function Header({title,sub,action}:{title:string;sub:string;action?:React.ReactNode}){
  return <div className="header"><div><h1>{title}</h1><p>{sub}</p></div>{action}</div>;
}
