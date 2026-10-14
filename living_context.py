# -*- coding: utf-8 -*-
"""Living Context · 持续背景（ctx）

装的是跨天仍然成立、但将来会变的事：她妈妈这阵子在墨尔本、楼上这几天施工、
她在准备一场考试。写入之前先问自己一句——这件事明天还成立吗？一个月后呢？

  痕迹 → remember      那天确实发生了，永远为真        「她妈妈来墨尔本看过她」
  当天 → today_set     只对今天有效，天亮清            「今天中午吃炖牛尾骨」
  状态 → 这里          跨天有效，但会过期，将来要更新   「她妈这段时间在墨尔本」

核心只有一条：过期不删，也不装作有效。到了复核日先降级成 stale——读到它的模型
知道该问一句「还在吗」，而不是张口就当真。七天缓冲里没续也没扔，它自己归档，
并留一块墓碑：下一次压缩摘要时，墓碑告诉摘要「这条已不成立」，旧引用不会再被捡回来。

一份 JSON，所有入口共用。读它的每条通道（api / sdk / …）各自记一个「已收进上下文」
标记，只有还没收进去的变更才会被挂出来——新增的才注入，留在上下文里的不反复注入。
"""
import copy
import datetime
import fcntl
import functools
import io
import json
import os
import threading
import uuid

PATH = os.environ.get('LIVING_CONTEXT_PATH', 'living_context.json')
ARCHIVE_PATH = PATH[:-5] + '_archive.json' if PATH.endswith('.json') else PATH + '.archive'
DEFAULT_TTL = 30          # 复核期，不是事情会持续这么久的依据
GRACE_DAYS = 7            # 到期后的缓冲：没续没扔，七天后自动归档
CHANNELS = ('api', 'sdk')  # 有几条读取通道就列几条，各自记同步标记

# 按她的时区算「今天」。接进自己的系统时替换成自己的时区函数即可。
today = datetime.date.today

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


# ───────────────────────── 写 ─────────────────────────

@_locked
def set_item(key, value, ttl_days=DEFAULT_TTL, said_by='', source_turn='', source_session=''):
    """记一条持续状态。同 key 覆盖（并刷新记录时间与复核期），不另建重复项。"""
    key = str(key or '').strip()
    value = str(value or '').strip()
    if not key or not value:
        raise ValueError('ctx key/value must not be empty')
    ttl_days = max(1, min(365, int(ttl_days or DEFAULT_TTL)))
    d = load()
    before = copy.deepcopy(next((x for x in d.get('items', []) if x.get('key') == key), None))
    revision = uuid.uuid4().hex
    day = today()
    items = [x for x in d.get('items', []) if x.get('key') != key]
    items.append({
        'key': key,
        'value': value,
        'noted_at': day.isoformat(),
        'recheck_after': (day + datetime.timedelta(days=ttl_days)).isoformat(),
        'said_by': said_by,
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
        })
    save(d)
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
    """某条没了这件事本身也得让摘要知道，否则摘要里还写着它。"""
    d.setdefault('dropped', []).append({
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
    """过复核日超过 GRACE_DAYS 仍没人续没人扔的，自动归档。返回归档条数。"""
    own = d is None
    if own:
        d = load()
    day = today()
    keep, gone = [], []
    for x in d.get('items', []):
        try:
            ra = datetime.date.fromisoformat(x.get('recheck_after', '9999-12-31'))
        except Exception:
            ra = datetime.date(9999, 12, 31)
        (gone if (day - ra).days > GRACE_DAYS else keep).append(x)
    if gone:
        d['items'] = keep
        for x in gone:
            _tombstone(d, x, 'expired')
        if own:
            save(d)
        _archive(gone, 'expired')
    return len(gone)


@_locked
def _maintain():
    """读之前的例行维护：清理过缓冲的；fresh→stale 的状态翻转也算一次变更，要重新挂一遍。"""
    d = load()
    changed = bool(sweep_expired(d))
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
def get_items():
    """返回 (fresh, stale)。stale = 过了复核日、还在缓冲内；超过缓冲的已被归档。"""
    d = _maintain()
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
    fresh, stale = get_items()
    if not fresh and not stale:
        return ''
    lines = ['<living_context note="现在身处哪里。会变的事，不是痕迹。">']
    for x in fresh:
        lines.append('  <now key="%s" since="%s">%s</now>' % (x['key'], x['noted_at'], x['value']))
    for x in stale:
        lines.append('  <expired key="%s" since="%s" days="%s" '
                     'note="到期了。还成立就 ctx_set 同键续上，结束了就 ctx_drop 扔掉；七天没动作自动归档">%s</expired>'
                     % (x['key'], x['noted_at'], x.get('expired_days'), x['value']))
    lines.append('</living_context>')
    return '\n'.join(lines)


# ─────────────── 增量注入：只挂还没收进上下文的变更 ───────────────

def _change_line(x, dropped=False):
    if dropped:
        label = ('已到期归档，不再作为当前事实；不代表事情已结束'
                 if x.get('reason') == 'expired' else '已移除')
        return '· %s：%s（原：%s）' % (x.get('key'), label, x.get('value'))
    status = '（已到复核期，是否仍成立待确认）' if _is_stale(x) else ''
    return '· %s%s：%s' % (x.get('key'), status, x.get('value'))


@_locked
def pending_items(channel):
    """这条通道还没收进上下文的变更（含墓碑）。大多数时候是空的。"""
    if channel not in CHANNELS:
        raise ValueError('unknown channel: %s' % channel)
    d = _maintain()
    pend = [x for x in d.get('items', []) if not x.get('synced', {}).get(channel)]
    gone = [dict(x, dropped=True) for x in d.get('dropped', []) if not x.get('synced', {}).get(channel)]
    return pend + gone


def render_pending_block(channel, write_hint=''):
    """给某条通道渲染「背景刚有变化」。write_hint 是告诉模型怎么写回去的一句话——
    走隐藏标记的通道说 [CTX_SET:键|值]，走真实工具的通道说 ctx_set。"""
    pend = pending_items(channel)
    if not pend:
        return ''
    lines = ['[背景刚有变化]']
    for x in pend:
        lines.append(_change_line(x, dropped=bool(x.get('dropped'))))
    lines.append('（这是她这阵子的背景更新，用来避免说错话，不是话题。' + (write_hint or '') + '）')
    return '\n'.join(lines) + '\n'


def _purge_tombstones(d):
    d['dropped'] = [x for x in d.get('dropped', [])
                    if not all(x.get('synced', {}).get(ch) for ch in CHANNELS)]


@_locked
def mark_synced(channel):
    """这条通道已经把变更收进上下文（写进摘要 / 一轮成功结束）：标记，以后不再挂。"""
    if channel not in CHANNELS:
        raise ValueError('unknown channel: %s' % channel)
    d = load()
    n = 0
    for x in d.get('items', []) + d.get('dropped', []):
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
    d = _maintain()
    items = sorted(d.get('items', []), key=lambda x: x.get('key', ''))
    lines = ['[持续背景完整快照]',
             '取代此前持续背景快照；未列出的旧项不再作为当前事实，不代表事情一定结束。']
    for x in items:
        lines.append(_change_line(x) + '（复核日：%s）' % x.get('recheck_after', '未设置'))
    if not items:
        lines.append('当前没有有效或待复核的持续背景。')
    if tail:
        lines.append(tail)
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
