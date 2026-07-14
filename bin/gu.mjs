#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const pyScript = join(scriptDir, "..", "src", "grok_usage.py");
const python = process.platform === "win32" ? "python" : "python3";

const result = spawnSync(python, [pyScript, ...process.argv.slice(2)], {
  stdio: "inherit",
});

if (result.error && result.error.code === "ENOENT") {
  const hint =
    process.platform === "win32"
      ? "Install Python 3 from https://www.python.org/downloads/ and ensure python is on PATH."
      : "Install Python 3 (brew install python3 on macOS, apt install python3 on Linux).";
  process.stderr.write(`Error: ${python} not found. ${hint}\n`);
  process.exit(1);
}

process.exit(result.status ?? 1);
