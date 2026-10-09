import { createContext, useContext, useEffect, useState } from "react";

// ------------------------------------------------------------------ routing
// Hash routes (#/halls/H1) need no server configuration, so the built
// dashboard can be served from any static file host.
function parse() {
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const [path, qs = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { path, parts, params: new URLSearchParams(qs) };
}

export function useRoute() {
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function navigate(to) {
  window.location.hash = to;
}

export function href(...parts) {
  return `#/${parts.map((p) => encodeURIComponent(p)).join("/")}`;
}

// ------------------------------------------------------------------ session
export const RANK = { viewer: 0, operator: 1, admin: 2 };

export const SessionContext = createContext({
  role: "viewer",
  tz: "Asia/Kolkata",
  demoMode: false,
  authRequired: true,
  can: () => false,
});

export function useSession() {
  return useContext(SessionContext);
}

export function makeSession(me) {
  return {
    role: me.role,
    tz: me.site_timezone,
    demoMode: me.demo_mode,
    authRequired: me.auth_required,
    version: me.app_version,
    environment: me.environment,
    can: (needed) => RANK[me.role] >= RANK[needed],
  };
}
