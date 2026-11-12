import { useEffect } from "react";
import { create } from "zustand";
import { api } from "../api/client";
import type { ProvidersResponse } from "../api/types";

interface ProvidersState {
  data: ProvidersResponse | null;
  error: string | null;
  loaded: boolean;
  reload: () => Promise<void>;
}

export const useProvidersStore = create<ProvidersState>((set) => ({
  data: null,
  error: null,
  loaded: false,
  reload: async () => {
    try {
      set({ data: await api.providers.list(), error: null, loaded: true });
    } catch (e) {
      set({ error: (e as Error).message, loaded: true });
    }
  },
}));

/** Provider status shared by the top bar and the settings screen (one live check per mount). */
export function useProviders() {
  const { data, error, reload, loaded } = useProvidersStore();
  useEffect(() => {
    if (!loaded) void reload();
  }, [loaded, reload]);
  return { data, error, reload };
}
