# -*- coding: utf-8 -*-
"""Living Context · 持续背景（ctx）

装的是跨天仍然成立、但将来会变的事：她妈妈这阵子来住着、楼上这几天施工、
她在准备一场考试。写入之前先问自己一句——这件事明天还成立吗？一个月后呢？

  痕迹 → remember      那天确实发生了，永远为真        「她妈妈来看过她」
  当天 → today_set     只对今天有效，天亮清            「今天中午吃炖牛尾骨」
  状态 → 这里          跨天有效，但会过期，将来要更新   「她妈这段时间在她这儿」

核心只有一条：过期不删，也不装作有效。到了复核日先降级成「未核实」（stale）——读到
它的模型知道该问一句「还在吗」，而不是张口就当真。到复核的那一条会递到它面前，准它
聊着顺手问一句还作不作数，问一次就够，不追：她说还在就续上，说不在了就扔掉。一直没
动静的，递到面前满三个她在的日子才归档——数的是她在、它也真递到面前的日子，不数日历，
不会趁她不在悄悄消失，也不会一直挂着反复问。退场的留一块墓碑：下一次压缩摘要时，
墓碑告诉摘要「这条已不成立」，旧引用不会再被捡回来。

一份 JSON，所有入口共用。走增量挂行的每条通道各自记一个「已收进上下文」标记，
只有还没收进去的变更才会被挂出来——新增的才注入，留在上下文里的不反复注入。
"""
import copy
import datetime
import fcntl
import functools
import io
import json
import os
import re
import threading
import uuid

PATH = os.environ.get('LIVING_CONTEXT_PATH', 'living_context.json')
ARCHIVE_PATH = PATH[:-5] + '_archive.json' if PATH.endswith('.json') else PATH + '.archive'
DEFAULT_TTL = 30          # 复核期，不是事情会持续这么久的依据
GRACE_DAYS = 3            # 未核实的递到面前满几个她在的日子，还没续没扔，就归档（留墓碑）。数的是她在、它也真
                          # 递到面前的日子（_due_shown），不数日历：她不在的日子不算。None = 一直挂着，只等模型扔
DUE_TELL_DAYS = 2         # 未核实的条目在 today_get 回执末尾最多带两天：同样数她在的日子，不是日历
TOMBSTONE_DAYS = 14       # 墓碑最多立两周：哪条通道两周都没把它收进摘要，也照样清掉，不让它越堆越多
TURN_WRITES_DAYS = 7      # 按轮撤销只管最近一周的写入，再早的撤销记录清掉
CHANNELS = ('api',)       # 只列走「增量挂行」的通道：读 pending_items、摘要落盘后 mark_synced 的那种。
                          # 走完整快照的（SDK 路线：render_snapshot + hint_snapshots）不用列——它从不
                          # mark_synced，列进来墓碑就永远收不掉。真有第二条挂行的通道，再加进来。

# 按她的时区算「今天」。接进自己的系统时替换成自己的时区函数即可。
today = datetime.date.today

# 她今天在不在。未核实的条目只在她在的日子里「递到面前」才算数，所以模型自己起的轮次（定时、心跳、
# 主动敲门）别让它算进去：接进自己的系统时换成自己的判断，比如「她今天发过消息没有」。
# 默认 True：读到就算——适合只在她说话时才会读这里的系统。
present_today = lambda: True

_LOCK = threading.RLock()
_DEPTH = threading.local()


def _locked(fn):
    """进程内 RLock + 文件锁，多个入口同时写一份 JSON 也不会互相踩。"""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        with _LOCK:
            if getattr(_DEPTH, 'active', False):
                return fn(*args, **kwargs)
            with open(PATH + '.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _DEPTH.active = True
                try:
                    return fn(*args, **kwargs)
                finally:
                    _DEPTH.active = False
    return run


# ───────────────────────── 存取 ─────────────────────────

def load():
    if not os.path.exists(PATH):
        return {'items': []}
    try:
        with io.open(PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'items': []}


def save(d):
    tmp = PATH + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)


def _unsynced():
    return {ch: False for ch in CHANNELS}


def _is_stale(x, day=None):
    day = (day or today()).isoformat()
    return x.get('recheck_after', '9999') < day


def _age(iso, day):
    """iso 日期离 day 几天；读不出来的当作很久以前。"""
    try:
        return (day - datetime.date.fromisoformat(str(iso))).days
    except Exception:
        return 10 ** 6


# ───────────────────────── 写 ─────────────────────────

def _until(until, day):
    """until 写的那天（YYYY-MM-DD）；读不出来、或早于今天，就当没写。最远一年。"""
    try:
        u = datetime.date.fromisoformat(str(until or '').strip().replace('/', '-'))
    except Exception:
        return None
    if u < day:
        return None
    return min(u, day + datetime.timedelta(days=365))


@_locked
def set_item(key, value='', ttl_days=DEFAULT_TTL, said_by='', source_turn='', source_session='', until=''):
    """记一条她这阵子的处境，一件一行。
    - 只给 key（value 空）：她说还在——原话不动，从哪天开始的也不动，只把复核日往后推。
    - 给了新的一句、跟原来不一样：丢旧建新——旧的那句收进归档（reason='replaced'），新的一句从今天记起。
      不在原话上改，免得新旧搅在一起。
    - 同一句：当续期。
    until：知道的那天（YYYY-MM-DD），过了那天它递回来问；比 ttl_days 优先。"""
    key = str(key or '').strip()
    value = str(value or '').strip()
    if not key:
        raise ValueError('ctx key must not be empty')
    ttl_days = max(1, min(365, int(ttl_days or DEFAULT_TTL)))
    d = load()
    old = next((x for x in d.get('items', []) if x.get('key') == key), None)
    if not value:
        if old is None:
            raise ValueError('没有「%s」这条；新记一条，要写一句她现在的情况' % key)
        value = old.get('value', '')
    before = copy.deepcopy(old)
    renew = old is not None and old.get('value') == value
    revision = uuid.uuid4().hex
    day = today()
    due = _until(until, day) or (day + datetime.timedelta(days=ttl_days))
    items = [x for x in d.get('items', []) if x.get('key') != key]
    items.append({
        'key': key,
        'value': value,
        'noted_at': (old.get('noted_at') if renew else '') or day.isoformat(),
        'recheck_after': due.isoformat(),
        'said_by': said_by or ((old or {}).get('said_by', '') if renew else ''),
        'synced': _unsynced(),
        '_status': 'fresh',
        '_write_revision': revision,
    })
    d['items'] = items
    if source_turn and source_session:
        # 记下这一轮写了什么，供「重 roll / 撤销这一轮」时按轮回退
        d.setdefault('_turn_writes', []).append({
            'turn': str(source_turn), 'session': str(source_session),
            'key': key, 'revision': revision, 'before': before,
            'day': day.isoformat(),
        })
    save(d)
    if old is not None and not renew:
        _archive([old], 'replaced')
    return d['items'][-1]


def _archive(items, reason):
    """挪进归档文件：不再进上下文，但要翻还翻得到。"""
    if not items:
        return
    arc = []
    try:
        if os.path.exists(ARCHIVE_PATH):
            with io.open(ARCHIVE_PATH, encoding='utf-8') as f:
                arc = json.load(f).get('items', [])
    except Exception:
        arc = []
    now = today().isoformat()
    for x in items:
        y = dict(x)
        y['archived_at'] = now
        y['archive_reason'] = reason
        arc.append(y)
    tmp = ARCHIVE_PATH + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'items': arc}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARCHIVE_PATH)


def _tombstone(d, x, reason):
    """某条没了这件事本身也得让摘要知道，否则摘要里还写着它。同一个键只留最新的一块。"""
    key = x.get('key')
    d['dropped'] = [t for t in d.get('dropped', []) if t.get('key') != key]
    d['dropped'].append({
        'key': x.get('key'), 'value': x.get('value'), 'reason': reason,
        'dropped_at': today().isoformat(), 'synced': _unsynced(),
    })


@_locked
def drop_item(key, reason='dropped'):
    """事情真结束了就扔：移入归档 + 留一块待同步的墓碑。返回移除条数。"""
    key = str(key or '').strip()
    d = load()
    gone = [x for x in d.get('items', []) if x.get('key') == key]
    if not gone:
        return 0
    d['items'] = [x for x in d.get('items', []) if x.get('key') != key]
    for x in gone:
        _tombstone(d, x, reason)
    save(d)
    _archive(gone, reason)
    return len(gone)


@_locked
def sweep_expired(d=None):
    """未核实的，递到面前满 GRACE_DAYS 个她在的日子（不含今天）还没人续没人扔，就归档，留墓碑。
    数的是 _due_shown 里的日子，不数日历：她不在、没递到面前的日子不算。GRACE_DAYS 为 None 时不归档。
    返回归档条数。"""
    if GRACE_DAYS is None:
        return 0
    own = d is None
    if own:
        d = load()
    day = today()
    iso = day.isoformat()
    keep, gone = [], []
    for x in d.get('items', []):
        shown = {s for s in (x.get('_due_shown') or []) if s < iso}
        (gone if (_is_stale(x, day) and len(shown) >= GRACE_DAYS) else keep).append(x)
    if gone:
        d['items'] = keep
        for x in gone:
            _tombstone(d, x, 'expired')
        if own:
            save(d)
        _archive(gone, 'expired')
    return len(gone)


def _prune(d):
    """墓碑和撤销记录只进不出的话，文件会一直长。返回是否有改动。
    - 墓碑：所有挂行通道都收进去了就清；立满 TOMBSTONE_DAYS 的，不管收没收也清。
    - 撤销记录：只留最近 TURN_WRITES_DAYS 天（没记日子的旧记录，从今天起算）。"""
    day = today()
    changed = False
    tombs = d.get('dropped', [])
    keep = [t for t in tombs
            if not all(t.get('synced', {}).get(ch) for ch in CHANNELS)
            and _age(t.get('dropped_at'), day) < TOMBSTONE_DAYS]
    if len(keep) != len(tombs):
        d['dropped'] = keep
        changed = True
    writes = d.get('_turn_writes', [])
    for e in writes:
        if not e.get('day'):
            e['day'] = day.isoformat()
            changed = True
    keep_w = [e for e in writes if _age(e.get('day'), day) < TURN_WRITES_DAYS]
    if len(keep_w) != len(writes):
        d['_turn_writes'] = keep_w
        changed = True
    return changed


def _note_shown(d, items=None):
    """未核实的条目这会儿递到模型面前了：她今天也在的话，记下今天（_due_shown）。返回是否有改动。
    items 不给就是全部未核实的（breath、快照、挂行、ctx_get 都整份递出去）。"""
    iso = today().isoformat()
    pool = d.get('items', []) if items is None else items
    todo = [x for x in pool if _is_stale(x) and iso not in (x.get('_due_shown') or [])]
    if not todo or not present_today():
        return False
    for x in todo:
        x['_due_shown'] = list(x.get('_due_shown') or []) + [iso]
    return True


@_locked
def _maintain(shown=False):
    """读之前的例行维护：收墓碑、修剪撤销记录、把递满日子的未核实归档；shown=True 时这次是递到面前，
    记一笔她在的日子。fresh→未核实 的状态翻转也算一次变更，要重新挂一遍。"""
    d = load()
    changed = bool(sweep_expired(d))
    changed = _prune(d) or changed
    if shown:
        changed = _note_shown(d) or changed
    for x in d.get('items', []):
        status = 'stale' if _is_stale(x) else 'fresh'
        if status != x.get('_status', 'fresh'):
            x['synced'] = _unsynced()
            x['_status'] = status
            changed = True
    if changed:
        save(d)
    return d


# ───────────────────────── 读 ─────────────────────────

@_locked
def get_items(shown=False):
    """返回 (fresh, stale)。stale = 过了复核日、还没核实的（递到面前满 GRACE_DAYS 个她在的日子还没动静才归档）。
    shown=True：这次是递到模型面前（breath、ctx_get 工具），记一笔她在的日子；自己内部读就别传。"""
    d = _maintain(shown)
    day = today()
    fresh, stale = [], []
    for x in d.get('items', []):
        if _is_stale(x, day):
            y = dict(x)
            try:
                y['expired_days'] = (day - datetime.date.fromisoformat(x['recheck_after'])).days
            except Exception:
                y['expired_days'] = None
            stale.append(y)
        else:
            fresh.append(x)
    return fresh, stale


def render_breath_block():
    """挂进 breath 的一小段。没有内容就返回空串——不占位、不硬凑。"""
    fresh, stale = get_items(shown=True)
    if not fresh and not stale:
        return ''
    lines = ['<living_context note="现在身处哪里。会变的事，不是痕迹。">']
    for x in fresh:
        lines.append('  <now key="%s" since="%s">%s</now>' % (x['key'], x['noted_at'], x['value']))
    for x in stale:
        lines.append('  <unverified key="%s" since="%s" due="%s" days="%s" '
                     'note="到期了，还没核实：别当成现在的事。聊着顺手问她一句还作不作数，还在就 ctx_set 同键续上，'
                     '不在了就 ctx_drop 扔掉；问一次就够，不追。没动静也不要紧，递到面前满三个她在的日子，它自己归档">%s</unverified>'
                     % (x['key'], x['noted_at'], x.get('recheck_after'), x.get('expired_days'), x['value']))
    lines.append('</living_context>')
    return '\n'.join(lines)


# ─────────────── 增量注入：只挂还没收进上下文的变更 ───────────────

def _change_line(x, dropped=False):
    if dropped:
        label = ('已到期归档，不再作为当前事实；不代表事情已结束'
                 if x.get('reason') == 'expired' else '已移除')
        return '· %s：%s（原：%s）' % (x.get('key'), label, x.get('value'))
    status = '（到期了，还没核实）' if _is_stale(x) else ''
    return '· %s%s：%s' % (x.get('key'), status, x.get('value'))


@_locked
def pending_items(channel, shown=False):
    """这条通道还没收进上下文的变更（含墓碑）。大多数时候是空的。
    同一个键后来又记回来了，挂的是新的那条，它的墓碑就不挂了。
    shown=True：这次是挂到她消息上递给模型（render_pending_block 会传）；交给摘要时别传。"""
    if channel not in CHANNELS:
        raise ValueError('unknown channel: %s' % channel)
    d = _maintain(shown)
    live = {x.get('key') for x in d.get('items', [])}
    pend = [x for x in d.get('items', []) if not x.get('synced', {}).get(channel)]
    gone = [dict(x, dropped=True) for x in d.get('dropped', [])
            if not x.get('synced', {}).get(channel) and x.get('key') not in live]
    return pend + gone


def _due_note(pend, how):
    """到期没核实的递到面前：「不是话题」的例外，准模型顺口问一句。how = (续上的说法, 扔掉的说法)。
    只点名还在的条目；已经扔掉的不点名。"""
    day = today().isoformat()
    keys = [x.get('key') for x in pend if not x.get('dropped') and x.get('recheck_after', '9999') < day]
    if not keys:
        return ''
    return ('（到期没核实的%s是例外：别当成现在的事，聊着顺手问她一句还作不作数，她说还在就%s，说不在了就%s；'
            '问一次就够，不追，没动静的递到面前满三个她在的日子自己归档。）'
            % ('、'.join('「%s」' % k for k in keys), how[0], how[1]))


def render_pending_block(channel, write_hint='', due_how=('用 ctx_set 同键续上', '用 ctx_drop 扔掉')):
    """给某条通道渲染「背景刚有变化」。write_hint 是告诉模型怎么写回去的一句话——
    走隐藏标记的通道说 [CTX_SET:键|值]，走真实工具的通道说 ctx_set。
    due_how 是到期没核实那句里「续上 / 扔掉」的说法，走隐藏标记的通道换成
    ('写 [CTX_SET:键|新值] 续上', '写 [CTX_DROP:键]')。"""
    pend = pending_items(channel, shown=True)
    if not pend:
        return ''
    lines = ['[背景刚有变化]']
    for x in pend:
        lines.append(_change_line(x, dropped=bool(x.get('dropped'))))
    lines.append('（这是她这阵子的背景更新，用来避免说错话，不是话题。' + (write_hint or '') + '）')
    due = _due_note(pend, due_how)
    if due:
        lines.append(due)
    return '\n'.join(lines) + '\n'


@_locked
def _due_for_today():
    d = _maintain()
    day = today()
    iso = day.isoformat()
    due, changed = [], False
    here = None
    for x in d.get('items', []):
        if not _is_stale(x, day):
            continue
        told = list(x.get('_due_told') or [])
        if iso not in told:
            if len(told) >= DUE_TELL_DAYS:
                continue          # 带满了：这里不再提，breath 和快照里照样挂着
            here = present_today() if here is None else here
            if here:              # 她不在的日子不占这两天
                x['_due_told'] = told + [iso]
                changed = True
        due.append(x)
    changed = _note_shown(d, due) or changed
    if changed:
        save(d)
    return due


def render_due_for_today():
    """未核实的状态，给 today_get 的回执末尾带上。只带「递到面前的头 DUE_TELL_DAYS 天」：
    数的是她在、真带上它的日子，她不在、没人来看的日子不算；同一天看几次都照样带。
    没有就返回空串，出错也返回空串，不连累 today_get。
    模型问「吃了吗」之前会来看 today_get，顺路就看见了。"""
    try:
        due = _due_for_today()
    except Exception:
        return ''
    if not due:
        return ''
    lines = ['【到期了，还没核实】']
    for x in due:
        lines.append('· %s：%s（复核日 %s）' % (x.get('key'), x.get('value'), x.get('recheck_after')))
    lines.append('别当成现在的事。聊着顺手问她一句还作不作数：还在就 ctx_set 同键续上，不在了就 ctx_drop 扔掉。'
                 '问一次就够，不追；没动静也不要紧，递到面前满三个她在的日子，它自己归档。')
    return '\n'.join(lines)


def _purge_tombstones(d):
    d['dropped'] = [x for x in d.get('dropped', [])
                    if not all(x.get('synced', {}).get(ch) for ch in CHANNELS)]


def _same_version(x):
    """认一条的版本：条目看键、写入版本和新旧；墓碑看键、日子和原值。"""
    if x.get('dropped') or 'dropped_at' in x:
        return ('t', x.get('key'), x.get('dropped_at'), x.get('value'))
    return ('i', x.get('key'), x.get('_write_revision'), x.get('_status', 'fresh'))


@_locked
def mark_synced(channel, items=None):
    """这条通道已经把变更收进上下文（写进摘要 / 一轮成功结束）：标记，以后不再挂。
    items：交给摘要的那一份 pending_items(channel)。给了就只标这几条——写摘要那几十秒里别的入口
    刚记的、刚改的、刚扔的没进摘要，留着下回再挂；不给就全部标上（旧用法）。"""
    if channel not in CHANNELS:
        raise ValueError('unknown channel: %s' % channel)
    d = load()
    saw = None if items is None else {_same_version(x) for x in items}
    n = 0
    for x in d.get('items', []) + d.get('dropped', []):
        if saw is not None and _same_version(x) not in saw:
            continue
        if not x.get('synced', {}).get(channel):
            x.setdefault('synced', _unsynced())[channel] = True
            n += 1
    if n:
        _purge_tombstones(d)
        save(d)
    return n


@_locked
def render_snapshot(tail=''):
    """完整快照：给长会话首轮、或手动改过背景之后用——取代此前所有快照。"""
    d = _maintain(shown=True)
    items = sorted(d.get('items', []), key=lambda x: x.get('key', ''))
    lines = ['[持续背景完整快照]',
             '取代此前持续背景快照；未列出的旧项不再作为当前事实，不代表事情一定结束。']
    for x in items:
        lines.append(_change_line(x) + '（复核日：%s）' % x.get('recheck_after', '未设置'))
    if not items:
        lines.append('当前没有有效或未核实的持续背景。')
    due = _due_note(items, ('用 ctx_set 同键续上', '用 ctx_drop 扔掉'))
    if due:
        lines.append(due)
    if tail:
        lines.append(tail)
    return '\n'.join(lines)


# ─────────────── 归档不是忘了：翻得到，但标成「以前」 ───────────────

_PUNCT = re.compile(r'[\s，。、,.!?！？「」"\'：:；;（）()·]+')


def _bigrams(s):
    s = _PUNCT.sub('', str(s or ''))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def search_archive(query, limit=3):
    """在归档里找以前的处境（扔掉的、换掉的、到期收起来的），给 recall 用：翻得到，交出去的时候标成「以前」。
    怎么算碰上：键的字全在她问的话里（「吃药」之于「那阵子吃的什么药」）；或者两个字两个字地比，碰上的够多，
    键上的分量重；问得很短（「药」）就看键里有没有这个字。分一样就新的在前，同一句只给一次。"""
    qs = _PUNCT.sub('', str(query or ''))
    if not qs or not os.path.exists(ARCHIVE_PATH):
        return []
    try:
        with io.open(ARCHIVE_PATH, encoding='utf-8') as f:
            arc = json.load(f).get('items', [])
    except Exception:
        return []
    qb, qc = _bigrams(qs), set(qs)
    scored = []
    for x in arc:
        k = _PUNCT.sub('', str(x.get('key') or ''))
        kc = set(k)
        score = len(qb & _bigrams(x.get('value'))) + 2 * len(qb & _bigrams(k))
        if len(kc) >= 2 and kc <= qc:
            score += 3
        elif len(qs) <= 2 and qc & kc:
            score += 2
        if score >= 2:
            scored.append((score, x.get('archived_at', ''), x))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out, seen = [], set()
    for _, _, x in scored:
        sig = (x.get('key'), x.get('value'))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(x)
        if len(out) >= limit:
            break
    return out


_PAST_HOW = {'replaced': '后来换了说法', 'dropped': '结束了', 'closed': '结束了',
             'expired': '到期没核实，收起来了，不代表结束'}


def render_past_states(items):
    """把翻到的旧处境渲染成一小段，接在 recall 结果后面：从哪天到哪天、怎么收起来的。没有就返回空串。"""
    if not items:
        return ''
    lines = ['<past_states note="她以前的处境，已经收起来了，不是现在。她问起那阵子再用；现在怎样，看 living_context 或者问她">']
    for x in items:
        lines.append('  <past key="%s" from="%s" to="%s" how="%s">%s</past>' % (
            x.get('key', ''), x.get('noted_at', ''), x.get('archived_at', ''),
            _PAST_HOW.get(x.get('archive_reason'), x.get('archive_reason', '')), x.get('value', '')))
    lines.append('</past_states>')
    return '\n'.join(lines)


# ─────────────── 按轮撤销（重 roll / 手动编辑时用） ───────────────

@_locked
def undo_turn_writes(turn, session):
    """只撤销这一轮写入、且现在仍是那个版本的条目；被后来的写入覆盖过的不动。"""
    if not turn or not session:
        return {'restored': 0, 'skipped': 0}
    d = load()
    events = d.get('_turn_writes', [])
    mine = [e for e in events if e.get('turn') == str(turn) and e.get('session') == str(session)]
    result = {'restored': 0, 'skipped': 0}
    for e in reversed(mine):
        current = next((x for x in d.get('items', []) if x.get('key') == e['key']), None)
        if not current or current.get('_write_revision') != e['revision']:
            result['skipped'] += 1
            continue
        d['items'] = [x for x in d.get('items', []) if x.get('key') != e['key']]
        previous = copy.deepcopy(e.get('before'))
        if previous is not None:
            previous['synced'] = _unsynced()
            d['items'].append(previous)
        result['restored'] += 1
    if mine:
        d['_turn_writes'] = [e for e in events if e not in mine]
        save(d)
    return result
