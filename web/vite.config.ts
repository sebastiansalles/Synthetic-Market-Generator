import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // the FastAPI app (uvicorn app:app --app-dir api --port 8000)
      "/profiles": "http://localhost:8000",
      "/simulate": "http://localhost:8000",
      "/assets": "http://localhost:8000",
      "/health": "http://localhost:8000",
      // Phase 4: WebSocket tick stream
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  test: {
    globals: true,
    environment: "node",
  },
});
