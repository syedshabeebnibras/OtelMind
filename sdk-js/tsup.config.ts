import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["cjs", "esm"],
  dts: true,
  splitting: false,
  sourcemap: true,
  clean: true,
  target: "node18",
  // Peer deps must not be bundled.
  external: ["openai", "@anthropic-ai/sdk"],
  // Node built-ins — available in Node 18, do not bundle.
  noExternal: [],
  esbuildOptions(options) {
    options.platform = "node";
  },
});
