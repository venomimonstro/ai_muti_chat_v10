"use client";

import {useEffect} from "react";

export default function WorkspaceUxEnhancer(){
  useEffect(()=>{
    const enhance=()=>{
      const select=document.querySelector<HTMLSelectElement>(".headerControls .selectControl:first-child select");
      if(!select)return;
      select.setAttribute("aria-label","Уровень задачи или конкретная LLM");
      select.title="Выберите уровень задачи или конкретную LLM и модель";
      const labels:Record<string,string>={
        "auto:economy":"Простая · эконом",
        "auto:balanced":"Средняя · оптимально",
        "auto:maximum":"Сложная · максимум качества",
      };
      for(const option of Array.from(select.options)){
        if(labels[option.value]&&option.textContent!==labels[option.value])option.textContent=labels[option.value];
      }
      const groups=select.querySelectorAll("optgroup");
      if(groups[0])groups[0].label="Уровень задачи — система выберет LLM";
      if(groups[1])groups[1].label="Вручную — выбрать LLM и модель";
    };
    enhance();
    const observer=new MutationObserver(enhance);
    observer.observe(document.body,{childList:true,subtree:true});
    return()=>observer.disconnect();
  },[]);
  return null;
}
