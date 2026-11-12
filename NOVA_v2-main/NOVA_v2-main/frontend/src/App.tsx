import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Orb } from "./components/Orb";
import { Notice } from "./components/ui";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { LoginPage, SetupPage } from "./pages/AuthPages";
import { ChatPage } from "./pages/ChatPage";
import { HistoryPage } from "./pages/HistoryPage";
import { MemoryPage } from "./pages/MemoryPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ProjectPage } from "./pages/project/ProjectPage";
import { CommandCenter } from "./pages/command/CommandCenter";
import { WorkflowPage } from "./pages/workflow/WorkflowPage";
import { WorkflowsPage } from "./pages/workflow/WorkflowsPage";
import { usePrefs } from "./store/prefs";
import { useSession } from "./store/session";
import { useVoice } from "./store/voice";

export function App() {
  const { loaded, initialized, user, loadError, refresh } = useSession();

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The signed-in owner's preferences (theme, motion, voice) come from the server.
  const userId = user?.id;
  useEffect(() => {
    if (userId) void usePrefs.getState().load();
  }, [userId]);

  const prefsSynced = usePrefs((s) => s.synced);
  const wakeEnabled = usePrefs((s) => s.prefs.voice.wake_enabled);
  useEffect(() => {
    if (!userId || !prefsSynced || !wakeEnabled) return;
    const resume = () => {
      if (document.visibilityState === "visible") void useVoice.getState().resumeWake();
    };
    resume();
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [userId, prefsSynced, wakeEnabled]);

  if (!loaded) {
    return <div className="boot"><Orb presence="thinking" size={56} /><span className="sr-only">Starting NOVA</span></div>;
  }
  if (loadError) {
    return (
      <div className="auth-page">
        <div className="auth-card stack">
          <h1>NOVA can't reach its backend</h1>
          <Notice>{loadError}</Notice>
          <p className="muted">Start the backend (see the README), then try again.</p>
          <button type="button" className="btn btn-primary" onClick={() => void refresh()}>Try again</button>
        </div>
      </div>
    );
  }
  if (!initialized) return <SetupPage />;
  if (!user) return <LoginPage />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<CommandCenter />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        <Route path="workflows/:id" element={<WorkflowPage />} />
        <Route path="c/:id" element={<ChatPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:id/*" element={<ProjectPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="memory" element={<MemoryPage />} />
        <Route path="providers" element={<ProvidersPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
