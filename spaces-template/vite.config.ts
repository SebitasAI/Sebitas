import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend is hosted via `convex deploy` (Convex Hosting). The build output
// (`dist/`) is uploaded along with the backend in a single deploy.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
