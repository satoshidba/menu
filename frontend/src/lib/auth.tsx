import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, setCsrf } from "./api";

export type Me = { user: { id: string; email: string; display_name: string; role: string }; household: { id: string; name: string; timezone: string }; csrf_token: string; features: { ai: boolean; barcode_lookup: boolean } };
type Ctx = { me: Me | null; loading: boolean; needsSetup: boolean; refresh: () => Promise<void>; signOut: () => Promise<void> };
const AuthCtx = createContext<Ctx>({ me: null, loading: true, needsSetup: false, refresh: async () => {}, signOut: async () => {} });
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const m = await api<Me>("/auth/me");
      setCsrf(m.csrf_token);
      setMe(m);
      setNeedsSetup(false);
    } catch {
      setMe(null);
      try { setNeedsSetup((await api("/setup/status")).needs_setup); } catch { /* offline */ }
    } finally {
      setLoading(false);
    }
  }, []);
  const signOut = useCallback(async () => { try { await api("/auth/logout", { method: "POST" }); } finally { setMe(null); } }, []);
  useEffect(() => {
    refresh();
    const h = () => setMe(null);
    window.addEventListener("larder:unauthorised", h);
    return () => window.removeEventListener("larder:unauthorised", h);
  }, [refresh]);
  return <AuthCtx.Provider value={{ me, loading, needsSetup, refresh, signOut }}>{children}</AuthCtx.Provider>;
}
