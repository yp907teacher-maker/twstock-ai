import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 一般建置：輸出到後端 static 目錄，由 FastAPI 提供
// 線上展示版：VITE_STATIC=1 npm run build -- --mode static，輸出到 dist-static（相對路徑）
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === "static" ? "./" : "/",
  build: mode === "static"
    ? { outDir: "dist-static", emptyOutDir: true }
    : { outDir: "../backend/app/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
}));
