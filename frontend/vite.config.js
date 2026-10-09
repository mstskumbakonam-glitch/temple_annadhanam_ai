import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api and /ws to the FastAPI backend,
// so the frontend never needs a hard-coded backend URL.
const BACKEND = process.env.VITE_BACKEND_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/ws": { target: BACKEND.replace(/^http/, "ws"), ws: true },
    },
  },
});
