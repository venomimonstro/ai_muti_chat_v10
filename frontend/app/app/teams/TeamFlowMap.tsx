"use client";

type Member={id:string;agent:string;agent_name:string;role:string;priority:number;can_delegate:boolean;enabled:boolean};
type Props={members:Member[];director:string;compact?:boolean};

export default function TeamFlowMap({members,director,compact=false}:Props){
 const ordered=[...members].filter(item=>item.enabled).sort((a,b)=>a.priority-b.priority||a.role.localeCompare(b.role,"ru"));
 if(!ordered.length)return null;
 return <div style={{overflowX:"auto",paddingBottom:3}}>
  <div style={{display:"flex",alignItems:"stretch",gap:7,minWidth:"max-content"}}>{ordered.map((member,index)=><div key={member.id} style={{display:"flex",alignItems:"center",gap:7}}>
   {index>0&&<div aria-hidden="true" style={{opacity:.42,fontSize:18}}>→</div>}
   <div style={{minWidth:compact?135:165,maxWidth:compact?190:230,border:`1px solid ${member.agent===director?"#aaa":"#ddd"}`,borderRadius:13,padding:compact?"9px 10px":"11px 12px",background:member.agent===director?"rgba(127,127,127,.08)":"transparent"}}>
    <div style={{fontSize:11,opacity:.55,textTransform:"uppercase"}}>{member.agent===director?"Руководитель":"Специалист"}</div>
    <strong style={{display:"block",fontSize:compact?13:14,marginTop:3}}>{member.role}</strong>
    {!compact&&<div style={{fontSize:12,opacity:.6,marginTop:3}}>{member.agent_name}</div>}
    {member.can_delegate&&<div style={{fontSize:11,opacity:.58,marginTop:4}}>может делегировать</div>}
   </div>
  </div>)}</div>
 </div>;
}
