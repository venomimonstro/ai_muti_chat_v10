"use client";

import {useEffect,useRef} from "react";
import {usePathname} from "next/navigation";
import {api} from "../../lib/api";

const PATH_EVENTS:Record<string,string>={"/":"landing_view","/pricing":"pricing_view","/register":"register_start","/app":"workspace_open"};
export async function trackProductEvent(eventName:string,metadata:Record<string,string|number|boolean>={}){try{await api("/analytics/events/",{method:"POST",body:JSON.stringify({event_name:eventName,client_event_id:crypto.randomUUID(),source_path:window.location.pathname,metadata})});}catch{/* analytics never blocks product UX */}}
export default function ProductAnalytics(){const pathname=usePathname();const seen=useRef(new Set<string>());useEffect(()=>{const eventName=PATH_EVENTS[pathname];if(!eventName||seen.current.has(`${pathname}:${eventName}`))return;seen.current.add(`${pathname}:${eventName}`);void trackProductEvent(eventName);},[pathname]);return null;}
