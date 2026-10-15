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
  MAX_REPLIES        max replies per run (default 20)
  MAX_PER_THREAD     max bot replies inside one thread (default 3)
  MEDIA_LIMIT        how many latest posts to scan (default 8)
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

IG = 'https://graph.instagram.com/v23.0'
TOKEN = os.environ.get('IG_TOKEN', '').strip()
KEY = os.environ.get('ANTHROPIC_API_KEY', '').strip()
DRY = os.environ.get('DRY_RUN', '0').strip().lower() in ('1', 'true', 'yes')
MODEL = os.environ.get('MODEL', '').strip() or 'claude-haiku-5-5'
WINDOW = float(os.environ.get('WINDOW_HOURS', '') or (48 if DRY else 6))
MAX_REPLIES = int(os.environ.get('MAX_REPLIES', '') or 20)
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

Maqsad: izoh yozgan odam yana izoh yozishi, suhbat davom etishi.
Har bir javob:
- 1–2 qisqa gap, 180 belgidan oshmasin.
- Odamning aynan nima deganiga javob bo'lsin, umumiy shablon emas.
- Oxiri savol, kichik bahs yoki chaqiriq bilan tugasin. Masalan: uning tajribasini so'ra, "do'stingizni belgilang, u nima der ekan?" de, keyingi qiyinroq savolni taklif qil, ikki variantdan birini tanlashni so'ra.
- Iliq, hazilkash, jonli so'zlashuv tilida. Kitobiy so'zlar yo'q. Emoji 0–1 ta.
- Har safar boshqacha boshla: "Zo'r savol", "Rahmat" kabi bir xil kirishlarni takrorlama.
- Odam qaysi tilda yozgan bo'lsa, o'sha tilda javob ber (o'zbek lotin, o'zbek kirill yoki rus).

Qat'iy qoidalar:
- Tashxis qo'yma, dori yoki davolash tavsiya qilma. Sog'lig'i haqida jiddiy savol bo'lsa, qisqa iliq javob ber va shifokorga ko'rinishni maslahat qil.
- Konkurs, sovg'a, Telegram Premium haqida hech narsa va'da qilma.
- Video haqidagi faktlarni faqat quyidagi "Video haqida" qismidan ol. Bilmasang, o'ylab topma — savol bilan javob ber.
- "Botmisan?" deb jiddiy so'rashsa, rostini ayt: admin yordamchisi, sun'iy intellekt.
- Haqorat, spam, reklama, mazmunsiz belgilar, siyosat yoki boshqa odamni kamsitadigan izoh bo'lsa — faqat SKIP deb yoz.
- Javobni faqat o'zini yoz: qo'shtirnoq, izoh, @username yo'q (uni dastur o'zi qo'shadi)."""


def claude(video, thread, target):
    convo = '\n'.join(f"{'Biz (med.comix)' if m['ours'] else '@' + m['username']}: {m['text']}" for m in thread)
    user = f"""Video haqida:
{video}

Izohlar zanjiri (eskidan yangiga):
{convo}

Endi @{target['username']} ning oxirgi izohiga javob yoz (yoki SKIP)."""
    res = http('https://api.anthropic.com/v1/messages', {
        'model': MODEL, 'max_tokens': 200, 'system': SYSTEM,
        'messages': [{'role': 'user', 'content': user}],
    }, {'content-type': 'application/json', 'x-api-key': KEY, 'anthropic-version': '2023-06-01'})
    txt = ''.join(b.get('text', '') for b in res.get('content', []) if b.get('type') == 'text').strip().strip('"«»').strip()
    return txt


def main():
    if not TOKEN or not KEY:
        sys.exit('IG_TOKEN yoki ANTHROPIC_API_KEY topilmadi (GitHub Secrets).')
    notes = json.load(open(os.path.join(HERE, 'videos.json'), encoding='utf-8'))
    me = ig_get('me', fields='user_id,username')
    my_name = me['username'].lower()
    print(f"Akkaunt: @{me['username']} | model {MODEL} | {'SINOV (javob yozmaydi)' if DRY else 'JONLI'} | oyna {WINDOW} soat")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW)
    media = ig_get('me/media', fields='id,caption,permalink,timestamp,comments_count,shortcode', limit=MEDIA_LIMIT).get('data', [])
    print(f"Postlar: {len(media)} ta")
    done, rows = 0, []
    for m in media:
        if done >= MAX_REPLIES: break
        code = m.get('shortcode') or m.get('permalink', '').rstrip('/').split('/')[-1]
        video = notes.get(code) or f"Video matni (caption): {m.get('caption', '')}"
        comments = ig_all(f"{m['id']}/comments", fields='id,text,timestamp,username,replies.limit(50){id,text,timestamp,username}', limit=50)
        print(f"[{code}] {m.get('timestamp', '')[:16]} | Instagram'dagi izohlar: {m.get('comments_count')} | API bergan: {len(comments)}"
              + (f" | eng yangisi {max(x['timestamp'] for x in comments)[:16]}" if comments else ''))
        for c in comments:
            if done >= MAX_REPLIES: break
            thread = [c] + sorted((c.get('replies') or {}).get('data', []), key=lambda r: r['timestamp'])
            for x in thread:
                x['ours'] = (x.get('username') or '').lower() == my_name
                x['text'] = (x.get('text') or '').strip()
            last = thread[-1]
            if last['ours'] or ts(last['timestamp']) < cutoff: continue
            if sum(x['ours'] for x in thread) >= MAX_PER_THREAD: continue
            if not last['text']: continue
            try:
                reply = claude(video, thread, last)
            except Exception as e:
                print('Claude xatosi:', e); continue
            if not reply or reply.upper().startswith('SKIP'):
                rows.append((last['username'], last['text'], '— (javobsiz)')); continue
            reply = f"@{last['username']} {reply}"[:300]
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
