// Sends a hit to Discord (webhook) and/or Telegram (bot API), whichever env vars are set.
//   DISCORD_WEBHOOK_URL
//   TELEGRAM_TOKEN + TELEGRAM_CHAT_ID
const post = (url, body) => fetch(url, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

async function discord(hit) {
  const body = {
    username: 'RedNote slots',
    embeds: [{
      title: (hit.title || '(no title)').slice(0, 256),
      url: hit.url,
      description: [hit.user && `👤 ${hit.user}`, hit.posted && `🕒 ${hit.posted}`, hit.likes && `❤️ ${hit.likes}`, `🔎 ${hit.keyword}`]
        .filter(Boolean).join('  ·  '),
      image: hit.cover ? { url: hit.cover } : undefined,
      timestamp: hit.found_at,
    }],
  };
  let res = await post(process.env.DISCORD_WEBHOOK_URL, body);
  if (res.status === 429) {                       // rate limited: wait as told, retry once
    const wait = Number(res.headers.get('retry-after') || 1) * 1000;
    await new Promise(r => setTimeout(r, wait));
    res = await post(process.env.DISCORD_WEBHOOK_URL, body);
  }
  if (!res.ok) throw new Error(`discord ${res.status}`);
}

async function telegram(hit) {
  const api = m => `https://api.telegram.org/bot${process.env.TELEGRAM_TOKEN}/${m}`;
  const caption = [hit.title, [hit.user, hit.posted, hit.likes && `❤️ ${hit.likes}`].filter(Boolean).join(' · '), hit.url]
    .filter(Boolean).join('\n');
  let res = hit.cover && await post(api('sendPhoto'), { chat_id: process.env.TELEGRAM_CHAT_ID, photo: hit.cover, caption: caption.slice(0, 1024) });
  if (!res || !res.ok) res = await post(api('sendMessage'), { chat_id: process.env.TELEGRAM_CHAT_ID, text: caption });
  if (!res.ok) throw new Error(`telegram ${res.status}`);
}

export async function notify(hit) {
  const jobs = [];
  if (process.env.DISCORD_WEBHOOK_URL) jobs.push(discord(hit));
  if (process.env.TELEGRAM_TOKEN && process.env.TELEGRAM_CHAT_ID) jobs.push(telegram(hit));
  if (!jobs.length) return;
  const results = await Promise.allSettled(jobs);
  const fail = results.find(r => r.status === 'rejected');
  if (fail) throw fail.reason;
}

// Plain-text message (used for operational alerts like "session expired").
export async function notifyText(text) {
  const jobs = [];
  if (process.env.DISCORD_WEBHOOK_URL) jobs.push(post(process.env.DISCORD_WEBHOOK_URL, { username: 'RedNote slots', content: text }));
  if (process.env.TELEGRAM_TOKEN && process.env.TELEGRAM_CHAT_ID)
    jobs.push(post(`https://api.telegram.org/bot${process.env.TELEGRAM_TOKEN}/sendMessage`, { chat_id: process.env.TELEGRAM_CHAT_ID, text }));
  const results = await Promise.allSettled(jobs);
  const bad = results.find(r => r.status === 'rejected' || !r.value.ok);
  if (bad) throw new Error('alert delivery failed');
  return jobs.length > 0;
}
