"""Instagram insights dump for med.comix (and sister channels).

Runs in GitHub Actions (.github/workflows/insights.yml) every 6 hours and on changes to this file.
Writes:
  stats/data/latest.json          full snapshot: account totals, audience, every post with its insights
  stats/data/posts_history.csv    one row per post per run (posts younger than 30 days) -> growth curves
  stats/data/account_history.csv  followers per run

Tokens come only from env (GitHub secrets). Nothing secret is printed or written.
No comment texts or usernames are stored (the repo is public).

Env:
  IG_TOKEN             med.comix token (Instagram API with Instagram Login)
  IG_TOKEN_TECHCOMIX   optional
  IG_TOKEN_360COMIX    optional
  Permissions needed: instagram_business_basic + instagram_business_manage_insights
"""
import csv, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

VERSIONS = ['v24.0', 'v23.0']
ACCOUNTS = [('medcomix', 'IG_TOKEN'), ('techcomix', 'IG_TOKEN_TECHCOMIX'), ('c360', 'IG_TOKEN_360COMIX')]
REEL_METRICS = ['views', 'reach', 'likes', 'comments', 'shares', 'saved', 'total_interactions', 'reposts',
                'ig_reels_avg_watch_time', 'ig_reels_video_view_total_time', 'reels_skip_rate']
FEED_METRICS = ['views', 'reach', 'likes', 'comments', 'shares', 'saved', 'total_interactions', 'reposts',
                'follows', 'profile_visits']
ACCOUNT_TOTALS = ['reach', 'views', 'accounts_engaged', 'total_interactions', 'likes', 'comments', 'shares',
                  'saves', 'replies', 'profile_links_taps']
HIST_COLS = ['run_at', 'account', 'media_id', 'posted_at', 'age_h', 'views', 'reach', 'likes', 'comments',
             'shares', 'saved', 'reposts', 'ig_reels_avg_watch_time', 'reels_skip_rate']
OUT = os.environ.get('OUT_DIR', 'stats/data')
API = None


class ApiError(Exception):
    pass


def clean(msg):
    return re.sub(r'(access_token=)[^&\s"\']+', r'\1***', str(msg))[:300]


def call(path, token, **params):
    """GET path (relative to API) or a full paging URL. Retries server errors."""
    if path.startswith('https://'):
        url = path
    else:
        params['access_token'] = token
        url = f'{API}/{path}?{urllib.parse.urlencode(params)}'
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')
            try:
                err = json.loads(body).get('error', {})
            except Exception:
                err = {'message': body[:200]}
            if (e.code >= 500 or err.get('code') in (1, 2)) and attempt < 2:
                time.sleep(4 * (attempt + 1))
                continue
            raise ApiError(clean(f"{err.get('code')}: {err.get('message')}"))
        except Exception as e:  # network, timeout
            if attempt < 2:
                time.sleep(4)
                continue
            raise ApiError(clean(e))


def values(d):
    out = {}
    for row in d.get('data', []):
        if 'total_value' in row:
            tv = row['total_value']
            out[row['name']] = tv.get('breakdowns') and tv or tv.get('value')
        else:
            vals = row.get('values') or []
            out[row['name']] = vals[0].get('value') if len(vals) == 1 else vals
    return out


class MetricStatus:
    """Stops asking for a metric that failed on 5 posts and never worked (e.g. no permission)."""
    def __init__(self):
        self.ok, self.fail, self.last = {}, {}, {}

    def skip(self, m):
        return self.fail.get(m, 0) >= 5 and not self.ok.get(m)

    def note(self, m, good, err=None):
        d = self.ok if good else self.fail
        d[m] = d.get(m, 0) + 1
        if err:
            self.last[m] = err

    def report(self):
        names = sorted(set(self.ok) | set(self.fail))
        return {m: {'ok': self.ok.get(m, 0), 'fail': self.fail.get(m, 0), **({'error': self.last[m]} if m in self.last and not self.ok.get(m) else {})} for m in names}


def media_insights(mid, tok, metrics, st):
    ms = [m for m in metrics if not st.skip(m)]
    if not ms:
        return {}
    try:
        got = values(call(f'{mid}/insights', tok, metric=','.join(ms)))
        for m in ms:
            st.note(m, m in got)
        return got
    except ApiError:
        pass
    got = {}
    for m in ms:  # one bad metric fails the whole request -> ask one by one
        try:
            v = values(call(f'{mid}/insights', tok, metric=m))
            got.update(v)
            st.note(m, m in v)
        except ApiError as e:
            st.note(m, False, str(e))
    return got


def try_block(errors, name, fn):
    try:
        return fn()
    except ApiError as e:
        errors.append(f'{name}: {e}')
        return None


def account(tok):
    global API
    me, errors = None, []
    for v in VERSIONS:
        API = f'https://graph.instagram.com/{v}'
        try:
            me = call('me', tok, fields='user_id,username,name,followers_count,follows_count,media_count')
            break
        except ApiError as e:
            errors.append(f'me {v}: {e}')
    if me is None:
        raise ApiError('; '.join(errors))
    errors = []
    uid = me.get('user_id') or me.get('id')
    now = datetime.now(timezone.utc)
    ts = lambda d: int(d.timestamp())
    acc = {'api_version': API.rsplit('/', 1)[1],
           'profile': {k: me.get(k) for k in ('username', 'name', 'followers_count', 'follows_count', 'media_count')}}

    def totals(days):
        return values(call(f'{uid}/insights', tok, metric=','.join(ACCOUNT_TOTALS), period='day', metric_type='total_value',
                           since=ts(now - timedelta(days=days)), until=ts(now)))
    acc['totals_7d'] = try_block(errors, 'totals_7d', lambda: totals(7))
    acc['totals_30d'] = try_block(errors, 'totals_30d', lambda: totals(29))
    acc['follows_unfollows_30d'] = try_block(errors, 'follows_and_unfollows', lambda: values(call(
        f'{uid}/insights', tok, metric='follows_and_unfollows', period='day', metric_type='total_value',
        breakdown='follow_type', since=ts(now - timedelta(days=29)), until=ts(now))))
    acc['follower_count_daily'] = try_block(errors, 'follower_count', lambda: values(call(
        f'{uid}/insights', tok, metric='follower_count', period='day',
        since=ts(now - timedelta(days=29)), until=ts(now - timedelta(days=1)))).get('follower_count'))
    acc['online_followers'] = try_block(errors, 'online_followers', lambda: values(call(
        f'{uid}/insights', tok, metric='online_followers', period='lifetime')).get('online_followers'))
    demo = {}
    for metric in ('follower_demographics', 'engaged_audience_demographics'):
        for bd in ('age', 'gender', 'city', 'country'):
            r = try_block(errors, f'{metric}/{bd}', lambda: values(call(
                f'{uid}/insights', tok, metric=metric, period='lifetime', metric_type='total_value',
                timeframe='this_month', breakdown=bd)).get(metric))
            if r is not None:
                demo.setdefault(metric, {})[bd] = r
    acc['demographics'] = demo

    items, url, first = [], 'me/media', True
    while url:
        d = call(url, tok, fields='id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count',
                 limit=50) if first else call(url, tok)
        first = False
        items += d.get('data', [])
        url = (d.get('paging') or {}).get('next')
        if len(items) >= 2000:
            break
    st, media = MetricStatus(), []
    for m in items:
        kind = m.get('media_product_type') or 'FEED'
        if kind == 'STORY':
            continue
        ins = media_insights(m['id'], tok, REEL_METRICS if kind == 'REELS' else FEED_METRICS, st)
        posted = datetime.fromisoformat(m['timestamp'].replace('+0000', '+00:00')) if m.get('timestamp') else None
        media.append({'id': m['id'], 'kind': kind, 'type': m.get('media_type'), 'permalink': m.get('permalink'),
                      'posted_at': m.get('timestamp'), 'age_h': round((now - posted).total_seconds() / 3600, 1) if posted else None,
                      'caption': (m.get('caption') or '')[:120], 'like_count': m.get('like_count'),
                      'comments_count': m.get('comments_count'), 'insights': ins})
        time.sleep(0.1)
    acc['media'] = media
    acc['metric_status'] = st.report()
    acc['errors'] = errors
    return acc


def main():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, 'latest.json')
    try:
        old = json.load(open(path))
    except Exception:
        old = {}
    run_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    new = {'generated_at': run_at, 'accounts': {}}
    hist, acc_rows, summary = [], [], []
    for key, env in ACCOUNTS:
        tok = os.environ.get(env, '').strip()
        if not tok:
            if key in old.get('accounts', {}):
                new['accounts'][key] = old['accounts'][key]
            continue
        try:
            a = account(tok)
        except ApiError as e:
            prev = old.get('accounts', {}).get(key)
            new['accounts'][key] = {**(prev or {}), 'stale': True, 'fatal_error': str(e)}
            summary.append(f'- **{key}**: FAILED — {e}')
            print('FAIL', key, e)
            continue
        new['accounts'][key] = a
        p = a['profile']
        acc_rows.append([run_at, key, p.get('followers_count'), p.get('follows_count'), p.get('media_count')])
        for m in a['media']:
            if m['age_h'] is not None and m['age_h'] <= 24 * 30:
                i = m['insights']
                hist.append([run_at, key, m['id'], m['posted_at'], m['age_h']] + [i.get(c) for c in HIST_COLS[5:]])
        bad = [k for k, v in a['metric_status'].items() if not v['ok']]
        summary.append(f"- **{key}** (@{p.get('username')}): {p.get('followers_count')} followers, "
                       f"{len(a['media'])} posts, API {a['api_version']}"
                       + (f"; metrics not available: {', '.join(bad)}" if bad else '')
                       + (f"; account errors: {len(a['errors'])}" if a['errors'] else ''))
        print('ok', key, p.get('followers_count'), len(a['media']), 'bad metrics:', bad, 'errors:', a['errors'])
    json.dump(new, open(path, 'w'), ensure_ascii=False, indent=1)
    for name, cols, rows in (('posts_history.csv', HIST_COLS, hist),
                             ('account_history.csv', ['run_at', 'account', 'followers', 'follows', 'media_count'], acc_rows)):
        f = os.path.join(OUT, name)
        new_file = not os.path.exists(f)
        with open(f, 'a', newline='') as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(cols)
            w.writerows(rows)
    if not summary:
        summary = ['- No token found (set the IG_TOKEN secret).']
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as fh:
            fh.write('### Instagram insights\n' + '\n'.join(summary) + '\n')
    print('\n'.join(summary))


if __name__ == '__main__':
    sys.exit(main())
