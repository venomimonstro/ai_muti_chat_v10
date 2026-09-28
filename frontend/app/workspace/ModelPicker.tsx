"use client";

import {useEffect,useMemo,useRef,useState} from "react";
import type {AIModel} from "../../lib/types";
import {Icon} from "./Icons";

type Props={value:string;models:AIModel[];disabled?:boolean;onChange:(value:string)=>void};

const modes=[
 {value:"auto:economy",label:"Простой",hint:"Быстро и экономно",icon:"zap" as const},
 {value:"auto:balanced",label:"Средний",hint:"Лучший баланс",icon:"spark" as const},
 {value:"auto:maximum",label:"Сложный",hint:"Анализ и сложные задачи",icon:"brain" as const},
];

function modelLabel(model:AIModel){
 const haystack=`${model.provider} ${model.slug} ${model.display_name}`.toLowerCase();
 if(haystack.includes("giga")){
  if(haystack.includes("max"))return "Системная Продвинутая";
  if(haystack.includes("pro"))return "Системная Расширенная";
  return "Системная Базовая";
 }
 return model.display_name;
}

function providerLabel(model:AIModel){
 const value=model.provider.trim();
 if(value.toLowerCase().includes("giga"))return "Системные модели";
 return value||"Другие модели";
}

export function ModelPicker({value,models,disabled=false,onChange}:Props){
 const[open,setOpen]=useState(false);const root=useRef<HTMLDivElement|null>(null);
 const groups=useMemo(()=>{
  const map=new Map<string,AIModel[]>();
  for(const model of models){const key=providerLabel(model);map.set(key,[...(map.get(key)??[]),model])}
  return Array.from(map.entries()).sort(([a],[b])=>a.localeCompare(b,"ru"));
 },[models]);
 const currentMode=modes.find(item=>item.value===value);
 const currentModel=!currentMode&&value.startsWith("model:")?models.find(item=>`model:${item.slug}`===value):undefined;
 const label=currentMode?.label??(currentModel?modelLabel(currentModel):"Средний");
 const sub=currentMode?.hint??(currentModel?providerLabel(currentModel):"Лучший баланс");
 useEffect(()=>{
  if(!open)return;
  const outside=(event:MouseEvent)=>{if(root.current&&!root.current.contains(event.target as Node))setOpen(false)};
  const key=(event:KeyboardEvent)=>{if(event.key==="Escape")setOpen(false)};
  document.addEventListener("mousedown",outside);window.addEventListener("keydown",key);
  return()=>{document.removeEventListener("mousedown",outside);window.removeEventListener("keydown",key)};
 },[open]);
 const choose=(next:string)=>{if(disabled)return;onChange(next);setOpen(false)};
 return <div className="modelPicker" ref={root}>
  <button type="button" className="modelPickerTrigger" disabled={disabled} onClick={()=>setOpen(v=>!v)} aria-haspopup="dialog" aria-expanded={open} title="Выбрать сложность или конкретную модель">
   <span className="modelPickerMark"><Icon name={currentMode?.icon??"brain"} size={14}/></span>
   <span className="modelPickerCurrent"><b>{label}</b><small>{sub}</small></span>
   <Icon name="chevron" size={13}/>
  </button>
  {open&&<div className="modelPickerMenu" role="dialog" aria-label="Выбор режима нейросети">
   <div className="modelPickerHead"><div><b>Как отвечать?</b><span>Выберите уровень — или конкретную модель.</span></div><button type="button" onClick={()=>setOpen(false)} aria-label="Закрыть"><Icon name="x" size={15}/></button></div>
   <div className="modelModeGrid">{modes.map(item=><button type="button" key={item.value} className={value===item.value?"active":""} onClick={()=>choose(item.value)}><span><Icon name={item.icon} size={16}/></span><b>{item.label}</b><small>{item.hint}</small>{value===item.value&&<Icon name="check" size={15}/>}</button>)}</div>
   <div className="modelPickerDivider"><span>Конкретная модель</span></div>
   <div className="modelPickerModels">{groups.length?groups.map(([provider,items])=><section key={provider}><h4>{provider}</h4>{items.map(model=>{const selected=value===`model:${model.slug}`;return <button type="button" key={model.slug} disabled={!model.available} className={selected?"active":""} onClick={()=>choose(`model:${model.slug}`)}><span className="modelDot"/><span className="modelCopy"><b>{modelLabel(model)}</b><small>{model.available?"Доступна":`Недоступна · ${model.health_state||"проверьте подключение"}`}</small></span>{selected&&<Icon name="check" size={15}/>}</button>})}</section>):<div className="modelPickerEmpty">Модели ещё загружаются…</div>}</div>
  </div>}
 </div>;
}
