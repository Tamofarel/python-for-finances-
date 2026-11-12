import "@fontsource-variable/instrument-sans";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { applyToDocument, usePrefs, watchSystemPreferences } from "./store/prefs";

// Apply the remembered theme and motion preference before the first paint.
applyToDocument(usePrefs.getState().prefs);
watchSystemPreferences();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
