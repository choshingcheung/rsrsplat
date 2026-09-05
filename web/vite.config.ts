import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      // The protocol fixtures live outside web/ on purpose: they are shared with the
      // Python service, which tests against the same files.
      allow: [".."],
    },
  },
});
