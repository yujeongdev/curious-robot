import { createHash } from "node:crypto";
import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { chromium } from "@playwright/test";
import { PNG } from "pngjs";

type Options = { run: string; output: string; width: number; height: number; fps: number };

const MAX_CAPTURE_STEPS = 120;

function captureSequences(events: unknown[] | null, maxSequence: number): number[] {
  if (!events || maxSequence <= MAX_CAPTURE_STEPS) {
    return Array.from({ length: maxSequence }, (_, index) => index + 1);
  }
  const important = new Set<number>([1, maxSequence]);
  for (const value of events) {
    if (!value || typeof value !== "object") continue;
    const event = value as { sequence?: unknown; type?: unknown };
    if (typeof event.sequence !== "number" || typeof event.type !== "string") continue;
    if (event.type.startsWith("stage.") || event.type === "belief.updated" || event.type.startsWith("action.") || event.type.startsWith("run.")) {
      important.add(event.sequence);
    }
  }
  const remaining = Math.max(0, MAX_CAPTURE_STEPS - important.size);
  for (let index = 0; index < remaining; index += 1) {
    important.add(Math.max(1, Math.round(1 + (index * (maxSequence - 1)) / Math.max(1, remaining - 1))));
  }
  return [...important].sort((left, right) => left - right).slice(0, MAX_CAPTURE_STEPS);
}

function args(): Options {
  const values = process.argv.slice(2);
  const get = (name: string, fallback?: string) => {
    const index = values.indexOf(`--${name}`);
    return index >= 0 ? values[index + 1] : fallback;
  };
  const run = get("run");
  const output = get("output");
  if (!run || !output) throw new Error("Usage: pnpm record:demo --run artifacts/<run_id> --output docs/media/demo.mp4 [--width 1280 --height 720 --fps 30]");
  return { run, output, width: Number(get("width", "1280")), height: Number(get("height", "720")), fps: Number(get("fps", "30")) };
}

function assertCommand(command: string, hint: string) {
  try { execFileSync(command, ["-version"], { stdio: "ignore" }); }
  catch { throw new Error(`${command} is required. ${hint}`); }
}

function assertNonBlankViewport(buffer: Buffer, checkpoint: number) {
  const png = PNG.sync.read(buffer);
  let luminanceSum = 0;
  let luminanceSquared = 0;
  let edgeCount = 0;
  const sample = (x: number, y: number) => {
    const index = (y * png.width + x) * 4;
    return ((png.data[index] ?? 0) * .2126 + (png.data[index + 1] ?? 0) * .7152 + (png.data[index + 2] ?? 0) * .0722);
  };
  const stride = 4;
  let count = 0;
  for (let y = 0; y < png.height - stride; y += stride) {
    for (let x = 0; x < png.width - stride; x += stride) {
      const value = sample(x, y);
      luminanceSum += value;
      luminanceSquared += value * value;
      if (Math.abs(value - sample(x + stride, y)) > 14 || Math.abs(value - sample(x, y + stride)) > 14) edgeCount += 1;
      count += 1;
    }
  }
  const mean = luminanceSum / count;
  const variance = luminanceSquared / count - mean * mean;
  if (mean < 3 || variance < 18 || edgeCount < 20) {
    throw new Error(`Blank/stale WebGL viewport at checkpoint ${checkpoint}: luminance=${mean.toFixed(1)}, variance=${variance.toFixed(1)}, edges=${edgeCount}`);
  }
}

async function waitForServer(url: string) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // Vite preview has not bound the port yet.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  throw new Error(`Timed out waiting for capture server at ${url}`);
}

async function main() {
  const options = args();
  assertCommand("ffmpeg", "Install ffmpeg and ensure it is on PATH.");
  if (!existsSync(resolve("dist/index.html"))) throw new Error("Frontend build missing. Run `corepack pnpm build` first.");
  const output = resolve(options.output);
  const runDirectory = resolve(options.run);
  const eventLog = join(runDirectory, "events.ndjson");
  const fixtureMode = options.run.startsWith("fixture:");
  if (!fixtureMode && !existsSync(eventLog)) throw new Error(`Committed event log not found: ${eventLog}`);
  const captureEvents = fixtureMode
    ? null
    : readFileSync(eventLog, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line) as unknown);
  if (captureEvents && captureEvents.length === 0) throw new Error(`Committed event log is empty: ${eventLog}`);
  const temporary = resolve(".capture-frames");
  rmSync(temporary, { recursive: true, force: true });
  mkdirSync(temporary, { recursive: true });
  mkdirSync(dirname(output), { recursive: true });

  const port = 4179;
  const server = spawn(process.execPath, [resolve("node_modules/vite/bin/vite.js"), "preview", "--host", "127.0.0.1", "--port", String(port)], { stdio: "pipe" });
  await waitForServer(`http://127.0.0.1:${port}`);
  const browser = await chromium.launch({ headless: true }).catch((error) => {
    throw new Error(`Playwright Chromium unavailable. Run \`corepack pnpm exec playwright install chromium\`. ${String(error)}`);
  });
  try {
    const page = await browser.newPage({ viewport: { width: options.width, height: options.height }, deviceScaleFactor: 1 });
    if (captureEvents) {
      await page.addInitScript((events) => {
        (globalThis as { __CURIOUS_CAPTURE_EVENTS__?: unknown[] }).__CURIOUS_CAPTURE_EVENTS__ = events as unknown[];
      }, captureEvents);
    }
    const captureRunId = fixtureMode ? "demo" : basename(runDirectory);
    await page.goto(`http://127.0.0.1:${port}/runs/${encodeURIComponent(captureRunId)}?fixture=true`, { waitUntil: "networkidle" });
    await page.evaluate("document.documentElement.setAttribute('data-capture', 'true')");
    await page.getByRole("tab", { name: "Replay" }).click();
    const slider = page.getByRole("slider", { name: "Replay sequence" });
    const max = Number(await slider.getAttribute("max"));
    if (!Number.isFinite(max) || max < 2) throw new Error("Replay fixture did not load a valid event sequence.");
    let frameIndex = 0;
    const validatedScenes = new Set<string>();
    const captureInputFps = Math.min(options.fps, 8);
    const sequences = captureSequences(captureEvents, max);
    for (const sequence of sequences) {
      await slider.fill(String(sequence));
      const expectedFrames: Record<string, number> = {};
      for (const value of captureEvents?.slice(0, sequence) ?? []) {
        const event = value as { type?: unknown; payload?: { frame_id?: number; scene?: string } };
        if (event.type === "simulation.visual_frame" && event.payload?.scene && event.payload.frame_id !== undefined) {
          expectedFrames[event.payload.scene] = event.payload.frame_id;
        }
      }
      await page.waitForFunction(
        `(() => {
          const expectedFrames = ${JSON.stringify(expectedFrames)};
          const sequenceLabel = document.querySelector('.replay-bar .mono')?.textContent ?? '';
          return sequenceLabel.includes('SEQ ${sequence} /') && Object.entries(expectedFrames).every(([scene, frameId]) => {
            const viewport = document.querySelector('.canvas-shell[data-world-scene="' + scene + '"]');
            return viewport?.getAttribute('data-frame-id') === String(frameId);
          });
        })()`,
      );
      await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
      for (const scene of Object.keys(expectedFrames)) {
        if (validatedScenes.has(scene)) continue;
        const viewport = page.locator(`.canvas-shell[data-world-scene="${scene}"]`);
        if (await viewport.locator("canvas").count() > 0) {
          assertNonBlankViewport(await viewport.screenshot(), sequence);
          validatedScenes.add(scene);
        }
      }
      await page.screenshot({ path: join(temporary, `frame-${String(frameIndex).padStart(6, "0")}.jpg`), type: "jpeg", quality: 95 });
      frameIndex += 1;
    }
    for (const requiredScene of ["truth", "belief"]) {
      if (!validatedScenes.has(requiredScene)) {
        throw new Error(`WebGL viewport validation incomplete: ${requiredScene} pane never rendered.`);
      }
    }
    execFileSync("ffmpeg", ["-y", "-framerate", String(captureInputFps), "-i", join(temporary, "frame-%06d.jpg"), "-r", String(options.fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", output], { stdio: "inherit" });
    const gif = output.replace(/\.mp4$/i, ".gif");
    execFileSync("ffmpeg", ["-y", "-i", output, "-vf", "fps=12,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer", gif], { stdio: "inherit" });
    const bytes = readFileSync(output);
    writeFileSync(`${output}.json`, JSON.stringify({ source_run: fixtureMode ? options.run : basename(runDirectory), width: options.width, height: options.height, fps: options.fps, source_frames: frameIndex, source_fps: captureInputFps, sha256: createHash("sha256").update(bytes).digest("hex") }, null, 2));
    rmSync(temporary, { recursive: true, force: true });
  } finally {
    await browser.close();
    server.kill("SIGTERM");
  }
}

main().catch((error) => { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; });
