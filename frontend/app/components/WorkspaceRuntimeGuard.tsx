"use client";

import {useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";

type PublicMode = "economy" | "balanced" | "maximum";

const LEVELS: Array<{value: PublicMode; label: string; hint: string}> = [
  {value: "economy", label: "System Lite", hint: "Быстро и экономно"},
  {value: "balanced", label: "System Pro", hint: "Оптимально для большинства задач"},
  {value: "maximum", label: "System Max", hint: "Максимум качества"},
];

function routingSelect() {
  return document.querySelector<HTMLSelectElement>(
    ".topbar .selectors > .selectWrap:first-child select",
  );
}

function composerActions() {
  return document.querySelector<HTMLElement>(".composer .composerActions");
}

function readPublicMode(): PublicMode {
  const raw = routingSelect()?.value ?? "";
  if (raw === "auto:economy") return "economy";
  if (raw === "auto:maximum") return "maximum";
  return "balanced";
}

/**
 * Reliability/UI layer around the existing workspace.
 *
 * 1. Renders only the product levels System Lite / Pro / Max inside the composer.
 *    The original bound routing select stays hidden in the DOM and remains the
 *    single React/API control, so no second routing implementation is introduced.
 * 2. Blocks a second click/Enter synchronously, before React state has time to
 *    re-render. This closes the sub-frame double-submit race that can create two
 *    different idempotency keys for one human action.
 */
export default function WorkspaceRuntimeGuard() {
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const [mode, setMode] = useState<PublicMode>("balanced");
  const submitLocked = useRef(false);
  const observedBusy = useRef(false);
  const unlockTimer = useRef<number | null>(null);

  useEffect(() => {
    let disposed = false;

    const sync = () => {
      if (disposed) return;
      const actions = composerActions();
      if (actions) setTarget((current) => current === actions ? current : actions);

      const source = routingSelect();
      if (!source) return;

      // The customer product has only System tiers. Legacy/manual conversations
      // are normalized to System Pro instead of exposing an upstream model name.
      if (source.value.startsWith("model:") && !source.disabled) {
        source.value = "auto:balanced";
        source.dispatchEvent(new Event("change", {bubbles: true}));
        setMode("balanced");
        return;
      }
      setMode(readPublicMode());
    };

    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class", "disabled"],
    });
    const interval = window.setInterval(sync, 500);
    document.addEventListener("change", sync, true);
    return () => {
      disposed = true;
      observer.disconnect();
      window.clearInterval(interval);
      document.removeEventListener("change", sync, true);
    };
  }, []);

  useEffect(() => {
    const unlock = () => {
      submitLocked.current = false;
      observedBusy.current = false;
      if (unlockTimer.current !== null) {
        window.clearTimeout(unlockTimer.current);
        unlockTimer.current = null;
      }
    };

    const isSubmitGesture = (event: Event) => {
      const node = event.target instanceof Element ? event.target : null;
      if (!node) return false;
      if (event instanceof KeyboardEvent) {
        return event.key === "Enter" && !event.shiftKey && !event.isComposing && node.matches(".composer textarea");
      }
      return event.type === "click" && Boolean(node.closest(".composer .send:not(.stop)"));
    };

    const guard = (event: Event) => {
      if (!isSubmitGesture(event)) return;
      if (submitLocked.current) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      submitLocked.current = true;
      observedBusy.current = false;
      if (unlockTimer.current !== null) window.clearTimeout(unlockTimer.current);
      // If validation prevents a send entirely, do not leave the composer locked.
      unlockTimer.current = window.setTimeout(() => {
        if (!document.querySelector(".composer.busy")) unlock();
      }, 1500);
    };

    const busyObserver = new MutationObserver(() => {
      const busy = Boolean(document.querySelector(".composer.busy"));
      if (busy) observedBusy.current = true;
      else if (observedBusy.current) unlock();
    });
    busyObserver.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });

    document.addEventListener("click", guard, true);
    document.addEventListener("keydown", guard, true);
    return () => {
      document.removeEventListener("click", guard, true);
      document.removeEventListener("keydown", guard, true);
      busyObserver.disconnect();
      if (unlockTimer.current !== null) window.clearTimeout(unlockTimer.current);
    };
  }, []);

  const choose = (next: PublicMode) => {
    const source = routingSelect();
    if (!source || source.disabled) return;
    setMode(next);
    source.value = `auto:${next}`;
    source.dispatchEvent(new Event("change", {bubbles: true}));
  };

  if (!target) return null;
  const current = LEVELS.find((item) => item.value === mode) ?? LEVELS[1];

  return createPortal(
    <label className="systemTierPicker" title={current.hint}>
      <span className="srOnly">Уровень модели</span>
      <select
        aria-label="Уровень модели"
        value={mode}
        disabled={Boolean(routingSelect()?.disabled)}
        onChange={(event) => choose(event.target.value as PublicMode)}
      >
        {LEVELS.map((item) => (
          <option key={item.value} value={item.value}>{item.label}</option>
        ))}
      </select>
    </label>,
    target,
  );
}
