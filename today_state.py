# -*- coding: utf-8 -*-
"""Living Context · 今日状态（today）

装的是只对今天有效的流水：吃没吃、吃的什么、睡没睡、几点睡、出没出门去了哪、
身体舒不舒服、当天有什么安排。判据只有一条：等下再问一遍她会不会烦。会，就记。

天亮自动清（懒清理：读的时候发现日期不是今天就清空）。几点算新的一天看 DAY_STARTS_AT，
默认早上六点：她两三点才睡，十点说的晚饭过了零点还算今天的，不会一点钟又被问一遍。
不进任何记忆库，不记对话原文，也不记模型自己的感受。自由 key/value，不硬编码槽位；
同 key 覆盖；软上限 MAX_ITEMS，超了淘汰最旧。一份 JSON，所有入口共用。
"""
import datetime
import fcntl
import json
import os
import sys
import threading
from contextlib import contextmanager

PATH = os.environ.get('TODAY_STATE_PATH', 'today_state.json')
MAX_ITEMS = 20


def _hour(v, default):
    try:
        return max(0, min(23, int(v)))
    except (TypeError, ValueError):
        return default


# 几点算新的一天。living_context 读同一个环境变量，两边的「一天」才是同一天。按自己的作息改。
DAY_STARTS_AT = _hour(os.environ.get('LIVING_CONTEXT_DAY_STARTS_AT'), 6)
_lock = threading.Lock()

# 按她的时区算「现在」。接进自己的系统时替换成自己的时区函数即可。
now = datetime.datetime.now


def today():
    """她的「今天」：DAY_STARTS_AT 点以前还算昨天。"""
    return (now() - datetime.timedelta(hours=DAY_STARTS_AT)).date().isoformat()


@contextmanager
def _locked():
    """进程内锁 + 文件锁：官端、api、SDK 几个进程同时写一份 JSON 也不会互相盖。"""
    with _lock:
        with open(PATH + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


def _load():
    """解析不了（多半是手改漏了个逗号）就把坏文件挪到一边（.broken-时间戳），从空的开始，不让下一次写盖掉它。"""
    if not os.path.exists(PATH):
        return {'date': '', 'items': {}}
    try:
        with open(PATH, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        aside = '%s.broken-%s' % (PATH, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        os.replace(PATH, aside)
        print('[today_state] %s 读不出来（%s），挪到了 %s，从空的开始' % (PATH, e, aside), file=sys.stderr)
        return {'date': '', 'items': {}}


def _save(d):
    tmp = PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)


def _rollover_if_needed(d):
    """跨日清空。返回是否发生清空。"""
    if d.get('date') != today():
        d['date'] = today()
        d['items'] = {}
        return True
    return False


def set_item(key, value):
    return bool(set_items([(key, value)]))


def set_items(items):
    """一次原子写入多条；她一句话说了几件事就一次写完，不连续调。"""
    clean = []
    for key, value in items or []:
        key = (key or '').strip()
        value = (value or '').strip()
        if key and value:
            clean.append((key, value))
    if not clean:
        return 0
    with _locked():
        d = _load()
        _rollover_if_needed(d)
        stamp = now().strftime('%H:%M')
        for key, value in clean:
            d['items'].pop(key, None)          # 覆盖也挪到最后：满了淘汰的是真最旧的，不是早上先记的那条
            d['items'][key] = {'v': value, 't': stamp}
        if len(d['items']) > MAX_ITEMS:
            for k in list(d['items'].keys())[:len(d['items']) - MAX_ITEMS]:
                d['items'].pop(k, None)
        _save(d)
    return len(clean)


def get_all():
    """读取（懒清理）。返回 {key: {'v': 值, 't': 'HH:MM'}}"""
    with _locked():
        d = _load()
        if _rollover_if_needed(d):
            _save(d)
        return dict(d.get('items', {}))


def render_block(write_mode='tool'):
    """生成注入块。空的时候也给一句，让模型知道它在、知道怎么写。

    write_mode='tool'   写入走真实工具 today_set（MCP / SDK）
    write_mode='marker' 写入走回复末尾的隐藏标记 [TODAY_SET:key|value]（API 路线）
    """
    if write_mode not in ('marker', 'tool'):
        raise ValueError('write_mode must be marker or tool')
    items = get_all()
    if write_mode == 'tool':
        write_hint = '当天事实有更新时，直接调用 today_set 写入；不要在正文里输出任何控制标记。'
        example = '例：today_set 写入「晚饭｜五点吃的，番茄炖牛骨白灼生菜」'
    else:
        write_hint = '在回复末尾加 [TODAY_SET:key|value] 记一条今日流水，天亮自动清，她看不到标记。'
        example = '例：[TODAY_SET:晚饭|五点吃的，番茄炖牛骨白灼生菜]'
    if not items:
        return ('【今日状态】（今天还没记过）\n' + write_hint + '\n'
                '这几类她一说就记，不用判断够不够格：吃没吃/吃的什么、睡没睡/几点睡、'
                '出没出门/去哪、身体状况、当天行程安排。它们最容易被你重复追问。\n' + example)
    lines = ['【今日状态】（天亮自动清）']
    for k, meta in items.items():
        lines.append('· %s：%s (%s)' % (k, meta.get('v', ''), meta.get('t', '')))
    lines.append('（' + write_hint + ' 吃饭/睡觉/出门/身体/当天行程这几类她一提就记，不评估够不够格。）')
    return '\n'.join(lines)


def clear():
    with _locked():
        _save({'date': today(), 'items': {}})
