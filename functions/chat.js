/**
 * OgaAI /chat — Cloudflare Pages Function
 *
 * Ported from:
 *   app/intent_parser.py   → parse()
 *   app/actions.py         → dispatch()
 *   app/response_engine.py → buildReply()
 *
 * LLM priority (free first):
 *   1. Cloudflare Workers AI  — free, no key, Llama 3.1 8B (env.AI binding)
 *   2. DeepSeek               — set DEEPSEEK_API_KEY in CF Pages env
 *   3. Anthropic Claude Haiku — set ANTHROPIC_API_KEY in CF Pages env
 *   4. OpenAI GPT-4o-mini     — set OPENAI_API_KEY in CF Pages env
 */

export async function onRequestPost(context) {
  const { request, env } = context;

  let body;
  try { body = await request.json(); }
  catch { return json({ reply: 'Invalid request', intent: 'UNKNOWN' }, 400); }

  const text      = (body.message || '').trim();
  const sessionId = body.session_id || 'web_user';

  if (!text) return json({ reply: 'Abeg type something na!', intent: 'UNKNOWN' });

  const parsed = parse(text);
  const action = await dispatch(parsed, sessionId);
  const reply  = await buildReply(parsed, action, text, env);

  return json({ reply, intent: parsed.intent, confidence: parsed.confidence, flags: parsed.flags });
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// ─── Intent constants ─────────────────────────────────────────────────────────

const I = {
  CHECK_BALANCE: 'CHECK_BALANCE',
  BUY_AIRTIME:   'BUY_AIRTIME',
  BUY_DATA:      'BUY_DATA',
  SEND_MONEY:    'SEND_MONEY',
  PAY_BILL:      'PAY_BILL',
  GREETING:      'GREETING',
  UNKNOWN:       'UNKNOWN',
};

// ─── Normalisation (Pidgin + basic Yoruba) ────────────────────────────────────

function normalise(text) {
  let t = text.toLowerCase().trim();
  // "2k" → "2000"
  t = t.replace(/(\d+)\s*k\b/g, (_, n) => String(parseInt(n, 10) * 1000));
  // Yoruba
  t = t.replace(/\bsan owo\b/gi,   'pay')
       .replace(/\bowo\b/gi,        'money')
       .replace(/\bina\b/gi,        'electricity')
       .replace(/\bomi\b/gi,        'water')
       .replace(/j[eẹ] k[ií] n\b/gi, 'let me');
  // Pidgin
  t = t.replace(/\babeg\b/gi, '')
       .replace(/\boya\b/gi,  '')
       .replace(/\bnaira\b/gi, 'NGN')
       .replace(/₦/g, 'NGN ');
  return t.replace(/\s+/g, ' ').trim();
}

function extractAmount(text) {
  const m = text.match(/[\d,]+(?:\.\d+)?/);
  return m ? parseFloat(m[0].replace(/,/g, '')) : null;
}

function extractPhone(text) {
  const m = text.replace(/\s/g, '').match(/(?:0|\+?234)([789]\d{9})/);
  return m ? '0' + m[1] : null;
}

function extractNetwork(text) {
  const map = { mtn:'MTN', airtel:'AIR', air:'AIR', glo:'GLO',
                '9mobile':'ETI', etisalat:'ETI', '9mob':'ETI' };
  const lower = text.toLowerCase();
  for (const [alias, code] of Object.entries(map)) {
    if (new RegExp(`\\b${alias}\\b`).test(lower)) return code;
  }
  return null;
}

function detectLang(raw) {
  const words = new Set(raw.toLowerCase().split(/\s+/));
  const yoruba = ['owo','ina','san','gba','emi','jẹ','kí'];
  const pidgin = ['abeg','oya','wetin','dey','na','shey','wahala'];
  if (yoruba.some(w => words.has(w))) return 'yo';
  if (pidgin.some(w => words.has(w))) return 'pcm';
  return 'en';
}

// ─── Pattern matching ─────────────────────────────────────────────────────────

function any(patterns, text) {
  return patterns.some(p => new RegExp(p, 'i').test(text));
}

const GREETING_WORDS = new Set([
  'how far','howfar','hafa','how body','wetin dey','sup','hi','hello','hey',
  'good morning','good afternoon','good evening','oga','how now',
]);

const BALANCE_P  = ['check.{0,20}balance','balance.{0,20}check',
                    'how much.{0,20}(i|my).{0,10}(get|have|dey)',
                    '(i|my).{0,10}(balance|account)'];
const AIRTIME_P  = ['(buy|get|send|recharge).{0,20}airtime','airtime.{0,20}(for|of|worth)',
                    'top.{0,5}up','load.{0,5}airtime','(buy|get).{0,10}credit'];
const DATA_P     = ['(buy|get|subscribe|sub).{0,20}data','data.{0,20}(bundle|plan|sub)',
                    '(get|buy).{0,20}(mb|gb).{0,20}data','internet.{0,20}(data|plan|bundle)'];
const SEND_P     = ['(send|transfer|move).{0,20}(money|naira|NGN|cash|funds)',
                    'send\\s+\\d','transfer\\s+\\d',
                    '(give|pay).{0,20}(my|a)\\s+(guy|friend|brother|sister)'];
const BILL_P     = ['(pay|settle).{0,20}(bill|light|electricity|water|dstv|gotv)',
                    'san owo','recharge.{0,20}(prepaid|meter|nepa|phcn)',
                    'buy.{0,20}(unit|token).{0,20}(light|electricity)'];

// ─── Parser ───────────────────────────────────────────────────────────────────

function parse(text) {
  const base = { raw: text, language: detectLang(text), amount: null,
                 network: null, phone: null, confidence: 0, flags: [] };
  const norm = normalise(text);

  if ([...GREETING_WORDS].some(t => norm.includes(t)))
    return { ...base, intent: I.GREETING, confidence: 0.95 };

  if (any(BALANCE_P, norm))
    return { ...base, intent: I.CHECK_BALANCE, confidence: 0.90 };

  if (any(DATA_P, norm)) {
    const flags = [], amount = extractAmount(norm),
          network = extractNetwork(norm) || extractNetwork(text),
          phone = extractPhone(text);
    if (!amount)   flags.push('missing_amount');
    if (!network)  flags.push('missing_network');
    return { ...base, intent: I.BUY_DATA, amount, network, phone,
             confidence: flags.length ? 0.60 : 0.85, flags };
  }

  if (any(AIRTIME_P, norm)) {
    const flags = [], amount = extractAmount(norm),
          network = extractNetwork(norm) || extractNetwork(text),
          phone = extractPhone(text);
    if (!amount) flags.push('missing_amount');
    return { ...base, intent: I.BUY_AIRTIME, amount, network, phone,
             confidence: flags.length ? 0.60 : 0.85, flags };
  }

  if (any(SEND_P, norm)) {
    const flags = [], amount = extractAmount(norm), phone = extractPhone(text);
    if (!amount) flags.push('missing_amount');
    if (!phone)  flags.push('missing_recipient_phone');
    return { ...base, intent: I.SEND_MONEY, amount, phone,
             confidence: flags.length ? 0.55 : 0.80, flags };
  }

  if (any(BILL_P, norm)) {
    const flags = [], amount = extractAmount(norm);
    if (!amount) flags.push('missing_amount');
    return { ...base, intent: I.PAY_BILL, amount,
             confidence: flags.length ? 0.55 : 0.80, flags };
  }

  return { ...base, intent: I.UNKNOWN, flags: ['needs_llm_provider'] };
}

// ─── Actions ──────────────────────────────────────────────────────────────────

function fmt(n) {
  return n ? Number(n).toLocaleString('en-NG', { maximumFractionDigits: 0 }) : '0';
}

async function dispatch(parsed, sessionId) {
  const { intent, flags, amount, network, phone } = parsed;

  if (intent === I.CHECK_BALANCE)
    return { ok: true, msg: 'Abeg, your account balance na ₦12,500.00 — demo mode.\nAdd COWRIE_CLIENT_ID in CF Pages env to connect your live account.' };

  if (intent === I.BUY_AIRTIME) {
    if (flags.includes('missing_amount'))
      return { ok: false, msg: 'How much airtime you want buy? E.g. *buy me 500 naira airtime*' };
    const net = network || 'MTN', ph = phone || sessionId;
    return { ok: true, msg: `[DEMO] ₦${fmt(amount)} ${net} airtime ordered for ${ph}.\nSet COWRIE_CLIENT_ID to go live.` };
  }

  if (intent === I.BUY_DATA) {
    if (flags.includes('missing_amount'))
      return { ok: false, msg: 'How much data you want? E.g. *get me 1000 naira MTN data*' };
    if (flags.includes('missing_network'))
      return { ok: false, msg: 'Which network? MTN, Airtel, Glo, or 9mobile?' };
    const net = network || 'MTN', ph = phone || sessionId;
    return { ok: true, msg: `[DEMO] ₦${fmt(amount)} ${net} data ordered for ${ph}.\nSet COWRIE_CLIENT_ID to go live.` };
  }

  if (intent === I.SEND_MONEY) {
    if (flags.includes('missing_amount'))
      return { ok: false, msg: 'How much you wan send? E.g. *send 2k to 08012345678*' };
    if (flags.includes('missing_recipient_phone'))
      return { ok: false, msg: 'Who you wan send am to? Gimme their phone number.' };
    return { ok: true, msg: `To confirm: send ₦${fmt(amount)} to ${phone}?\nReply *YES* to confirm or *NO* to cancel.\n_(Bank transfer live in V2)_` };
  }

  if (intent === I.PAY_BILL)
    return { ok: false, msg: 'Bill payment dey come for V2, Oga!\nE-go cover: NEPA/PHCN prepaid, DSTV/GOtv, Water bills.' };

  return { ok: false, msg: '' };
}

// ─── Response engine ──────────────────────────────────────────────────────────

const GREETINGS = [
  'How far, Oga! I be OgaAI — your Pidgin money assistant. Wetin I fit do for you?\n\n• *check my balance*\n• *buy me 500 naira airtime*\n• *get 1000 naira MTN data*\n• *send 2k to 08012345678*',
  'E don do, my people! OgaAI dey here. How I fit help you today?',
  'Oya na! Wetin you need? Airtime, data, transfer? I dey for you.',
];
let greetIdx = 0;

const SYS = 'You are OgaAI, a financial assistant for Nigerians. Reply in warm Nigerian Pidgin unless the user writes formal English. Keep replies short — this is a chat app. Never invent transaction results.';

async function buildReply(parsed, action, raw, env) {
  if (parsed.intent === I.GREETING)
    return GREETINGS[greetIdx++ % GREETINGS.length];

  if (action?.msg) return action.msg;

  if (parsed.intent === I.UNKNOWN || parsed.flags.includes('needs_llm_provider'))
    return await llm(raw, env);

  return 'Oga, I hear you but I no fit do that one right now. Try: *check my balance*, *buy airtime*, or *send money*.';
}

// ─── LLM chain ────────────────────────────────────────────────────────────────
// Free first: CF Workers AI → Groq → Gemini → OpenRouter → Cerebras
// Paid last:  DeepSeek → Anthropic → OpenAI

const FREE_PROVIDERS = [
  ['GROQ_API_KEY',       'https://api.groq.com/openai/v1/chat/completions',                         'llama-3.1-8b-instant'],
  ['GEMINI_API_KEY',     'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions', 'gemini-2.0-flash-lite'],
  ['OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1/chat/completions',                           'meta-llama/llama-3.1-8b-instruct:free'],
  ['CEREBRAS_API_KEY',   'https://api.cerebras.ai/v1/chat/completions',                             'llama3.1-8b'],
];

const PAID_PROVIDERS = [
  ['DEEPSEEK_API_KEY', 'https://api.deepseek.com/v1/chat/completions', 'deepseek-chat'],
  ['OPENAI_API_KEY',   'https://api.openai.com/v1/chat/completions',   'gpt-4o-mini'],
];

async function llm(message, env) {
  // CF Workers AI — always free, no key needed
  if (env?.AI) {
    try {
      const r = await env.AI.run('@cf/meta/llama-3.1-8b-instruct', {
        messages: [{ role: 'system', content: SYS }, { role: 'user', content: message }],
        max_tokens: 300,
      });
      if (r?.response) return r.response;
    } catch { /* fall through */ }
  }

  // Free + paid providers (all OpenAI-compatible except Anthropic)
  for (const [envKey, url, model] of [...FREE_PROVIDERS, ...PAID_PROVIDERS]) {
    const key = env?.[envKey];
    if (key) {
      const reply = await openAICompat(url, key, model, message);
      if (reply) return reply;
    }
  }

  // Anthropic (different request format)
  if (env?.ANTHROPIC_API_KEY) {
    try {
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'x-api-key': env.ANTHROPIC_API_KEY,
                   'anthropic-version': '2023-06-01',
                   'content-type': 'application/json' },
        body: JSON.stringify({ model: 'claude-haiku-4-5-20251001', max_tokens: 300,
                               system: SYS, messages: [{ role: 'user', content: message }] }),
      });
      const d = await r.json();
      if (d?.content?.[0]?.text) return d.content[0].text;
    } catch { /* fall through */ }
  }

  return 'Hmm, I no fully understand wetin you mean. Try: *check my balance*, *buy 500 airtime*, or *send 2k to 08012345678*.';
}

async function openAICompat(url, apiKey, model, message) {
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model, max_tokens: 300,
        messages: [{ role: 'system', content: SYS }, { role: 'user', content: message }],
      }),
    });
    const d = await r.json();
    return d?.choices?.[0]?.message?.content || '';
  } catch { return ''; }
}
