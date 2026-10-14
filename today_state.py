# -*- coding: utf-8 -*-
"""Living Context · 今日状态（today）

装的是只对今天有效的流水：吃没吃、吃的什么、睡没睡、几点睡、出没出门去了哪、
身体舒不舒服、当天有什么安排。判据只有一条：等下再问一遍她会不会烦。会，就记。

天亮自动清（懒清理：读的时候发现日期不是今天就清空），不进任何记忆库，
不记对话原文，也不记模型自己的感受。自由 key/value，不硬编码槽位；同 key 覆盖；
软上限 MAX_ITEMS，超了淘汰最旧。一份 JSON，所有入口共用。
"""
import datetime
import json
import os
import threading

PATH = os.environ.get('TODAY_STATE_PATH', 'today_state.json')
MAX_ITEMS = 20
_lock = threading.Lock()

# 按她的时区算「今天」和「现在」。接进自己的系统时替换成自己的时区函数即可。
now = datetime.datetime.now


def today():
    return now().date().isoformat()


def _load():
    if not os.path.exists(PATH):
        return {'date': '', 'items': {}}
    try:
        with open(PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
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
    with _lock:
        d = _load()
        _rollover_if_needed(d)
        stamp = now().strftime('%H:%M')
        for key, value in clean:
            d['items'][key] = {'v': value, 't': stamp}
        if len(d['items']) > MAX_ITEMS:
            for k in list(d['items'].keys())[:len(d['items']) - MAX_ITEMS]:
                d['items'].pop(k, None)
        _save(d)
    return len(clean)


def get_all():
    """读取（懒清理）。返回 {key: {'v': 值, 't': 'HH:MM'}}"""
    with _lock:
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
    with _lock:
        _save({'date': today(), 'items': {}})
