import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

const envDir = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  const configEnv = loadEnv(mode, envDir, "CONTEXTMESH_API_PROXY_TARGET");
  const target =
    process.env.CONTEXTMESH_API_PROXY_TARGET ??
    configEnv.CONTEXTMESH_API_PROXY_TARGET ??
    "http://127.0.0.1:8000";

  return {
    envDir,
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: { "/api": { target }, "/health": { target } },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      restoreMocks: true,
      clearMocks: true,
    },
  };
});
