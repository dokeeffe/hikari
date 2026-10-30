import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const frontendRoot = fileURLToPath(new URL("./hikari/web/frontend/", import.meta.url));
const staticOutput = fileURLToPath(new URL("./hikari/web/static/", import.meta.url));

export default defineConfig({
  root: frontendRoot,
  base: "/static/",
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: staticOutput,
    emptyOutDir: true,
  },
});
