import assert from 'node:assert';
process.env.DISCORD_WEBHOOK_URL = 'https://discord.test/hook';
process.env.TELEGRAM_TOKEN = 'T'; process.env.TELEGRAM_CHAT_ID = '1';
process.env.KEYWORDS = 'a, b ,c';
process.env.TOPIC_TERMS = 'usvisascheduling,美签';
process.env.EVENT_TERMS = '放号,new slots?';
const C = await import('./config.js');
const { isMatch, normaliseNote } = await import('./filter.js');

assert.deepEqual(C.KEYWORDS, ['a', 'b', 'c']);
assert(isMatch('今天usvisascheduling刷到放号了', C.TOPIC_TERMS, C.EVENT_TERMS));
assert(isMatch('NEW SLOTS for 美签', C.TOPIC_TERMS, C.EVENT_TERMS));
assert(!isMatch('美签材料清单', C.TOPIC_TERMS, C.EVENT_TERMS));
assert(!isMatch('今天吃什么', C.TOPIC_TERMS, C.EVENT_TERMS));
assert(isMatch('anything', [], []));                       // empty term lists = no filtering
const n = normaliseNote({ id: 'abc', xsec_token: 't', note_card: { display_title: 'x', user: { nickname: 'u' } } });
assert.equal(n.id, 'abc'); assert(n.url.includes('xsec_token=t'));

const calls = [];
globalThis.fetch = async (url, o) => { calls.push({ url, body: JSON.parse(o.body) }); return { ok: true, status: 200, headers: new Headers() }; };
const { notify, notifyText } = await import('./notify.js');
await notify({ title: 't', url: 'https://x/y', user: 'u', cover: 'https://img/c.jpg', keyword: 'k', found_at: new Date().toISOString() });
assert(calls.some(c => c.url === 'https://discord.test/hook' && c.body.embeds[0].image.url === 'https://img/c.jpg'));
assert(calls.some(c => c.url.endsWith('/sendPhoto')));
calls.length = 0;
assert.equal(await notifyText('hello'), true);
assert(calls.some(c => c.body.content === 'hello') && calls.some(c => c.body.text === 'hello'));
console.log('all ok');
