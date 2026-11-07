import { chromium } from 'playwright';
import { mkdir, rename } from 'node:fs/promises';
import path from 'node:path';

const project = path.resolve(import.meta.dirname, '..');
const recordingDir = path.join(project, 'recordings', 'staging');
const destination = path.join(project, 'recordings', 'raw', 'incident-evidence-tour.webm');
await mkdir(recordingDir, { recursive: true });
await mkdir(path.dirname(destination), { recursive: true });

const launchOptions = { headless: true, slowMo: 80 };
if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) {
  launchOptions.executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
}
const browser = await chromium.launch(launchOptions);
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
  recordVideo: { dir: recordingDir, size: { width: 1920, height: 1080 } },
});
const page = await context.newPage();

try {
  await page.goto('https://flip-green-check-report-2026.dualheron.chatgpt.site/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2800);
  for (const selector of ['#evidence', '.contract-section', '.timeline', '.architecture', '.closing']) {
    await page.locator(selector).scrollIntoViewIfNeeded();
    await page.waitForTimeout(3000);
  }
  await page.goto('https://github.com/Abhigyan-Shekhar/flip/pull/11/files', { waitUntil: 'domcontentloaded' });
  if (!(await page.title()).includes('Pull Request #11')) throw new Error(`Unexpected source page: ${await page.title()}`);
  await page.waitForTimeout(3500);
  await page.locator('body').evaluate((el) => el.scrollTo({ top: 0, behavior: 'instant' }));
  await page.waitForTimeout(1800);
} finally {
  const video = page.video();
  await context.close();
  await browser.close();
  if (video) await rename(await video.path(), destination);
}

console.log(`Recorded the public report and public PR #11 diff: ${destination}`);
