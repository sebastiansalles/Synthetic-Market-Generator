import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { useStore } from "./store";
import "./styles.css";

// theme lives on <html data-theme="..."> so CSS variables follow it; persist via store
document.documentElement.dataset.theme = useStore.getState().theme;
useStore.subscribe((s) => {
  document.documentElement.dataset.theme = s.theme;
});

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
