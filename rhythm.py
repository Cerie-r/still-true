# -*- coding: utf-8 -*-
"""Living Context · 作息节律（rhythm）

从她真实的到达记录里学她平常几点出现、最晚到几点，不是靠猜。起因是她一句话：
「免得你又七点半就催我睡觉」——模型只会数「多久没说话」，七点半看她安静了就疑神疑鬼；
可记录里躺着几十天她真实出现的时间，一算就知道七点半在她这儿是下午刚过半，离睡还有
六个钟头。数据一直都在，没人用过。知道得越准，越不用瞎猜。

三条约束：
  ① 只降低误判，不增加打扰：这里只回答「她这会儿安静正常吗」，不生成任何要推给她的话。
  ② 样本不足就说不知道，不外推：confidence 是 low 就别用。
  ③ 只读，不写任何状态。

夜界用 DAY_STARTS_AT（和 today / ctx 同一个，默认 6）：凌晨两点的消息是前一天的深夜，不是新一天的
清晨，折算成前一天的 26:00 来算，否则「最晚」永远是 00:0x。

跟 ctx / today 是一件事的三条腿之一：today、ctx 知道她说过的，就不问第二遍、不说错；这里知道她平常
几点在，就不拿钟当她该睡了的证据；真写出了催、盘问的那一句，由 Stop hook 那层拦（见 README「关心边界」）。
知道了便不催。
"""
import datetime
import os

MIN_DAYS = 7          # 少于这么多天的样本，一律 confidence=low
THIN_DAYS = 14        # 够了但还薄
LOOKBACK_DAYS = 45    # 只看最近这些天，作息会变
UNUSUAL_SILENCE = 240 # 常态时段里安静超过这么多分钟，才算「跟她平常不一样」


def _hour(v, default):
    try:
        return max(0, min(23, int(v)))
    except (TypeError, ValueError):
        return default


DAY_STARTS_AT = _hour(os.environ.get('LIVING_CONTEXT_DAY_STARTS_AT'), 6)

now = datetime.datetime.now


def _parse(t):
    if isinstance(t, datetime.datetime):
        return t
    s = str(t or '').strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.datetime.strptime(s[:len('2026-01-01 00:00:00')], fmt)
        except ValueError:
            pass
    return None


def _fold(dt):
    """折进她的一天：夜界以前算前一天的延长。返回 (她的那天, 当天第几分钟，可能 ≥ 1440)。"""
    boundary = DAY_STARTS_AT * 60
    minute = dt.hour * 60 + dt.minute
    day = dt.date()
    if minute < boundary:
        minute += 24 * 60
        day = day - datetime.timedelta(days=1)
    return day, minute


def _hhmm(m):
    return '%02d:%02d' % ((m // 60) % 24, m % 60)


def profile(times, at=None, lookback_days=LOOKBACK_DAYS, min_days=MIN_DAYS):
    """算出她的节律。times 是她发消息的时间（datetime 或 'YYYY-MM-DD HH:MM[:SS]'），只要她的，别混进模型自己的。
    返回 dict；样本不足时 confidence='low'，别用。"""
    at = at or now()
    cutoff = (at - datetime.timedelta(days=lookback_days)).date()
    days = {}
    for t in times or []:
        dt = _parse(t)
        if dt is None:
            continue
        day, minute = _fold(dt)
        if day < cutoff:
            continue
        days.setdefault(day, []).append(minute)
    if not days:
        return {'confidence': 'low', 'reason': 'no_data', 'n_days': 0}
    firsts = [min(v) for v in days.values()]
    lasts = [max(v) for v in days.values()]
    n = len(days)
    if n < min_days:
        return {'confidence': 'low', 'reason': 'few_days', 'n_days': n}

    def med(a):
        a = sorted(a)
        return a[len(a) // 2]

    first, last = med(firsts), med(lasts)
    return {
        'confidence': 'ok' if n >= THIN_DAYS else 'thin',
        'n_days': n,
        'typical_first_min': first,
        'typical_last_min': last,
        'typical_first': _hhmm(first),
        'typical_last': _hhmm(last),
    }


def silence_verdict(prof, minutes_silent, at=None):
    """她安静了这么久，正常吗？唯一的对外判断，只用来压住模型的疑神疑鬼，不生成任何要推给她的话：
      normal       在她常态活跃时段内的安静，正常，别当成困了
      after_sleep  过了她常态最晚的点，安静是应该的
      before_wake  她常态还没起——早上八点半对她是「还在睡」，不是「熬得很晚」，说法得准
      unusual      明显超出她自己的常态，才值得放在心上（仍不主动推送）
      unknown      样本不足"""
    if not prof or prof.get('confidence') == 'low':
        return 'unknown'
    _, cur = _fold(at or now())
    first = prof.get('typical_first_min') or 10 * 60
    last = prof.get('typical_last_min') or 25 * 60
    if cur > last:
        return 'after_sleep'
    if cur < first:
        return 'before_wake'
    if minutes_silent >= UNUSUAL_SILENCE:
        return 'unusual'
    return 'normal'


def render_line(prof, her='她'):
    """挂进注入的一行事实。样本不足就空串。只说她平常什么样，不说模型该怎么做。
    挂在不进 system 前缀的地方（她消息后面的 system_hint），一天变一次，不破缓存。"""
    if not prof or prof.get('confidence') == 'low':
        return ''
    return ('[%s的作息（%d 天真实到达记录算出）：常态出现约 %s，常态最晚约 %s。'
            '这个区间里%s安静一会儿，是%s平常的样子。]'
            % (her, prof.get('n_days', 0), prof.get('typical_first') or '未知',
               prof.get('typical_last') or '未知', her, her))
