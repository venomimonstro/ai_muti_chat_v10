"use client";

import {useEffect} from "react";
import {api} from "../../lib/api";

const sent=new Set<string>();
let sentCount=0;
const MAX_PER_PAGE=20;

export function reportClientError(errorName:string,message:string,stack:string,correlationId=""){
 if(sentCount>=MAX_PER_PAGE)return;
 const sourcePath=typeof window!=="undefined"?window.location.pathname:"";
 const fingerprint=`${errorName}|${sourcePath}|${message.slice(0,180)}|${correlationId}`;
 if(sent.has(fingerprint))return;
 sent.add(fingerprint);sentCount+=1;
 void api("/client-errors/",{method:"POST",body:JSON.stringify({error_name:errorName.slice(0,120),message:message.slice(0,500),stack:stack.slice(-8000),source_path:sourcePath,correlation_id:correlationId.slice(0,160)})}).catch(()=>undefined);
}

export default function ClientErrorReporter(){
 useEffect(()=>{
  const onError=(event:ErrorEvent)=>{const error=event.error;reportClientError(error?.name||"JavaScriptError",String(error?.message||event.message||"Ошибка JavaScript"),String(error?.stack||""));};
  const onRejection=(event:PromiseRejectionEvent)=>{const reason=event.reason;reportClientError(reason?.name||"UnhandledPromiseRejection",String(reason?.message||"Необработанная ошибка Promise"),String(reason?.stack||""));};
  window.addEventListener("error",onError);
  window.addEventListener("unhandledrejection",onRejection);
  return()=>{window.removeEventListener("error",onError);window.removeEventListener("unhandledrejection",onRejection);};
 },[]);
 return null;
}
