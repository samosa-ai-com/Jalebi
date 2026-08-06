/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:3456",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/vitest.setup.ts",
  },
});
