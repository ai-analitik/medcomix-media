"""Fetch every comment (and reply) of one Instagram post for a giveaway draw.
Writes stats/data/giveaway_<media_id>.json with username, text and time of each comment.
Env: IG_TOKEN (med.comix), MEDIA_ID. Read-only: it never posts or changes anything."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import insights as I

tok = os.environ['IG_TOKEN']; mid = os.environ['MEDIA_ID']
I.API = f'https://graph.instagram.com/{I.VERSIONS[0]}'
def row(c):
    f = c.get('from') or {}
    return {'id': c.get('id'), 'username': c.get('username') or f.get('username'), 'user_id': f.get('id'),
            'text': c.get('text'), 'timestamp': c.get('timestamp')}

rows, page = [], I.call(f'{mid}/comments', tok, fields='id,text,username,from,timestamp,replies{id,text,username,from,timestamp}', limit=50)
while True:
    for c in page.get('data', []):
        rows.append(row(c))
        for r in (c.get('replies') or {}).get('data', []):
            rows.append(row(r) | {'reply': True})
    nxt = page.get('paging', {}).get('next')
    if not nxt: break
    page = I.call(nxt, tok)
os.makedirs(I.OUT, exist_ok=True)
json.dump({'media_id': mid, 'count': len(rows), 'comments': rows},
          open(f'{I.OUT}/giveaway_{mid}.json', 'w'), ensure_ascii=False, indent=1)
print('comments fetched:', len(rows))
