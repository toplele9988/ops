import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The console serves the build under /app/ behind its own login; the JSON API lives at /api/v1.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
