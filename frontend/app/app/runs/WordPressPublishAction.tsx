"use client";

import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";

type Binding={id:string;agent:string;connection:string;connection_name:string;connection_kind:string;connection_health:string;purpose:string;enabled:boolean};
type Publication={post_id:number;status:string;url:string;slug:string;connection_id:string;connection_name:string;title:string};
type Props={runId:string;agentId:string;state:string;initial?:Publication|null};

export default function WordPressPublishAction({runId,agentId,state,initial}:Props){
 const[bindings,setBindings]=useState<Binding[]>([]);const[selected,setSelected]=useState(initial?.connection_id||"");const[publication,setPublication]=useState<Publication|null>(initial||null);const[busy,setBusy]=useState("");const[error,setError]=useState("");
 useEffect(()=>{void api<Binding[]>(`/agent-connections/?agent=${encodeURIComponent(agentId)}`).then(rows=>{const wp=rows.filter(item=>item.connection_kind==="wordpress"&&item.enabled);setBindings(wp);setSelected(current=>current||wp[0]?.connection||"")}).catch(e=>setError(e instanceof Error?e.message:"Не удалось загрузить WordPress"))},[agentId]);
 const ready=useMemo(()=>bindings.filter(item=>item.connection_health==="healthy"),[bindings]);
 if(state!=="completed"&&!publication)return null;
 if(!bindings.length&&!publication)return null;
 const send=async(status:"draft"|"publish")=>{if(!selected){setError("Выберите WordPress");return}setBusy(status);setError("");try{const result=await api<{publication:Publication;created:boolean}>(`/agent-runs/${runId}/wordpress/`,{method:"POST",body:JSON.stringify({connection:selected,status})});setPublication(result.publication)}catch(e){setError(e instanceof Error?e.message:"WordPress не принял материал")}finally{setBusy("")}};
 return <section style={{border:"1px solid #ddd",borderRadius:18,padding:20,marginBottom:18}}>
  <div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>WordPress</div><h2 style={{margin:"7px 0"}}>Материал для сайта</h2>
  {publication?<><div style={{marginBottom:10}}>Пост <strong>#{publication.post_id}</strong> · {publication.status==="publish"?"опубликован":"черновик"} · {publication.connection_name}</div><div style={{display:"flex",gap:8,flexWrap:"wrap"}}>{publication.url&&<a href={publication.url} target="_blank" rel="noreferrer" style={{padding:"9px 12px",border:"1px solid #ccc",borderRadius:9,textDecoration:"none",color:"inherit"}}>Открыть в WordPress ↗</a>}{publication.status!=="publish"&&<button disabled={!!busy} onClick={()=>void send("publish")}>{busy==="publish"?"Публикуем…":"Опубликовать этот черновик"}</button>}</div></>:<><p style={{opacity:.68}}>Результат run можно сохранить в WordPress как черновик или опубликовать по вашему явному действию.</p>{bindings.length>1&&<select value={selected} onChange={e=>setSelected(e.target.value)} style={{padding:9,border:"1px solid #ccc",borderRadius:9,marginBottom:9}}>{bindings.map(item=><option key={item.id} value={item.connection} disabled={item.connection_health!=="healthy"}>{item.connection_name}{item.connection_health!=="healthy"?" · не проверено":""}</option>)}</select>}<div style={{display:"flex",gap:8,flexWrap:"wrap"}}><button disabled={!!busy||!selected||!ready.length} onClick={()=>void send("draft")}>{busy==="draft"?"Сохраняем…":"Сохранить черновик"}</button><button disabled={!!busy||!selected||!ready.length} onClick={()=>void send("publish")}>{busy==="publish"?"Публикуем…":"Опубликовать"}</button></div></>}
  {error&&<div style={{marginTop:10,color:"#b33140"}}>{error}</div>}
 </section>;
}
