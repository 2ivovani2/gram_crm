import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const output = resolve(root, "services/crm/static/learning");
const entry = resolve(here, "src/index.ts");
const remotion = resolve(here, "node_modules/.bin/remotion");
const tutorials = [
  "connect-bot-channel",
  "first-welcome",
  "media-buttons-links",
  "use-template",
  "audience-broadcast",
];

mkdirSync(output, { recursive: true });
for (const id of tutorials) {
  execFileSync(remotion, ["render", entry, id, resolve(output, `${id}.mp4`), "--codec=h264", "--crf=25", "--pixel-format=yuv420p", "--concurrency=4", `--public-dir=${resolve(here, "public")}`], { cwd: root, stdio: "inherit" });
  execFileSync(remotion, ["still", entry, `${id}-poster`, resolve(output, `${id}.jpg`), "--image-format=jpeg", "--jpeg-quality=86", `--public-dir=${resolve(here, "public")}`], { cwd: root, stdio: "inherit" });
}
