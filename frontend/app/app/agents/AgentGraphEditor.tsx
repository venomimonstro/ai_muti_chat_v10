"use client";

import {useMemo,useState} from "react";
import AgentGraphMap from "./AgentGraphMap";

type PublishStatus="draft"|"publish";
type ConditionSource="previous_text"|"objective";
type ConditionOperator="contains"|"not_contains"|"is_empty"|"not_empty";
type Node={
 id:string;title:string;type:string;status?:PublishStatus;prompt?:string;post_title?:string;slug?:string;
 condition_source?:ConditionSource;operator?:ConditionOperator;value?:string;on_true?:string;on_false?:string;
 notification_title?:string;message?:string;wait_minutes?:number;
};
type Graph={version?:number;nodes?:Node[];edges?:Array<{from:string;to:string}>};
type Props={graph:Graph;disabled?:boolean;onSave:(graph:Graph)=>Promise<void>};

const types=[
 ["llm","AI-задача"],
 ["research","Исследование"],
 ["web","Интернет"],
 ["files","Файлы проекта"],
 ["image","Изображение"],
 ["review","Проверка"],
 ["analytics","Аналитика"],
 ["condition","Условие"],
 ["approval","Подтверждение пользователя"],
 ["wait","Подождать"],
 ["notify","Уведомить пользователя"],
 ["publish","Публикация"],
 ["finish","Завершить workflow"],
] as const;
const allowedTypes=new Set(types.map(([value])=>value));
const waitOptions=[[5,"5 минут"],[30,"30 минут"],[60,"1 час"],[360,"6 часов"],[1440,"1 день"]] as const;

const normalize=(nodes:Node[],version=1):Graph=>({
 version,
 nodes,
 edges:nodes.slice(0,-1).map((node,index)=>({from:node.id,to:nodes[index+1].id})),
});

const safeId=(title:string,index:number)=>{
 const base=title.toLowerCase().replace(/[^a-zа-яё0-9]+/gi,"-").replace(/^-|-$/g,"").slice(0,48);
 return `${base||"step"}-${Date.now().toString(36)}-${index}`;
};

export default function AgentGraphEditor({graph,disabled,onSave}:Props){
 const initial=useMemo(()=>((graph.nodes||[]) as Node[]).map(item=>({...item,status:item.type==="publish"?(item.status||"draft"):item.status})),[graph]);
 const[nodes,setNodes]=useState<Node[]>(initial);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const update=(index:number,patch:Partial<Node>)=>setNodes(current=>current.map((item,i)=>i===index?{...item,...patch}:item));
 const changeType=(index:number,type:string)=>setNodes(current=>current.map((item,i)=>i===index?{
  ...item,type,
  status:type==="publish"?(item.status||"draft"):undefined,
  condition_source:type==="condition"?(item.condition_source||"previous_text"):undefined,
  operator:type==="condition"?(item.operator||"contains"):undefined,
  value:type==="condition"?(item.value||""):undefined,
  on_true:type==="condition"?item.on_true:undefined,
  on_false:type==="condition"?item.on_false:undefined,
  wait_minutes:type==="wait"?(item.wait_minutes||60):undefined,
 }:item));
 const move=(index:number,delta:number)=>setNodes(current=>{const target=index+delta;if(target<0||target>=current.length)return current;const next=[...current];[next[index],next[target]]=[next[target],next[index]];return next});
 const remove=(index:number)=>setNodes(current=>{const removed=current[index]?.id;return current.filter((_,i)=>i!==index).map(node=>({...node,on_true:node.on_true===removed?undefined:node.on_true,on_false:node.on_false===removed?undefined:node.on_false}))});
 const add=()=>setNodes(current=>[...current,{id:safeId("Новый шаг",current.length),title:"Новый шаг",type:"llm"}]);
 const save=async()=>{setBusy(true);setError("");try{const cleaned=nodes.map((node,index)=>{const type=allowedTypes.has(node.type as typeof types[number][0])?node.type:"llm";const next:Node={...node,id:node.id||safeId(node.title,index),title:node.title.trim()||`Шаг ${index+1}`,type};if(type==="publish")next.status=node.status==="publish"?"publish":"draft";else delete next.status;if(type!=="condition"){delete next.condition_source;delete next.operator;delete next.value;delete next.on_true;delete next.on_false;}if(type!=="notify"){delete next.notification_title;delete next.message;}if(type!=="wait")delete next.wait_minutes;return next});await onSave(normalize(cleaned,Number(graph.version||1)+1));}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось сохранить карту")}finally{setBusy(false)}};
 const targets=(currentId:string)=>nodes.filter(item=>item.id!==currentId);
 return <div>
  <div style={{padding:"10px 12px",border:"1px solid #e2e2e2",borderRadius:11,marginBottom:12,fontSize:13,opacity:.72}}>Карта исполняется сервером. Условие меняет маршрут, «Подождать» переживает перезапуск worker, уведомление создаётся в AI Workspace. GitHub, код и sandbox настраиваются отдельно в Dev Studio.</div>
  <div style={{border:"1px solid #ececec",borderRadius:16,padding:14,marginBottom:14}}><div style={{fontSize:12,fontWeight:700,opacity:.55,marginBottom:10}}>ВИЗУАЛЬНАЯ КАРТА</div><AgentGraphMap nodes={nodes}/></div>
  <div style={{display:"grid",gap:10}}>{nodes.map((node,index)=><div key={node.id} style={{border:"1px solid #ddd",borderRadius:13,padding:10}}>
   <div style={{display:"grid",gridTemplateColumns:"36px minmax(0,1fr) 190px auto",gap:8,alignItems:"center"}}>
    <div style={{width:30,height:30,borderRadius:999,border:"1px solid #bbb",display:"grid",placeItems:"center",fontWeight:700}}>{index+1}</div>
    <input aria-label={`Название шага ${index+1}`} value={node.title} onChange={event=>update(index,{title:event.target.value})} disabled={disabled||busy} style={{minWidth:0,padding:9,border:"1px solid #ccc",borderRadius:9}}/>
    <select aria-label={`Тип шага ${index+1}`} value={allowedTypes.has(node.type as typeof types[number][0])?node.type:"llm"} onChange={event=>changeType(index,event.target.value)} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}>{types.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
    <div style={{display:"flex",gap:5}}><button type="button" onClick={()=>move(index,-1)} disabled={disabled||busy||index===0} aria-label="Поднять шаг">↑</button><button type="button" onClick={()=>move(index,1)} disabled={disabled||busy||index===nodes.length-1} aria-label="Опустить шаг">↓</button><button type="button" onClick={()=>remove(index)} disabled={disabled||busy||nodes.length<=1} aria-label="Удалить шаг">×</button></div>
   </div>
   {node.type==="condition"&&<div style={{display:"grid",gridTemplateColumns:"repeat(2,minmax(180px,1fr))",gap:10,margin:"10px 0 0 44px"}}>
    <label style={{display:"grid",gap:5,fontSize:13}}>Что проверять<select value={node.condition_source||"previous_text"} onChange={event=>update(index,{condition_source:event.target.value as ConditionSource})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="previous_text">Результат предыдущих шагов</option><option value="objective">Исходную задачу</option></select></label>
    <label style={{display:"grid",gap:5,fontSize:13}}>Условие<select value={node.operator||"contains"} onChange={event=>update(index,{operator:event.target.value as ConditionOperator})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="contains">Содержит текст</option><option value="not_contains">Не содержит текст</option><option value="is_empty">Пусто</option><option value="not_empty">Не пусто</option></select></label>
    {!(["is_empty","not_empty"] as string[]).includes(node.operator||"contains")&&<label style={{display:"grid",gap:5,fontSize:13,gridColumn:"1 / -1"}}>Текст для проверки<input value={node.value||""} onChange={event=>update(index,{value:event.target.value})} placeholder="Например: одобрено" disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}/></label>}
    <label style={{display:"grid",gap:5,fontSize:13}}>Если Да<select value={node.on_true||""} onChange={event=>update(index,{on_true:event.target.value||undefined})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="">Следующий шаг</option>{targets(node.id).map(target=><option key={target.id} value={target.id}>{target.title}</option>)}</select></label>
    <label style={{display:"grid",gap:5,fontSize:13}}>Если Нет<select value={node.on_false||""} onChange={event=>update(index,{on_false:event.target.value||undefined})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="">Следующий шаг</option>{targets(node.id).map(target=><option key={target.id} value={target.id}>{target.title}</option>)}</select></label>
   </div>}
   {node.type==="wait"&&<div style={{display:"grid",gridTemplateColumns:"minmax(180px,.7fr) minmax(220px,1.3fr)",gap:10,margin:"10px 0 0 44px"}}><label style={{display:"grid",gap:5,fontSize:13}}>Сколько ждать<select value={waitOptions.some(([value])=>value===(node.wait_minutes||60))?node.wait_minutes||60:"custom"} onChange={event=>event.target.value!=="custom"&&update(index,{wait_minutes:Number(event.target.value)})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}>{waitOptions.map(([value,label])=><option key={value} value={value}>{label}</option>)}<option value="custom">Своё значение</option></select></label><label style={{display:"grid",gap:5,fontSize:13}}>Минуты<input type="number" min={1} max={10080} value={node.wait_minutes||60} onChange={event=>update(index,{wait_minutes:Math.max(1,Math.min(10080,Number(event.target.value)||1))})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}/><span style={{fontSize:11,opacity:.58}}>От 1 минуты до 7 дней. Worker во время ожидания не занят.</span></label></div>}
   {node.type==="notify"&&<div style={{display:"grid",gap:9,margin:"10px 0 0 44px"}}><label style={{display:"grid",gap:5,fontSize:13}}>Заголовок уведомления<input value={node.notification_title||""} onChange={event=>update(index,{notification_title:event.target.value})} placeholder="Например: Пост готов к проверке" disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}/></label><label style={{display:"grid",gap:5,fontSize:13}}>Сообщение<textarea rows={2} value={node.message||""} onChange={event=>update(index,{message:event.target.value})} placeholder="Можно оставить пустым — будет использован результат предыдущего шага" disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9,resize:"vertical"}}/></label></div>}
   {node.type==="finish"&&<div style={{fontSize:12,opacity:.65,margin:"10px 0 0 44px"}}>После этого блока workflow завершается успешно. Шаги ниже по этой ветке не выполняются.</div>}
   {node.type==="publish"&&<div style={{display:"grid",gridTemplateColumns:"minmax(180px,.8fr) minmax(220px,1.2fr)",gap:10,margin:"10px 0 0 44px"}}><label style={{display:"grid",gap:5,fontSize:13}}>Что сделать в WordPress<select value={node.status||"draft"} onChange={event=>update(index,{status:event.target.value as PublishStatus})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="draft">Сохранить как черновик</option><option value="publish">Опубликовать сразу</option></select></label><div style={{fontSize:12,opacity:.65,alignSelf:"end",paddingBottom:8}}>{(node.status||"draft")==="publish"?"Материал станет публичным после выполнения policy/подтверждения.":"Безопасный режим: материал появится в WordPress как черновик."}</div></div>}
  </div>)}</div>
  {error&&<div style={{marginTop:10,color:"#b33140"}}>{error}</div>}
  <div style={{display:"flex",justifyContent:"space-between",gap:10,marginTop:12}}><button type="button" onClick={add} disabled={disabled||busy} style={{padding:"9px 12px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>+ Добавить шаг</button><button type="button" onClick={()=>void save()} disabled={disabled||busy||!nodes.length} style={{padding:"9px 13px",border:0,borderRadius:9,fontWeight:700}}>{busy?"Сохраняем…":"Сохранить карту"}</button></div>
 </div>;
}
