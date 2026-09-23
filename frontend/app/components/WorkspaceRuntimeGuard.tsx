"use client";

import {useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";

type PublicMode = "economy" | "balanced" | "maximum";

const LEVELS: Array<{value: PublicMode; label: string; hint: string}> = [
  {value: "economy", label: "System Lite", hint: "Быстро и экономно"},
  {value: "balanced", label: "System Pro", hint: "Оптимально для большинства задач"},
  {value: "maximum", label: "System Max", hint: "Максимум качества"},
];

function isWorkspace() {
  return typeof window !== "undefined" && (window.location.pathname === "/app" || window.location.pathname.startsWith("/app/"));
}

function routingSelect() {
  if (typeof document === "undefined") return null;
  return document.querySelector<HTMLSelectElement>(
    ".chatHeader .headerControls > .selectControl:first-child select",
  );
}

function composerTarget() {
  if (typeof document === "undefined") return null;
  return document.querySelector<HTMLElement>(".composerTierSlot");
}

function readPublicMode(): PublicMode {
  const raw = routingSelect()?.value ?? "";
  if (raw === "auto:economy") return "economy";
  if (raw === "auto:maximum") return "maximum";
  return "balanced";
}

function dispatchRouting(source: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  if (setter) setter.call(source, value);
  else source.value = value;
  source.dispatchEvent(new Event("change", {bubbles: true}));
}

/** Mirrors the real React-bound WorkspaceV2 routing control inside the composer.
 * The hidden source select remains the single API/state owner; this component
 * only presents the customer-facing System Lite / Pro / Max vocabulary.
 */
export default function WorkspaceRuntimeGuard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [mode, setMode] = useState<PublicMode>("balanced");
  const [sending, setSending] = useState(false);
  const submitLocked = useRef(false);
  const observedSending = useRef(false);
  const unlockTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!isWorkspace()) return;
    let disposed = false;
    const sync = () => {
      if (disposed) return;
      const nextTarget = composerTarget();
      if (nextTarget) setTarget((current) => current === nextTarget ? current : nextTarget);
      setSending(Boolean(document.querySelector(".sendButton.stop")));
      const source = routingSelect();
      if (!source) return;
      setMode(readPublicMode());
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.body, {subtree:true,childList:true,attributes:true,attributeFilter:["disabled","value","class"]});
    const interval = window.setInterval(sync, 400);
    document.addEventListener("change", sync, true);
    return () => {disposed=true;observer.disconnect();window.clearInterval(interval);document.removeEventListener("change",sync,true)};
  }, []);

  // Defence in depth for real-user double click / double Enter. Composer itself
  // also gates submit; this capture-phase lock closes the same-frame event race.
  useEffect(() => {
    if (!isWorkspace()) return;
    const unlock=()=>{submitLocked.current=false;observedSending.current=false;if(unlockTimer.current!==null){window.clearTimeout(unlockTimer.current);unlockTimer.current=null}};
    const gesture=(event:Event)=>{
      const node=event.target instanceof Element?event.target:null;if(!node)return false;
      if(event instanceof KeyboardEvent)return event.key==="Enter"&&!event.shiftKey&&!event.isComposing&&node.matches(".composerShell textarea");
      return event.type==="click"&&Boolean(node.closest(".sendButton:not(.stop)"));
    };
    const guard=(event:Event)=>{if(!gesture(event))return;if(submitLocked.current){event.preventDefault();event.stopImmediatePropagation();return}submitLocked.current=true;observedSending.current=false;if(unlockTimer.current!==null)window.clearTimeout(unlockTimer.current);unlockTimer.current=window.setTimeout(unlock,10000)};
    const observer=new MutationObserver(()=>{const active=Boolean(document.querySelector(".sendButton.stop"));if(active)observedSending.current=true;else if(observedSending.current)unlock()});
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:["class"]});
    document.addEventListener("click",guard,true);document.addEventListener("keydown",guard,true);
    return()=>{document.removeEventListener("click",guard,true);document.removeEventListener("keydown",guard,true);observer.disconnect();if(unlockTimer.current!==null)window.clearTimeout(unlockTimer.current)};
  },[]);

  const choose=(next:PublicMode)=>{const source=routingSelect();if(!source||source.disabled||sending)return;setMode(next);dispatchRouting(source,`auto:${next}`)};
  if(!target)return null;
  const current=LEVELS.find(item=>item.value===mode)??LEVELS[1];
  return createPortal(
    <label className="systemTierPicker" title={sending?"Дождитесь окончания текущего ответа":current.hint}>
      <span className="srOnly">Уровень модели</span>
      <select aria-label="Уровень модели" value={mode} disabled={sending||Boolean(routingSelect()?.disabled)} onChange={event=>choose(event.target.value as PublicMode)}>
        {LEVELS.map(item=><option key={item.value} value={item.value}>{item.label}</option>)}
      </select>
    </label>,
    target,
  );
}
