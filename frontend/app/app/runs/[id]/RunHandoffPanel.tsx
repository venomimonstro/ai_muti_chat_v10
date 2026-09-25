"use client";

import {useParams} from "next/navigation";
import {useEffect,useState} from "react";
import {api} from "../../../../lib/api";

type Handoff={id:string;from_agent_name:string;to_agent_name:string;task:string;created_at:string;completed_at:string|null;result?:{text?:string}};
type Run={id:string;team:string|null;handoffs?:Handoff[]};

export default function RunHandoffPanel(){
 const params=useParams<{id:string}>();
 const id=String(params.id||"");
 const[run,setRun]=useState<Run|null>(null);
 useEffect(()=>{let active=true;api<Run>(`/agent-runs/${id}/`).then(data=>{if(active)setRun(data)}).catch(()=>{});return()=>{active=false}},[id]);
 const handoffs=run?.handoffs||[];
 if(!run?.team||!handoffs.length)return null;
 return <section style={{maxWidth:1080,margin:"18px auto -8px",padding:"0 20px"}}>
  <div style={{border:"1px solid #ddd",borderRadius:16,padding:15}}>
   <div style={{fontSize:12,opacity:.55,textTransform:"uppercase",marginBottom:9}}>Передачи внутри команды</div>
   <div style={{display:"grid",gap:8}}>{handoffs.map((item,index)=><div key={item.id} style={{display:"grid",gridTemplateColumns:"minmax(120px,.8fr) 30px minmax(120px,.8fr) minmax(180px,1.4fr)",gap:8,alignItems:"center",fontSize:13}}>
    <strong>{item.from_agent_name}</strong><span style={{textAlign:"center",opacity:.55}}>→</span><strong>{item.to_agent_name}</strong><div style={{opacity:.68}}>{item.task.replace(/^team-member-\d+:\s*/,"")}{item.completed_at?` · ${new Date(item.completed_at).toLocaleString("ru-RU")}`:""}</div>
   </div>)}</div>
  </div>
 </section>;
}
