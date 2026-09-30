// Renders a promo page to mp4 frame by frame (deterministic, independent of machine speed).
// Usage: cd docs/promo && bun --install=force capture.mjs argos-intro.en.html argos-intro.en.mp4
// Needs ffmpeg with libx264. Uses the installed Google Chrome unless CHROME_PATH is set.
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const [src, out, fpsArg] = process.argv.slice(2);
if (!src || !out) {
  console.error("usage: capture.mjs <page.html> <out.mp4> [fps=30]");
  process.exit(1);
}
const fps = Number(fpsArg ?? 30);

const browser = await chromium.launch(
  process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" },
);
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
await page.goto(`${pathToFileURL(resolve(src)).href}#p0`);
await page.waitForFunction(() => !!window.__argosTimeline, null, { polling: 200, timeout: 120000 });
await page.evaluate(() => document.fonts.ready.then(() => true));
// Stage fills the viewport; player controls hidden.
await page.addStyleTag({
  content: `body{padding:0!important;margin:0;overflow:hidden}.shell{max-width:none;gap:0}
  .vp{position:fixed!important;left:0;top:0;width:1920px!important;height:1080px!important;border-radius:0!important;box-shadow:none!important}
  .ctrl{display:none!important}`,
});
await page.evaluate(() => window.dispatchEvent(new Event("resize")));
await page.waitForTimeout(300);

const cdp = await page.context().newCDPSession(page);
const duration = await page.evaluate(() => window.__argosTimeline.duration());
const frames = Math.round(duration * fps);
const ff = spawn(
  "ffmpeg",
  ["-v", "error", "-y", "-f", "image2pipe", "-framerate", String(fps), "-i", "-",
    "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
  { stdio: ["pipe", "inherit", "inherit"] },
);

for (let i = 0; i < frames; i++) {
  // Return nothing: serializing the GSAP timeline back to Node stalls for seconds per frame.
  await page.evaluate((t) => {
    window.__argosTimeline.time(t);
  }, i / fps);
  const { data } = await cdp.send("Page.captureScreenshot", { format: "jpeg", quality: 94, optimizeForSpeed: true });
  if (!ff.stdin.write(Buffer.from(data, "base64"))) await new Promise((r) => ff.stdin.once("drain", r));
  if (i % 300 === 0) console.log(`${out}: ${i}/${frames}`);
}
ff.stdin.end();
await new Promise((r) => ff.on("close", r));
await browser.close();
console.log(`done ${out} (${frames} frames)`);
