"use client";

import {useState} from "react";
import {api} from "../../../lib/api";

type PullRequest={number:number;html_url:string;head:string;head_sha:string;base:string;state:string;draft?:boolean;existing?:boolean};
type Props={runId:string;state:string;workingBranch?:string;initial?:PullRequest|null};

export default function DevPullRequestAction({runId,state,workingBranch,initial}:Props){
 const[pull,setPull]=useState<PullRequest|null>(initial||null);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 if(!workingBranch)return null;
 const create=async()=>{setBusy(true);setError("");try{const result=await api<{pull_request:PullRequest;created:boolean}>(`/agent-runs/${runId}/pull-request/`,{method:"POST"});setPull(result.pull_request)}catch(e){setError(e instanceof Error?e.message:"Не удалось создать Pull Request")}finally{setBusy(false)}};
 return <section style={{border:"1px solid #ddd",borderRadius:18,padding:20,marginBottom:18}}>
  <div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>GitHub · Review</div><h2 style={{margin:"7px 0"}}>Рабочая ветка готова</h2><div style={{fontFamily:"ui-monospace, monospace",fontSize:13,overflowWrap:"anywhere",marginBottom:12}}>{workingBranch}</div>
  {pull?<div><div style={{marginBottom:10}}>Draft Pull Request <strong>#{pull.number}</strong> создан. Merge в default branch автоматически не выполняется.</div><a href={pull.html_url} target="_blank" rel="noreferrer" style={{display:"inline-block",padding:"10px 13px",border:"1px solid #ccc",borderRadius:10,textDecoration:"none",color:"inherit"}}>Открыть Pull Request ↗</a></div>:state==="completed"?<><p style={{opacity:.68}}>QA/Security и Final Review завершены. Создание PR выполняется только по вашему явному действию.</p><button disabled={busy} onClick={()=>void create()} style={{padding:"10px 14px",border:0,borderRadius:10,fontWeight:700}}>{busy?"Создаём…":"Создать draft Pull Request"}</button></>:<p style={{opacity:.68}}>Pull Request станет доступен после успешного завершения QA/Security и Final Review.</p>}
  {error&&<div style={{marginTop:10,color:"#b33140"}}>{error}</div>}
 </section>;
}
