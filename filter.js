import { TOPIC_TERMS, EVENT_TERMS } from './config.js';

// Reads config at call time so tests can pass their own term lists.
export function isMatch(text, topic = TOPIC_TERMS, event = EVENT_TERMS) {
  const t = text || '';
  return (!topic.length || topic.some(r => r.test(t))) && (!event.length || event.some(r => r.test(t)));
}

// Normalise one item from RedNote's search API response.
export function normaliseNote(item) {
  const card = item.note_card || {};
  const id = item.id || card.note_id;
  if (!id) return null;
  return {
    id,
    title: card.display_title || '',
    user: card.user?.nickname || card.user?.nick_name || '',
    likes: card.interact_info?.liked_count || '',
    type: card.type || '',
    cover: card.cover?.url_default || card.cover?.url_pre || card.cover?.url || '',
    posted: (card.corner_tag_info || []).find(t => t.type === 'publish_time')?.text || '',
    avatar: card.user?.avatar || '',
    url: `https://www.xiaohongshu.com/explore/${id}` + (item.xsec_token ? `?xsec_token=${encodeURIComponent(item.xsec_token)}&xsec_source=pc_search` : ''),
  };
}
