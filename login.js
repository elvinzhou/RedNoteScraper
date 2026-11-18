// One-time: opens a visible browser. Log in to RedNote yourself (QR / SMS), then press Enter here.
import { chromium } from 'playwright';
import readline from 'node:readline/promises';
import { STATE_FILE } from './config.js';

const browser = await chromium.launch({ headless: false });
const context = await browser.newContext();
const page = await context.newPage();
await page.goto('https://www.xiaohongshu.com/explore');
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
await rl.question('Log in in the browser window, then press Enter here to save the session... ');
await context.storageState({ path: STATE_FILE });
console.log(`Saved session to ${STATE_FILE}`);
rl.close();
await browser.close();
