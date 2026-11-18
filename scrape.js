// Searches RedNote for each configured keyword, captures the search API JSON the page itself loads,
// filters for matches and appends new ones to hits.jsonl + notifies.
import { chromium } from 'playwright';
import fs from 'node:fs';
import * as C from './config.js';
import { isMatch, normaliseNote } from './filter.js';
import { notify, notifyText } from './notify.js';

const sleep = ms => new Promise(r => setTimeout(r, ms));
const jitter = () => sleep(C.DELAY_MS[0] + Math.random() * (C.DELAY_MS[1] - C.DELAY_MS[0]));

if (!C.KEYWORDS.length) {
  console.error('KEYWORDS is empty. Copy .env.example to .env and set it.');
  process.exit(2);
}

// Alert (at most once per cooldown) that the session needs attention, then exit non-zero.
async function sessionProblem(reason) {
  console.error(`Session problem: ${reason}`);
  let last = 0;
  try { last = Number(fs.readFileSync(C.ALERT_FILE, 'utf8')); } catch {}
  if (Date.now() - last > C.ALERT_COOLDOWN_HOURS * 3600e3) {
    try {
      if (await notifyText(`⚠️ RedNote scraper stopped: ${reason}\nRe-run "npm run login" on a machine with a display and copy auth.json over.`))
        fs.writeFileSync(C.ALERT_FILE, String(Date.now()));
    } catch (e) { console.error('alert failed:', e.message); }
  }
  process.exit(3);
}

if (!fs.existsSync(C.STATE_FILE)) await sessionProblem(`no ${C.STATE_FILE} found`);
const seen = new Set(fs.existsSync(C.SEEN_FILE) ? JSON.parse(fs.readFileSync(C.SEEN_FILE, 'utf8')) : []);

const browser = await chromium.launch({ headless: !process.env.HEADED });
const context = await browser.newContext({ storageState: C.STATE_FILE });
const page = await context.newPage();

let batch = [];
page.on('response', async res => {
  if (!res.url().includes('/api/sns/web/v1/search/notes')) return;
  try {
    const json = await res.json();
    for (const item of json?.data?.items || []) {
      const n = normaliseNote(item);
      if (n) batch.push(n);
    }
  } catch { /* non-JSON / aborted */ }
});

// Best-effort check: redirected to a login/captcha page, or a visible "登录" (log in) button.
async function looksLoggedOut() {
  if (/login|captcha|verify/i.test(page.url())) return true;
  return page.getByText('登录', { exact: true }).first().isVisible().catch(() => false);
}

let newHits = 0;
for (const kw of C.KEYWORDS) {
  batch = [];
  console.log(`> searching: ${kw}`);
  await page.goto(`https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(kw)}&source=web_search_result_notes`);
  await jitter();

  if (await looksLoggedOut()) {
    await browser.close();
    await sessionProblem('RedNote is showing a login/captcha page (session expired or blocked)');
  }
  await page.getByText('最新', { exact: true }).first().click({ timeout: 3000 }).catch(() => {}); // sort newest
  await jitter();

  for (let i = 0; i < C.MAX_SCROLLS; i++) {
    await page.mouse.wheel(0, 2500);
    await jitter();
  }

  for (const n of batch) {
    if (seen.has(n.id)) continue;
    seen.add(n.id);
    if (!isMatch(n.title)) continue;
    const hit = { ...n, keyword: kw, found_at: new Date().toISOString() };
    fs.appendFileSync(C.OUT_FILE, JSON.stringify(hit) + '\n');
    console.log(`  HIT: ${n.title}  ${n.url}`);
    newHits++;
    await notify(hit).catch(e => console.error('  notify failed:', e.message));
  }
}

fs.writeFileSync(C.SEEN_FILE, JSON.stringify([...seen]));
fs.rmSync(C.ALERT_FILE, { force: true });   // healthy run: re-arm the alert
console.log(`Done. ${newHits} new hit(s) -> ${C.OUT_FILE}`);
await browser.close();
