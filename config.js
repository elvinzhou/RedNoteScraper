// All settings come from environment variables (or a .env file in the working directory).
// See .env.example. Variables already set in the environment win over .env.
import fs from 'node:fs';

try {
  for (const line of fs.readFileSync('.env', 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!m || line.trim().startsWith('#')) continue;
    process.env[m[1]] ??= m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
} catch { /* no .env: fine */ }

const env = process.env;
const list = (name, sep = ',') => (env[name] || '').split(sep).map(s => s.trim()).filter(Boolean);
const regexes = name => list(name).map(s => new RegExp(s, 'i'));
const num = (name, d) => (env[name] !== undefined && env[name] !== '' ? Number(env[name]) : d);

export const KEYWORDS = list('KEYWORDS');             // search terms, comma-separated
export const TOPIC_TERMS = regexes('TOPIC_TERMS');    // title must match one of these...
export const EVENT_TERMS = regexes('EVENT_TERMS');    // ...and one of these (leave either empty to skip that check)

export const MAX_SCROLLS = num('MAX_SCROLLS', 4);
export const DELAY_MS = [num('DELAY_MIN_MS', 2500), num('DELAY_MAX_MS', 5000)];
export const ALERT_COOLDOWN_HOURS = num('ALERT_COOLDOWN_HOURS', 24);
export const DATA_DIR = env.DATA_DIR || '.';
export const OUT_FILE = `${DATA_DIR}/hits.jsonl`;
export const SEEN_FILE = `${DATA_DIR}/seen.json`;
export const STATE_FILE = env.STATE_FILE || `${DATA_DIR}/auth.json`;
export const ALERT_FILE = `${DATA_DIR}/.session_alert`;
