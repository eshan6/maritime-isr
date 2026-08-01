import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API is token-authenticated and local-only. Rather than ship the token to
// the browser, the dev/preview server proxies /api -> the FastAPI backend and
// injects the token header there. The browser talks to /api same-origin, so
// there is no CORS to negotiate and no secret in client code.
const API_TARGET = process.env.MISR_API_URL || "http://127.0.0.1:8000";
const API_TOKEN = process.env.MISR_API_TOKEN || "maritime-isr-dev";

const proxy = {
  "/api": {
    target: API_TARGET,
    changeOrigin: true,
    rewrite: (p) => p.replace(/^\/api/, ""),
    configure: (proxy) => {
      proxy.on("proxyReq", (proxyReq) => {
        proxyReq.setHeader("X-API-Token", API_TOKEN);
      });
    },
  },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
});
