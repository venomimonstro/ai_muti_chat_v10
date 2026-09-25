"use client";

import {useParams,useRouter} from "next/navigation";
import {useEffect,useState} from "react";
import {api} from "../../../../lib/api";

type Run={id:string;state:string;error_code?:string;error_message?:string};

const terminal=new Set(["completed","failed","canceled","budget_exceeded"]);
const label:Record<string,string>={
 completed:"Задача завершена. Её можно запустить ещё раз как новый отдельный запуск.",
 failed:"Задача остановилась с ошибкой. Повтор создаст новый запуск и сохранит текущую историю расходов.",
 canceled:"Запуск отменён. Повтор не изменит старую историю и создаст новую попытку.",
 budget_exceeded:"Задача остановлена лимитом бюджета. После изменения лимита можно создать новый запуск.",
};

export default function RunRecoveryBar(){
 const params=useParams<{id:string}>();
 const router=useRouter();
 const id=String(params.id||"");
 const[run,setRun]=useState<Run|null>(null);
 const[busy,setBusy]=useState(false);
 const[error,setError]=useState("");
 useEffect(()=>{let active=true;api<Run>(`/agent-runs/${id}/`).then(data=>{if(active)setRun(data)}).catch(()=>{});return()=>{active=false}},[id]);
 if(!run||!terminal.has(run.state))return null;
 const repeat=async()=>{if(busy)return;setBusy(true);setError("");try{const next=await api<Run>(`/agent-runs/${id}/repeat/`,{method:"POST",body:"{}"});router.push(`/app/runs/${next.id}`)}catch(e){setError(e instanceof Error?e.message:"Не удалось повторить запуск");setBusy(false)}};
 return <div style={{maxWidth:1080,margin:"18px auto -12px",padding:"0 20px"}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:14,alignItems:"center",padding:"12px 14px",border:"1px solid #ddd",borderRadius:14,background:"rgba(127,127,127,.05)"}}>
   <div><strong>Следующее действие</strong><div style={{fontSize:13,opacity:.65,marginTop:3}}>{label[run.state]}</div>{error&&<div style={{fontSize:13,marginTop:5}}>{error}</div>}</div>
   <button disabled={busy} onClick={()=>void repeat()} style={{padding:"9px 13px",border:0,borderRadius:10,fontWeight:700,whiteSpace:"nowrap"}}>{busy?"Создаём…":"Повторить задачу"}</button>
  </div>
 </div>;
}
