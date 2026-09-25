"use client";

import {useMemo,useState} from "react";

type Policy=Record<string,unknown>;
type Props={policy:Policy;onSave:(policy:Policy)=>Promise<void>;disabled?:boolean};

type BoolTool={key:"web"|"files"|"images"|"github"|"write_code";label:string;description:string};
const generic:BoolTool[]=[
 {key:"web",label:"Интернет",description:"Искать актуальную информацию в интернете."},
 {key:"files",label:"Файлы проекта",description:"Читать доступные файлы и использовать их как контекст."},
 {key:"images",label:"Изображения",description:"Создавать изображения через Image Studio."},
];
const dev:BoolTool[]=[
 {key:"github",label:"GitHub",description:"Читать подключённый repository через защищённую интеграцию."},
 {key:"files",label:"Файлы проекта",description:"Использовать файлы проекта как контекст."},
 {key:"write_code",label:"Изменения кода",description:"Готовить изменения кода только через Dev Studio и безопасный контур."},
];

export default function AgentToolPolicy({policy,onSave,disabled}:Props){
 const[local,setLocal]=useState<Policy>(()=>({...policy}));const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const isDev=useMemo(()=>Boolean(local.github||local.write_code||local.shell),[local]);
 const rows=isDev?dev:generic;
 const toggle=(key:string)=>setLocal(current=>({...current,[key]:!Boolean(current[key])}));
 const setApproval=(key:"publish"|"merge",enabled:boolean)=>setLocal(current=>({...current,[key]:enabled?"approval":"disabled"}));
 const save=async()=>{setBusy(true);setError("");try{const next:Policy={...local};if(isDev){next.shell=local.shell==="sandbox"?"sandbox":"disabled";next.merge=local.merge==="approval"?"approval":"disabled";}else{next.publish=local.publish==="approval"?"approval":"disabled";}await onSave(next);}catch(e){setError(e instanceof Error?e.message:"Не удалось сохранить разрешения")}finally{setBusy(false)}};
 return <div>
  <div style={{display:"grid",gap:9}}>{rows.map(item=><label key={item.key} style={{display:"grid",gridTemplateColumns:"1fr auto",gap:12,alignItems:"center",padding:"10px 0",borderBottom:"1px solid #eee",cursor:"pointer"}}><span><strong>{item.label}</strong><small style={{display:"block",opacity:.58,marginTop:3}}>{item.description}</small></span><input type="checkbox" checked={Boolean(local[item.key])} onChange={()=>toggle(item.key)} disabled={disabled||busy}/></label>)}</div>
  {!isDev&&<label style={{display:"grid",gridTemplateColumns:"1fr auto",gap:12,alignItems:"center",padding:"10px 0",borderBottom:"1px solid #eee",cursor:"pointer"}}><span><strong>Публикация</strong><small style={{display:"block",opacity:.58,marginTop:3}}>Перед внешней публикацией всегда спрашивать подтверждение пользователя.</small></span><input type="checkbox" checked={local.publish==="approval"} onChange={e=>setApproval("publish",e.target.checked)} disabled={disabled||busy}/></label>}
  {isDev&&<><label style={{display:"grid",gridTemplateColumns:"1fr auto",gap:12,alignItems:"center",padding:"10px 0",borderBottom:"1px solid #eee",cursor:"pointer"}}><span><strong>Sandbox</strong><small style={{display:"block",opacity:.58,marginTop:3}}>Команды выполняются только в изолированной среде.</small></span><input type="checkbox" checked={local.shell==="sandbox"} onChange={e=>setLocal(current=>({...current,shell:e.target.checked?"sandbox":"disabled"}))} disabled={disabled||busy}/></label><label style={{display:"grid",gridTemplateColumns:"1fr auto",gap:12,alignItems:"center",padding:"10px 0",cursor:"pointer"}}><span><strong>Commit / merge</strong><small style={{display:"block",opacity:.58,marginTop:3}}>Перед публикацией изменений требуется подтверждение.</small></span><input type="checkbox" checked={local.merge==="approval"} onChange={e=>setApproval("merge",e.target.checked)} disabled={disabled||busy}/></label></>}
  {error&&<div style={{marginTop:8,color:"#b33140",fontSize:13}}>{error}</div>}
  <button type="button" onClick={()=>void save()} disabled={disabled||busy} style={{marginTop:11,padding:"9px 12px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>{busy?"Сохраняем…":"Сохранить разрешения"}</button>
 </div>;
}
