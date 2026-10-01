"use client";

import {useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";
import WorkspaceV2 from "./WorkspaceV2";
import {ActivityTrace,type ActivityStep} from "./ActivityTrace";

function stepId(message:string){
 const text=message.toLocaleLowerCase("ru-RU");
 if(text.includes("резервн")||text.includes("переключ"))return "fallback";
 if(text.includes("повтор")||text.includes("восстанав"))return "recovery";
 if(text.includes("источник")||text.includes("актуальн")||text.includes("внешн"))return "research";
 if(text.includes("сопостав")||text.includes("собира")||text.includes("формир"))return "synthesis";
 if(text.includes("auto")||text.includes("модел")||text.includes("маршрут"))return "routing";
 return "prepare";
}

function warningState(message:string){
 const text=message.toLocaleLowerCase("ru-RU");
 return text.includes("недоступ")||text.includes("без неподтвержд")?"warning":"running";
}

export default function WorkspaceExperience(){
 const[host,setHost]=useState<HTMLElement|null>(null);
 const[steps,setSteps]=useState<ActivityStep[]>([]);
 const[running,setRunning]=useState(false);
 const wasRunning=useRef(false);
 const lastMessage=useRef("");

 useEffect(()=>{
  let traceHost:HTMLDivElement|null=null;
  const ensureHost=()=>{
   const main=document.querySelector<HTMLElement>(".workspaceMain");
   const thread=document.querySelector<HTMLElement>(".workspaceMain .threadViewport");
   if(!main)return;
   if(!traceHost||!traceHost.isConnected){
    traceHost=document.createElement("div");
    traceHost.dataset.chatActivityTrace="true";
    if(thread)main.insertBefore(traceHost,thread);else main.appendChild(traceHost);
    setHost(traceHost);
   }else if(thread&&traceHost.nextElementSibling!==thread){
    main.insertBefore(traceHost,thread);
   }
  };
  const sync=()=>{
   ensureHost();
   const isRunning=Boolean(document.querySelector(".workspaceMain .sendButton.stop"));
   const note=document.querySelector<HTMLElement>(".workspaceMain .topNotice.subtle");
   const error=document.querySelector<HTMLElement>(".workspaceMain .topNotice.error span");
   const message=note?.textContent?.trim()??"";

   if(isRunning&&!wasRunning.current){
    setSteps([]);
    lastMessage.current="";
   }
   if(isRunning&&message&&message!==lastMessage.current){
    lastMessage.current=message;
    const id=stepId(message);
    setSteps(current=>{
     const completed=current.map(item=>item.state==="running"?{...item,state:"completed"}:item);
     const existing=completed.findIndex(item=>item.step===id&&item.message===message);
     if(existing>=0){
      const next=[...completed];next[existing]={...next[existing],state:warningState(message)};return next.slice(-5);
     }
     return [...completed,{step:id,state:warningState(message),message}].slice(-5);
    });
    // The activity trace replaces the old one-line technical notice while a
    // response is running. Errors and offline notices remain untouched.
    note?.setAttribute("aria-hidden","true");
    if(note)note.style.display="none";
   }
   if(!isRunning&&wasRunning.current){
    setSteps(current=>current.map(item=>item.state==="running"?{...item,state:"completed"}:item));
    if(error?.textContent?.trim()){
     const text=error.textContent.trim();
     setSteps(current=>[...current.map(item=>item.state==="running"?{...item,state:"completed"}:item),{step:"error",state:"failed",message:text}].slice(-5));
    }
   }
   if(note&&!isRunning&&note.style.display==="none"){
    note.style.removeProperty("display");
    note.removeAttribute("aria-hidden");
   }
   wasRunning.current=isRunning;
   setRunning(isRunning);
  };
  const observer=new MutationObserver(sync);
  observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:["class"]});
  sync();
  return()=>{observer.disconnect();traceHost?.remove();setHost(null)};
 },[]);

 return <><WorkspaceV2/>{host&&createPortal(<ActivityTrace steps={steps} running={running}/>,host)}</>;
}
