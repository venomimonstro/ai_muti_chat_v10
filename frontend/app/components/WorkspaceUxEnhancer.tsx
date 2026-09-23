"use client";

import {useEffect} from "react";

export default function WorkspaceUxEnhancer(){
  useEffect(()=>{
    const enhance=()=>{
      const select=document.querySelector<HTMLSelectElement>(".headerControls .selectControl:first-child select");
      if(!select)return;
      select.setAttribute("aria-label","Уровень System");
      select.title="Уровень качества ответа";
      const labels:Record<string,string>={
        "auto:economy":"System Lite",
        "auto:balanced":"System Pro",
        "auto:maximum":"System Max",
      };
      for(const option of Array.from(select.options)){
        if(labels[option.value]&&option.textContent!==labels[option.value])option.textContent=labels[option.value];
      }
      const groups=select.querySelectorAll("optgroup");
      if(groups[0])groups[0].label="Уровень System";
      if(groups[1])groups[1].label="Дополнительные модели";
    };
    enhance();
    const observer=new MutationObserver(enhance);
    observer.observe(document.body,{childList:true,subtree:true});
    return()=>observer.disconnect();
  },[]);
  return null;
}
