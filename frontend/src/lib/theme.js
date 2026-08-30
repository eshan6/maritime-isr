// Light or dark, chosen once and remembered.
//
// Three states, not two. "System" follows the operating system, which is what
// somebody who has set their whole machine dark expects on arrival; the other
// two are an explicit override that beats the machine in either direction. A
// two-state toggle cannot express "follow my laptop" and gets it wrong for
// every user who has already made that choice elsewhere.
//
// The chosen state is written to `data-theme` on the root element, where the
// stylesheet's token blocks pick it up. Nothing else in the app reads the
// value except the map, which has to rebuild its style object because MapLibre
// paints from JavaScript rather than from CSS.

import { useEffect, useState } from "react";

const KEY = "misr.theme";
const EVENT = "misr:themechange";

export const THEMES = ["system", "light", "dark"];

export function readTheme() {
  try {
    const v = localStorage.getItem(KEY);
    return THEMES.includes(v) ? v : "system";
  } catch {
    // A browser with storage blocked still gets a working interface, on the
    // system setting. It just cannot remember an override between loads.
    return "system";
  }
}

//: What is actually on screen right now, with "system" resolved. The map needs
//: this rather than the preference, because it has to know which colours to
//: paint, not which policy produced them.
export function resolvedTheme(pref = readTheme()) {
  if (pref === "light" || pref === "dark") return pref;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark" : "light";
}

export function applyTheme(pref) {
  const root = document.documentElement;
  if (pref === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
}

export function setTheme(pref) {
  try {
    localStorage.setItem(KEY, pref);
  } catch { /* not fatal: the choice holds for this page */ }
  applyTheme(pref);
  window.dispatchEvent(new CustomEvent(EVENT, { detail: pref }));
}

//: Subscribes to both the toggle and the operating system, so a machine that
//: switches to dark at sunset carries the interface with it while the
//: preference is "system".
export function useTheme() {
  const [pref, setPref] = useState(readTheme);
  const [resolved, setResolved] = useState(() => resolvedTheme());

  useEffect(() => {
    applyTheme(pref);
    setResolved(resolvedTheme(pref));
    const onChange = (e) => {
      const next = e.detail ?? readTheme();
      setPref(next);
      setResolved(resolvedTheme(next));
    };
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onSystem = () => setResolved(resolvedTheme());
    window.addEventListener(EVENT, onChange);
    mq?.addEventListener?.("change", onSystem);
    return () => {
      window.removeEventListener(EVENT, onChange);
      mq?.removeEventListener?.("change", onSystem);
    };
  }, [pref]);

  return { pref, resolved, setTheme };
}
