"use client";

import {useEffect,useMemo,useRef,useState} from "react";
import {api} from "../../lib/api";
import type {AIModel} from "../../lib/types";
import {Icon} from "./Icons";

type Props={value:string;models:AIModel[];disabled?:boolean;onChange:(value:string)=>void};
type PickerSection="auto"|"model";

const modes=[
 {value:"auto:auto",label:"AUTO",hint:"Сам определит сложность и выберет подходящую модель",icon:"spark" as const},
 {value:"auto:economy",label:"Простой",hint:"Быстро и экономно",icon:"zap" as const},
 {value:"auto:balanced",label:"Средний",hint:"Оптимальный баланс качества и цены",icon:"spark" as const},
 {value:"auto:maximum",label:"Сложный",hint:"Максимум качества для сложных задач",icon:"brain" as const},
];

const PROVIDERS:Record<string,{label:string;order:number}>={
 "llm-system":{label:"LLM System",order:10},
 system:{label:"LLM System",order:10},
 gigachat:{label:"LLM System",order:10},
 openai:{label:"ChatGPT",order:20},
 chatgpt:{label:"ChatGPT",order:20},
 deepseek:{label:"DeepSeek",order:30},
 anthropic:{label:"Claude",order:40},
 claude:{label:"Claude",order:40},
 gemini:{label:"Gemini",order:50},
 google:{label:"Gemini",order:50},
 xai:{label:"Grok",order:60},
 grok:{label:"Grok",order:60},
 openrouter:{label:"OpenRouter",order:70},
};

function normalizedProvider(model:AIModel){
 const raw=(model.provider||"").trim().toLowerCase();
 if(PROVIDERS[raw])return raw;
 const haystack=`${raw} ${model.display_name} ${model.slug}`.toLowerCase();
 if(haystack.includes("llm system")||haystack.includes("system lite")||haystack.includes("system pro")||haystack.includes("system max")||haystack.includes("giga"))return "llm-system";
 if(haystack.includes("chatgpt")||haystack.includes("openai")||haystack.includes("gpt-"))return "openai";
 if(haystack.includes("deepseek"))return "deepseek";
 if(haystack.includes("claude")||haystack.includes("anthropic"))return "anthropic";
 if(haystack.includes("gemini")||haystack.includes("google"))return "gemini";
 if(haystack.includes("grok")||haystack.includes("xai"))return "xai";
 if(haystack.includes("openrouter"))return "openrouter";
 return raw||"other";
}

function providerLabelByKey(key:string){
 return PROVIDERS[key]?.label??(key==="other"?"Другие модели":key);
}

function modelLabel(model:AIModel){
 let label=(model.display_name||model.slug).trim();
 const provider=providerLabelByKey(normalizedProvider(model));
 const prefixes=[`${provider} · `,`${provider}: `,`${provider} - `];
 for(const prefix of prefixes){if(label.toLowerCase().startsWith(prefix.toLowerCase())){label=label.slice(prefix.length).trim();break}}
 if(normalizedProvider(model)==="llm-system"){
  const haystack=`${model.slug} ${model.display_name} ${model.exact_api_id}`.toLowerCase();
  if(haystack.includes("max"))return "System Max";
  if(haystack.includes("pro"))return "System Pro";
  return "System Lite";
 }
 return label||model.slug;
}

export function ModelPicker({value,models,disabled=false,onChange}:Props){
 const[open,setOpen]=useState(false);
 const[catalog,setCatalog]=useState<AIModel[]>(()=>models.filter(item=>item.available));
 const root=useRef<HTMLDivElement|null>(null);
 const initialSection:PickerSection=value.startsWith("model:")?"model":"auto";
 const[section,setSection]=useState<PickerSection>(initialSection);

 useEffect(()=>{setCatalog(models.filter(item=>item.available));},[models]);
 useEffect(()=>{
  let active=true;
  const refresh=async()=>{
   if(typeof document!=="undefined"&&document.visibilityState==="hidden")return;
   try{const rows=await api<AIModel[]>("/models/");if(active)setCatalog(rows.filter(item=>item.available));}catch{}
  };
  const timer=window.setInterval(()=>void refresh(),30000);
  const wake=()=>void refresh();
  window.addEventListener("online",wake);
  window.addEventListener("focus",wake);
  return()=>{active=false;window.clearInterval(timer);window.removeEventListener("online",wake);window.removeEventListener("focus",wake)};
 },[]);

 const groups=useMemo(()=>{
  const map=new Map<string,AIModel[]>();
  for(const model of catalog){
   const key=normalizedProvider(model);
   map.set(key,[...(map.get(key)??[]),model]);
  }
  return Array.from(map.entries()).sort(([a],[b])=>{
   const ao=PROVIDERS[a]?.order??999;
   const bo=PROVIDERS[b]?.order??999;
   return ao-bo||providerLabelByKey(a).localeCompare(providerLabelByKey(b),"ru");
  });
 },[catalog]);

 const currentMode=modes.find(item=>item.value===value);
 const currentModel=!currentMode&&value.startsWith("model:")?catalog.find(item=>`model:${item.slug}`===value):undefined;
 const unavailableManual=!currentMode&&value.startsWith("model:")&&!currentModel;
 const currentProvider=currentModel?normalizedProvider(currentModel):"";
 const firstProvider=groups[0]?.[0]??"";
 const[selectedProvider,setSelectedProvider]=useState<string>(currentProvider||firstProvider);

 useEffect(()=>{
  if(value.startsWith("model:")){
   setSection("model");
   const model=catalog.find(item=>`model:${item.slug}`===value);
   if(model)setSelectedProvider(normalizedProvider(model));
  }
 },[value,catalog]);
 useEffect(()=>{
  if(!selectedProvider&&firstProvider)setSelectedProvider(firstProvider);
  if(selectedProvider&&!groups.some(([key])=>key===selectedProvider)&&firstProvider)setSelectedProvider(firstProvider);
 },[groups,selectedProvider,firstProvider]);
 useEffect(()=>{
  if(!open)return;
  const outside=(event:MouseEvent)=>{if(root.current&&!root.current.contains(event.target as Node))setOpen(false)};
  const key=(event:KeyboardEvent)=>{if(event.key==="Escape")setOpen(false)};
  document.addEventListener("mousedown",outside);window.addEventListener("keydown",key);
  return()=>{document.removeEventListener("mousedown",outside);window.removeEventListener("keydown",key)};
 },[open]);

 const choose=(next:string)=>{if(disabled)return;onChange(next);setOpen(false)};
 const label=currentMode?(currentMode.value==="auto:auto"?"AUTO":`AUTO · ${currentMode.label}`):currentModel?`${providerLabelByKey(currentProvider)} · ${modelLabel(currentModel)}`:unavailableManual?"Резервный маршрут":"AUTO";
 const sub=currentMode?.hint??(currentModel?"Конкретная модель":unavailableManual?"Ранее выбранная модель временно недоступна — чат переключится на рабочую резервную модель":"Сам определит сложность и выберет подходящую модель");
 const providerModels=groups.find(([key])=>key===selectedProvider)?.[1]??[];

 return <div className="modelPicker" ref={root}>
  <button type="button" className="modelPickerTrigger" disabled={disabled} onClick={()=>setOpen(v=>!v)} aria-haspopup="dialog" aria-expanded={open} title="Выбрать AUTO, уровень или конкретную нейросеть">
   <span className="modelPickerMark"><Icon name={currentMode?.icon??"brain"} size={14}/></span>
   <span className="modelPickerCurrent"><b>{label}</b><small>{sub}</small></span>
   <Icon name="chevron" size={13}/>
  </button>
  {open&&<div className="modelPickerMenu modelPickerMenuV2" role="dialog" aria-label="Выбор нейросети">
   <div className="modelPickerHead"><div><b>Выбор нейросети</b><span>AUTO определяет сложность сам; уровни фиксируют класс моделей.</span></div><button type="button" onClick={()=>setOpen(false)} aria-label="Закрыть"><Icon name="x" size={15}/></button></div>
   <div className="modelPickerTabs" role="tablist" aria-label="Способ выбора модели">
    <button type="button" role="tab" aria-selected={section==="auto"} className={section==="auto"?"active":""} onClick={()=>setSection("auto")}><Icon name="spark" size={15}/>AUTO и уровни</button>
    <button type="button" role="tab" aria-selected={section==="model"} className={section==="model"?"active":""} onClick={()=>setSection("model")}><Icon name="brain" size={15}/>Конкретная модель</button>
   </div>
   {section==="auto"?<div className="modelPickerPane">
    <p className="modelPickerHelp">AUTO определяет сложность запроса и выбирает модель из настроенного администратором пула. Уровни позволяют зафиксировать класс моделей.</p>
    <div className="modelModeGrid">{modes.map(item=><button type="button" key={item.value} className={value===item.value?"active":""} onClick={()=>choose(item.value)}><span><Icon name={item.icon} size={16}/></span><b>{item.label}</b><small>{item.hint}</small>{value===item.value&&<Icon name="check" size={15}/>}</button>)}</div>
   </div>:<div className="modelPickerPane">
    <p className="modelPickerHelp">Сначала выберите нейросеть, затем конкретную модель. Каталог автоматически обновляется и показывает только реально готовые модели.</p>
    <div className="modelProviderTabs" role="tablist" aria-label="Провайдер нейросети">{groups.map(([provider,items])=>{
     return <button type="button" role="tab" aria-selected={selectedProvider===provider} key={provider} className={selectedProvider===provider?"active":""} onClick={()=>setSelectedProvider(provider)}><b>{providerLabelByKey(provider)}</b><small>{items.length} доступно</small></button>
    })}</div>
    <div className="modelPickerModels modelPickerProviderModels">{providerModels.length?providerModels.map(model=>{const selected=value===`model:${model.slug}`;return <button type="button" key={model.slug} className={selected?"active":""} onClick={()=>choose(`model:${model.slug}`)}><span className="modelDot online"/><span className="modelCopy"><b>{modelLabel(model)}</b><small>Готова к работе</small></span>{selected&&<Icon name="check" size={15}/>}</button>}):<div className="modelPickerEmpty">У этого провайдера сейчас нет доступных моделей.</div>}</div>
   </div>}
  </div>}
 </div>;
}
