"""med.comix comment bot.

Every run: read recent Reels, find comment threads where the newest message is
from a viewer (not from us), ask Claude for a short reply that keeps the
conversation going, and post it as a reply in that thread.

Stateless: a thread is answered only when its last message is not ours, so a
run never answers the same message twice. Uses only the Python standard library.

Env:
  IG_TOKEN           Instagram API (Instagram Login) long-lived token   [secret]
  ANTHROPIC_API_KEY  Claude API key                                     [secret]
  DRY_RUN            "1" = only show the replies, do not post them
  MODEL              Claude model id (default claude-haiku-5-5)
  WINDOW_HOURS       only answer messages newer than this (default 6)
  MAX_REPLIES        max replies per run (default 5)
  MAX_PER_THREAD     max bot replies inside one thread (default 3)
  MEDIA_LIMIT        how many latest posts to scan (default 8)
"""
import json, os, random, re, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

IG = 'https://graph.instagram.com/v23.0'
TOKEN = os.environ.get('IG_TOKEN', '').strip()
KEY = os.environ.get('ANTHROPIC_API_KEY', '').strip()
DRY = os.environ.get('DRY_RUN', '0').strip().lower() in ('1', 'true', 'yes')
MODEL = os.environ.get('MODEL', '').strip() or 'claude-haiku-5-5'
WINDOW = float(os.environ.get('WINDOW_HOURS', '') or (48 if DRY else 6))
MAX_REPLIES = int(os.environ.get('MAX_REPLIES', '') or 5)
MAX_PER_THREAD = int(os.environ.get('MAX_PER_THREAD', '') or 3)
MEDIA_LIMIT = int(os.environ.get('MEDIA_LIMIT', '') or 8)
HERE = os.path.dirname(os.path.abspath(__file__))
SUMMARY = os.environ.get('GITHUB_STEP_SUMMARY')


def http(url, data=None, headers=None, method=None):
    body = None
    if data is not None:
        body = json.dumps(data).encode() if headers and headers.get('content-type') == 'application/json' else urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method or ('POST' if body else 'GET'))
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors='replace')
            if e.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(5 * (attempt + 1)); continue
            raise RuntimeError(f'HTTP {e.code}: {msg[:400]}') from None


def ig_get(path, **params):
    params['access_token'] = TOKEN
    return http(f'{IG}/{path}?{urllib.parse.urlencode(params)}')


def ig_all(path, pages=6, **params):
    out, res = [], ig_get(path, **params)
    for _ in range(pages):
        out += res.get('data', [])
        nxt = res.get('paging', {}).get('next')
        if not nxt: break
        res = http(nxt)
    return out


def ts(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z')


SYSTEM = """Sen med.comix Instagram sahifasining admini nomidan izohlarga javob yozasan.
med.comix — tana va tibbiyot haqidagi qiziqarli faktlarni o'zbek tilida komiks uslubida ko'rsatadigan Reels sahifa.

Maqsad: odam yana izoh yozishi uchun unga qisqa va qiziqarli savol berish.
Javob qoidasi:
- Faqat BITTA qisqa savol, 4–12 so'z. Tushuntirish, maqtov, fakt yoki uzun gap yozma.
- Savol aynan uning izohidan kelib chiqsin, unga javob berish oson va qiziq bo'lsin.
- Oddiy, jonli, do'stona so'zlashuv tili. Kitobiy yoki g'alati so'zlar yo'q.
- Har safar boshqacha savol ber, bir xil shablonni takrorlama.
- Odam qaysi tilda va alifboda yozgan bo'lsa, o'shanda yoz (kirillda yozsa — kirillda, ruscha bo'lsa — ruscha).
- Emoji odatda qo'yma. Faqat izohda hazil, kulgi yoki emoji bo'lsa, bitta mos emoji qo'yish mumkin.

Misollar:
- Izoh: "5000" → Qaysi joyda yana 1000 qo'shib yubordingiz?
- Izoh: "menda chiqdi" → Ikkala qo'lingizda ham chiqdimi?
- Izoh: "voy rostdanmi 😂" → Do'stingiz ham ishondimi, sinab ko'rdingizmi? 😂

Qat'iy qoidalar:
- Tashxis qo'yma, dori tavsiya qilma. Jiddiy sog'liq savoliga: "Bu haqda shifokorga ko'ringaningiz ma'qul, o'zingizni qanday his qilyapsiz?" kabi qisqa javob ber.
- Konkurs, sovg'a, Telegram Premium haqida hech narsa va'da qilma.
- Video haqidagi faktlarni faqat "Video haqida" qismidan ol, o'ylab topma.
- "Botmisan?" deb jiddiy so'rashsa, rostini ayt: admin yordamchisi, sun'iy intellekt.
- Haqorat, spam, reklama, mazmunsiz belgilar, siyosat yoki boshqa odamni kamsitadigan izoh bo'lsa — faqat SKIP deb yoz.
- Faqat savolning o'zini yoz: qo'shtirnoq, izoh, @username yo'q."""


def claude(video, thread, target):
    convo = '\n'.join(f"{'Biz (med.comix)' if m['ours'] else '@' + (m['username'] or 'tomoshabin')}: {m['text']}" for m in thread)
    user = f"""Video haqida:
{video}

Izohlar zanjiri (eskidan yangiga):
{convo}

Endi @{target['username'] or 'tomoshabin'} ning oxirgi izohiga javob yoz (yoki SKIP).
Faqat bitta qisqa savol yoz."""
    res = http('https://api.anthropic.com/v1/messages', {
        'model': MODEL, 'max_tokens': 400, 'system': SYSTEM,
        'thinking': {'type': 'disabled'},  # short replies need no thinking; thinking would eat max_tokens
        'messages': [{'role': 'user', 'content': user}],
    }, {'content-type': 'application/json', 'x-api-key': KEY, 'anthropic-version': '2023-06-01'})
    txt = ''.join(b.get('text', '') for b in res.get('content', []) if b.get('type') == 'text').strip().strip('"«»').strip()
    emo = re.findall(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]', txt)
    for e in emo[1:]:
        txt = txt.replace(e, '', 1)
    txt = re.sub(r'\s{2,}', ' ', txt).strip()
    m = re.match(r'(.+?\?)(\s*[\U0001F300-\U0001FAFF\u2600-\u27BF])?', txt, re.S)
    if m:                                               # keep only the first question (and its emoji, if any)
        txt = (m.group(1).split('. ')[-1] + (m.group(2) or '')).strip()
    elif len(txt) > 100:
        txt = re.split(r'(?<=[.!])\s', txt)[0]
    if res.get('stop_reason') == 'max_tokens' or not txt:
        raise RuntimeError(f"javob chala: stop_reason={res.get('stop_reason')}, matn={txt[:40]!r}")
    return txt


QWORDS = ('?', 'nega', 'nima', 'qanday', 'qanaqa', 'qachon', 'qayer', 'nechta', 'nimaga', 'rostdan', 'нега', 'нима', 'қандай', 'почему', 'как', 'зачем')


def score(msg, ours, now):
    t = msg['text'].lower()
    letters = sum(ch.isalnum() for ch in t)
    sc = 0.0
    if ours: sc += 5                                    # they answered us: keep the conversation going
    if any(q in t for q in QWORDS): sc += 3             # questions
    sc += min(msg.get('like_count') or 0, 20) * 0.5     # liked comments are seen by more people
    sc += min(letters, 120) / 40                        # some substance
    if letters < 2: sc -= 3                             # emoji-only / symbols
    age_h = (now - ts(msg['timestamp'])).total_seconds() / 3600
    sc += max(0.0, 2 - age_h)                           # fresh comments first
    return sc


def main():
    if not TOKEN or not KEY:
        sys.exit('IG_TOKEN yoki ANTHROPIC_API_KEY topilmadi (GitHub Secrets).')
    notes = json.load(open(os.path.join(HERE, 'videos.json'), encoding='utf-8'))
    me = ig_get('me', fields='id,user_id,username')
    my_name = me['username'].lower()
    my_ids = {str(v) for v in (me.get('id'), me.get('user_id')) if v}
    def mark(x):
        fr = x.get('from') or {}
        x['username'] = x.get('username') or fr.get('username') or ''
        x['ours'] = x['username'].lower() == my_name or str(fr.get('id') or '') in my_ids
        x['text'] = (x.get('text') or '').strip()
        return x
    print(f"Akkaunt: @{me['username']} | model {MODEL} | {'SINOV (javob yozmaydi)' if DRY else 'JONLI'} | oyna {WINDOW} soat")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW)
    media = ig_get('me/media', fields='id,caption,permalink,timestamp,comments_count,shortcode', limit=MEDIA_LIMIT).get('data', [])
    print(f"Postlar: {len(media)} ta")
    now = datetime.now(timezone.utc)
    cands = []
    for m in media:
        code = m.get('shortcode') or m.get('permalink', '').rstrip('/').split('/')[-1]
        video = notes.get(code) or notes.get('caption:' + (m.get('caption') or '').strip()) or f"Video matni (caption): {m.get('caption', '')}"
        comments = ig_all(f"{m['id']}/comments", fields='id,text,timestamp,username,from,like_count,parent_id,replies.limit(50){id,text,timestamp,username,from,like_count}', limit=50)
        print(f"[{code}] {m.get('timestamp', '')[:16]} | Instagram'dagi izohlar: {m.get('comments_count')} | API bergan: {len(comments)}"
              + (f" | ro'yxat boshi {comments[0]['timestamp'][:16]}, oxiri {comments[-1]['timestamp'][:16]}" if comments else ''))
        for c in comments:
            if c.get('parent_id'): continue                  # a reply listed on its own: it is handled inside its thread
            if mark(c)['ours']: continue                     # our own top-level comment
            thread = [c] + sorted((mark(r) for r in (c.get('replies') or {}).get('data', [])), key=lambda r: r['timestamp'])
            last = thread[-1]
            if last['ours'] or not last['text'] or ts(last['timestamp']) < cutoff: continue
            ours = sum(x['ours'] for x in thread)
            if ours >= MAX_PER_THREAD: continue
            cands.append((score(last, ours, now), code, video, c, thread, last))
    # Most promising first: people already talking with us, questions, liked and longer comments, fresh ones
    cands.sort(key=lambda z: -z[0])
    print(f"Javob kutayotgan izohlar: {len(cands)} ta, shulardan eng yaxshi {MAX_REPLIES} tasiga javob beriladi")
    done, rows, errs, calls = 0, [], 0, 0
    for sc, code, video, c, thread, last in cands:
        if done >= MAX_REPLIES or calls >= MAX_REPLIES + 15: break
        # read the thread again right now, straight from the replies edge, so we never answer twice or answer ourselves
        try:
            live = [mark(r) for r in ig_all(f"{c['id']}/replies", pages=2, fields='id,text,timestamp,username,from', limit=50)]
        except Exception as e:
            print('Javoblarni o\'qib bo\'lmadi, o\'tkazib yuborildi:', e); continue
        thread = [c] + sorted(live, key=lambda r: r['timestamp'])
        last = thread[-1]
        if last['ours'] or sum(x['ours'] for x in thread) >= MAX_PER_THREAD: continue
        calls += 1
        t0 = time.time()
        try:
            reply = claude(video, thread, last); errs = 0
            print(f"  Claude {time.time() - t0:.1f}s (ball {sc:.1f})")
        except Exception as e:
            errs += 1; print('Claude xatosi:', repr(e)[:300])
            if errs >= 3: sys.exit('Claude 3 marta ketma-ket xato berdi, to\'xtatildi.')
            continue
        if not reply or reply.upper().startswith('SKIP'):
            rows.append((last['username'], last['text'], '— (javobsiz)')); continue
        reply = (f"@{last['username']} {reply}" if last['username'] else reply)[:300]
        if not DRY:
            try:
                http(f"{IG}/{c['id']}/replies", {'message': reply, 'access_token': TOKEN})
            except Exception as e:
                print('Instagram xatosi:', e); continue
            time.sleep(4)
        done += 1
        rows.append((last['username'], last['text'], reply))
        print(f"[{code}] @{last['username']}: {last['text'][:80]!r}\n   -> {reply}")
    print(f"Tayyor: {done} ta javob {'(yozilmadi, sinov)' if DRY else 'yozildi'}.")
    if SUMMARY:
        with open(SUMMARY, 'a', encoding='utf-8') as f:
            f.write(f"### {'Sinov' if DRY else 'Jonli'}: {done} ta javob\n\n| Kim | Izoh | Bot javobi |\n|---|---|---|\n")
            for u, t, r in rows:
                cell = lambda s: s.replace('|', '/').replace('\n', ' ')[:200]
                f.write(f"| @{u} | {cell(t)} | {cell(r)} |\n")


if __name__ == '__main__':
    main()
