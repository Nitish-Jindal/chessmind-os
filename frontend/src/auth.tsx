import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, loadTokens, saveTokens, type Player } from "./api";

interface AuthCtx {
  player: Player | null;
  loading: boolean;
  login(email: string, password: string): Promise<void>;
  register(email: string, password: string, displayName: string): Promise<void>;
  logout(): void;
  refreshPlayer(): Promise<void>;
  setPlayer(p: Player): void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [player, setPlayer] = useState<Player | null>(null);
  const [loading, setLoading] = useState<boolean>(loadTokens() !== null);

  const refreshPlayer = useCallback(async () => {
    try {
      setPlayer(await api.me());
    } catch {
      saveTokens(null);
      setPlayer(null);
    }
  }, []);

  useEffect(() => {
    if (loadTokens()) void refreshPlayer().finally(() => setLoading(false));
  }, [refreshPlayer]);

  const value = useMemo<AuthCtx>(
    () => ({
      player,
      loading,
      setPlayer,
      refreshPlayer,
      async login(email, password) {
        saveTokens(await api.login(email, password));
        await refreshPlayer();
      },
      async register(email, password, displayName) {
        await api.register(email, password, displayName);
        saveTokens(await api.login(email, password));
        await refreshPlayer();
      },
      logout() {
        saveTokens(null);
        setPlayer(null);
      },
    }),
    [player, loading, refreshPlayer],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}
