import { create } from "zustand";
import { api, setCsrfToken } from "../api/client";
import type { AuthStatus, User } from "../api/types";

interface SessionState {
  loaded: boolean;
  initialized: boolean;
  user: User | null;
  loadError: string | null;
  refresh: () => Promise<void>;
  accept: (s: AuthStatus) => void;
  logout: () => Promise<void>;
}

export const useSession = create<SessionState>((set) => ({
  loaded: false,
  initialized: false,
  user: null,
  loadError: null,
  refresh: async () => {
    try {
      const s = await api.auth.status();
      setCsrfToken(s.csrf_token);
      set({ loaded: true, initialized: s.initialized, user: s.user, loadError: null });
    } catch (e) {
      set({ loaded: true, loadError: (e as Error).message });
    }
  },
  accept: (s) => {
    setCsrfToken(s.csrf_token);
    set({ loaded: true, initialized: s.initialized, user: s.user, loadError: null });
  },
  logout: async () => {
    try {
      await api.auth.logout();
    } finally {
      setCsrfToken(null);
      set({ user: null });
    }
  },
}));
