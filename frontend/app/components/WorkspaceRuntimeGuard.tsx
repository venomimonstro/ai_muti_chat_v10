"use client";

import {useEffect,useRef} from "react";
import {api} from "../../lib/api";

const PENDING_PREFIX="aiws:pending-stream:";
const MISSING_GRACE_MS=30_000;

type PendingStreamRecord={idempotencyKey?:string;createdAt?:number};
type PendingStatus={state:string;active:boolean;terminal:boolean};

function isWorkspace(){
 return typeof window!=="undefined"&&(window.location.pathname==="/app"||window.location.pathname.startsWith("/app/"));
}

function pendingRows(){
 if(typeof window==="undefined")return [] as Array<{key:string;conversationId:string;value:PendingStreamRecord}>;
 const rows:Array<{key:string;conversationId:string;value:PendingStreamRecord}>=[];
 try{
  for(let index=0;index<localStorage.length;index+=1){
   const key=localStorage.key(index);if(!key||!key.startsWith(PENDING_PREFIX))continue;
   const conversationId=key.slice(PENDING_PREFIX.length);if(!conversationId)continue;
   try{const value=JSON.parse(localStorage.getItem(key)??"{}") as PendingStreamRecord;rows.push({key,conversationId,value})}catch{localStorage.removeItem(key)}
  }
 }catch{}
 return rows;
}

async function reconcilePendingStreams(){
 if(!isWorkspace()||typeof document==="undefined"||document.visibilityState==="hidden")return;
 const now=Date.now();
 for(const row of pendingRows()){
  const idempotencyKey=String(row.value.idempotencyKey??"").trim();
  if(!idempotencyKey){try{localStorage.removeItem(row.key)}catch{};continue}
  try{
   const status=await api<PendingStatus>(`/conversations/${row.conversationId}/messages/status/?idempotency_key=${encodeURIComponent(idempotencyKey)}`);
   const age=Math.max(0,now-Number(row.value.createdAt??0));
   // A terminal generation can never be resumed. A missing generation is also
   // safe to forget after a short grace period: no provider call exists to resume.
   // Active queued/running requests keep their original idempotency key so a page
   // reload follows the same provider call instead of charging twice.
   if(status.terminal||(status.state==="missing"&&age>=MISSING_GRACE_MS)){
    try{localStorage.removeItem(row.key)}catch{}
   }
  }catch{
   // Fail safe: if status cannot be verified, keep the pending key. Reusing one
   // idempotency key is safer than risking a duplicate paid provider request.
  }
 }
}

/**
 * Reliability-only guard for the customer workspace.
 *
 * Routing/model state belongs exclusively to WorkspaceV2 + ModelPicker. Older
 * versions of this component mirrored a hidden legacy select and even rewrote a
 * manual model choice to AUTO/System Pro; that violated the visible user choice
 * and could make the chat appear to ignore a selected provider. This guard now
 * deliberately has zero routing UI/state responsibilities.
 */
export default function WorkspaceRuntimeGuard(){
 const submitLocked=useRef(false);const observedSending=useRef(false);const unlockTimer=useRef<number|null>(null);

 useEffect(()=>{
  if(!isWorkspace())return;
  void reconcilePendingStreams();
  const wake=()=>void reconcilePendingStreams();
  const timer=window.setInterval(wake,30_000);
  window.addEventListener("online",wake);window.addEventListener("focus",wake);document.addEventListener("visibilitychange",wake);
  return()=>{window.clearInterval(timer);window.removeEventListener("online",wake);window.removeEventListener("focus",wake);document.removeEventListener("visibilitychange",wake)};
 },[]);

 // Defence in depth for real-user double click / double Enter. Composer itself
 // also gates submit; this capture-phase lock closes the same-frame event race.
 useEffect(()=>{
  if(!isWorkspace())return;
  const unlock=()=>{submitLocked.current=false;observedSending.current=false;if(unlockTimer.current!==null){window.clearTimeout(unlockTimer.current);unlockTimer.current=null}};
  const gesture=(event:Event)=>{const node=event.target instanceof Element?event.target:null;if(!node)return false;if(event instanceof KeyboardEvent)return event.key==="Enter"&&!event.shiftKey&&!event.isComposing&&node.matches(".composerShell textarea");return event.type==="click"&&Boolean(node.closest(".sendButton:not(.stop)"))};
  const guard=(event:Event)=>{if(!gesture(event))return;if(submitLocked.current){event.preventDefault();event.stopImmediatePropagation();return}submitLocked.current=true;observedSending.current=false;if(unlockTimer.current!==null)window.clearTimeout(unlockTimer.current);unlockTimer.current=window.setTimeout(unlock,10_000)};
  const observer=new MutationObserver(()=>{const active=Boolean(document.querySelector(".sendButton.stop"));if(active)observedSending.current=true;else if(observedSending.current)unlock()});
  observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:["class"]});
  document.addEventListener("click",guard,true);document.addEventListener("keydown",guard,true);
  return()=>{document.removeEventListener("click",guard,true);document.removeEventListener("keydown",guard,true);observer.disconnect();if(unlockTimer.current!==null)window.clearTimeout(unlockTimer.current)};
 },[]);

 return null;
}
