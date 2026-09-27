"use client";

import {useEffect} from "react";

function gigachatLabel(value:string,current:string){
  const haystack=`${value} ${current}`.toLowerCase();
  if(!haystack.includes("giga"))return null;
  if(haystack.includes("max"))return "GigaChat · Системная Продвинутая";
  if(haystack.includes("pro"))return "GigaChat · Системная Расширенная";
  return "GigaChat · Системная Базовая";
}

export default function WorkspaceUxEnhancer(){
  useEffect(()=>{
    const enhance=()=>{
      const select=document.querySelector<HTMLSelectElement>(".headerControls .selectControl:first-child select");
      if(!select)return;
      select.setAttribute("aria-label","Режим или конкретная AI-модель");
      select.title="Выберите уровень нейросети или конкретную модель";
      const labels:Record<string,string>={
        "auto:economy":"Простой",
        "auto:balanced":"Средний",
        "auto:maximum":"Сложный",
      };
      for(const option of Array.from(select.options)){
        const automatic=labels[option.value];
        if(automatic){option.textContent=automatic;continue;}
        if(option.value.startsWith("model:")){
          const renamed=gigachatLabel(option.value,option.textContent??"");
          if(renamed)option.textContent=`${renamed}${option.disabled?" · недоступна":""}`;
        }
      }
      const groups=select.querySelectorAll("optgroup");
      if(groups[0])groups[0].label="Выбрать уровень нейросети";
      if(groups[1])groups[1].label="Выбрать конкретную нейросеть и модель";
    };
    enhance();
    const observer=new MutationObserver(enhance);
    observer.observe(document.body,{childList:true,subtree:true});
    return()=>observer.disconnect();
  },[]);
  return null;
}
