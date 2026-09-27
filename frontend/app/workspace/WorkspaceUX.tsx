"use client";

import {useEffect,useMemo,useRef,useState} from "react";
import {createPortal} from "react-dom";
import {api} from "../../lib/api";
import type {AIModel} from "../../lib/types";
import WorkspaceV2 from "./WorkspaceV2";
import {Icon} from "./Icons";
import styles from "./WorkspaceUX.module.css";

type ModeValue="auto:economy"|"auto:balanced"|"auto:maximum";

type ModeOption={
 value:ModeValue;
 title:string;
 description:string;
 badge:string;
 icon:"zap"|"scale"|"brain";
};

const MODES:ModeOption[]=[
 {value:"auto:economy",title:"Простой",description:"Быстрые ответы для повседневных задач",badge:"Быстрее",icon:"zap"},
 {value:"auto:balanced",title:"Средний",description:"Баланс качества, скорости и стоимости",badge:"Оптимально",icon:"scale"},
 {value:"auto:maximum",title:"Сложный",description:"Максимум качества для анализа, кода и исследований",badge:"Максимум качества",icon:"brain"},
];

const normalize=(value:string)=>value.trim().toLowerCase();
const isSystemModel=(model:AIModel)=>{
 const haystack=normalize(`${model.provider} ${model.slug} ${model.display_name} ${model.exact_api_id}`);
 return haystack.includes("gigachat")||haystack.includes("giga-chat")||haystack.includes("sber");
};
const systemModelName=(model:AIModel)=>{
 const haystack=normalize(`${model.slug} ${model.display_name} ${model.exact_api_id}`);
 if(/(^|[-_\s])(max|ultra)([-_\s]|$)/.test(haystack))return "Системная Продвинутая";
 if(/(^|[-_\s])(pro|plus)([-_\s]|$)/.test(haystack))return "Системная Расширенная";
 return "Системная Базовая";
};
const providerName=(provider:string)=>{
 const value=normalize(provider);
 if(value.includes("openai"))return "OpenAI";
 if(value.includes("anthropic"))return "Anthropic";
 if(value.includes("deepseek"))return "DeepSeek";
 if(value.includes("google"))return "Google";
 if(value.includes("yandex"))return "Yandex";
 if(value.includes("mistral"))return "Mistral";
 if(value.includes("qwen")||value.includes("alibaba"))return "Qwen";
 return provider||"Другие модели";
};
const modelLabel=(model:AIModel)=>isSystemModel(model)?systemModelName(model):model.display_name;
const nativeSelector=()=>document.querySelector<HTMLSelectElement>(".chatHeader .headerControls > .selectControl:not(.projectSelect) select");

function ModelPicker({models}:{models:AIModel[]}){
 const[open,setOpen]=useState(false);
 const[current,setCurrent]=useState<string>("auto:balanced");
 const rootRef=useRef<HTMLDivElement|null>(null);

 useEffect(()=>{
  const sync=()=>{const select=nativeSelector();if(select&&select.value!==current)setCurrent(select.value)};
  sync();
  const interval=window.setInterval(sync,700);
  const onChange=(event:Event)=>{if(event.target===nativeSelector())sync()};
  document.addEventListener("change",onChange,true);
  return()=>{window.clearInterval(interval);document.removeEventListener("change",onChange,true)};
 },[current]);

 useEffect(()=>{
  if(!open)return;
  const onPointer=(event:PointerEvent)=>{if(rootRef.current&&!rootRef.current.contains(event.target as Node))setOpen(false)};
  const onKey=(event:KeyboardEvent)=>{if(event.key==="Escape")setOpen(false)};
  document.addEventListener("pointerdown",onPointer,true);
  document.addEventListener("keydown",onKey);
  return()=>{document.removeEventListener("pointerdown",onPointer,true);document.removeEventListener("keydown",onKey)};
 },[open]);

 const selectValue=(value:string)=>{
  const select=nativeSelector();
  if(!select)return;
  const setter=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value")?.set;
  if(setter)setter.call(select,value);else select.value=value;
  select.dispatchEvent(new Event("change",{bubbles:true}));
  setCurrent(value);
  setOpen(false);
 };

 const grouped=useMemo(()=>{
  const result=new Map<string,AIModel[]>();
  for(const model of models){
   const key=isSystemModel(model)?"Системные модели":providerName(model.provider);
   const list=result.get(key)??[];list.push(model);result.set(key,list);
  }
  return [...result.entries()].sort(([a],[b])=>a==="Системные модели"?-1:b==="Системные модели"?1:a.localeCompare(b,"ru"));
 },[models]);
 const selectedMode=MODES.find(item=>item.value===current);
 const selectedModel=current.startsWith("model:")?models.find(model=>`model:${model.slug}`===current):undefined;
 const triggerTitle=selectedMode?.title??(selectedModel?modelLabel(selectedModel):"Средний");
 const triggerHint=selectedMode?"Режим":selectedModel?(isSystemModel(selectedModel)?"Системная модель":providerName(selectedModel.provider)):"Режим";

 return <div className={styles.picker} ref={rootRef}>
  <button type="button" className={`${styles.trigger} ${open?styles.triggerOpen:""}`} onClick={()=>setOpen(value=>!value)} aria-haspopup="dialog" aria-expanded={open}>
   <span className={styles.triggerIcon}>{selectedMode?<Icon name={selectedMode.icon} size={16}/>:<Icon name="spark" size={16}/>}</span>
   <span className={styles.triggerCopy}><small>{triggerHint}</small><strong>{triggerTitle}</strong></span>
   <Icon name="chevron" size={14} className={open?styles.chevronOpen:""}/>
  </button>
  {open&&<div className={styles.popover} role="dialog" aria-label="Выбор режима нейросети">
   <div className={styles.popoverHead}><div><strong>Как отвечать?</strong><span>Выберите уровень — система сама подберёт модель</span></div></div>
   <div className={styles.modeGrid}>{MODES.map(mode=>{
    const active=current===mode.value;
    return <button key={mode.value} type="button" className={`${styles.modeCard} ${active?styles.active:""}`} onClick={()=>selectValue(mode.value)} aria-pressed={active}>
     <span className={styles.modeTop}><span className={styles.modeIcon}><Icon name={mode.icon} size={17}/></span>{active&&<span className={styles.check}><Icon name="check" size={13}/></span>}</span>
     <strong>{mode.title}</strong><small>{mode.description}</small><em>{mode.badge}</em>
    </button>})}</div>
   <div className={styles.divider}><span>или выберите конкретную модель</span></div>
   <div className={styles.modelScroll}>{grouped.map(([provider,items])=><section className={styles.modelGroup} key={provider}>
    <header><span>{provider}</span><small>{items.filter(item=>item.available).length}/{items.length} доступно</small></header>
    <div className={styles.modelList}>{items.map(model=>{
     const value=`model:${model.slug}`;const active=current===value;
     return <button type="button" key={model.slug} disabled={!model.available} className={`${styles.modelRow} ${active?styles.activeModel:""}`} onClick={()=>selectValue(value)} aria-pressed={active}>
      <span className={styles.modelAvatar}>{isSystemModel(model)?"S":modelLabel(model).slice(0,1).toUpperCase()}</span>
      <span className={styles.modelCopy}><strong>{modelLabel(model)}</strong><small>{!model.available?"Временно недоступна":isSystemModel(model)?"Системная модель":providerName(model.provider)}</small></span>
      {active&&<Icon name="check" size={15}/>} 
     </button>})}</div>
   </section>)}</div>
  </div>}
 </div>;
}

export default function WorkspaceUX(){
 const[target,setTarget]=useState<HTMLElement|null>(null);
 const[models,setModels]=useState<AIModel[]>([]);
 useEffect(()=>{
  let observer:MutationObserver|null=null;
  const locate=()=>{
   const host=document.querySelector<HTMLElement>(".chatHeader .headerControls");
   const select=nativeSelector();
   if(host&&select){setTarget(host);document.documentElement.classList.add("enhancedModelPicker");observer?.disconnect();return true}
   return false;
  };
  if(!locate()){observer=new MutationObserver(locate);observer.observe(document.body,{childList:true,subtree:true})}
  void api<AIModel[]>("/models/").then(setModels).catch(()=>undefined);
  return()=>{observer?.disconnect();document.documentElement.classList.remove("enhancedModelPicker")};
 },[]);
 return <><WorkspaceV2/>{target&&createPortal(<ModelPicker models={models}/>,target)}</>;
}
