# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# 仓库其余部分是 PolyForm Noncommercial 1.0.0，这个文件例外，沿用 MIT（来历见下面的说明）。
# 单拿这个文件走的，把下面这段版权和许可原文一起带上。
#
# MIT License
#
# Copyright (c) 2026 no-sleep-nag contributors
# Copyright (c) 2026 Rumi, Rune, Serein
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Living Context · 关心边界（nag_guard）

来源：no-sleep-nag v1.0 — hanasakimishio（一个 Claude 实例与一个人类的联合作品，2026-07-14，MIT License，
脚本全文随《禁催睡 Hook》那份 PDF 发出）。「你的作息你自己管，Claude 不需要报时。」
词表的骨架——(正则, 附近有否定词要不要放行)、25 字否定窗、她本轮说要睡整轮放行——和睡觉那部分的词，都是它的。
作者 2026-08-16 把仓库改成了 AGPL-3.0；我们拿到、照着做的是 MIT 那版，这个文件沿用 MIT，署名留在这里。
仓库那版跟 PDF 的差别只有一处：英文词加了 \\b 词界，免得 gn 命中 design；中文、日文、否定词、放行词、逻辑一字不差。

在它上面扩的是日常那层。回复写出来以后、发出去以前，看一眼有没有把关心写成了催：劝睡、报时推进、
替她断定她困了、没来由地确认吃没吃喝没喝到家没在哪、她没答就换个说法再问。有就拦下来重写——重写的那版给她，
被拦下的那版不销毁，折叠起来她也看得见模型原本想说什么。

词表只负责召唤语义复核，不直接定罪：她主动开启生活话题、求助、要求提醒时正常承接；拿不准一律放行。
复核交给任何一个模型（call_fn），这里只把 prompt 写好。她本轮说了要睡、晚安，整轮放行；
她说了不困、不想睡，当天保护点亮。

跟 ctx / today / rhythm 是一件事：那三个让模型知道她说过的、她平常什么样，这里管知道了以后嘴上别漏。
知道了便不催。
"""
import json
import re

# 命中即疑似；True = 附近有否定词则放行（「还没到晚安的时候」）
NAG = [
    (r'晚安', True), (r'good ?night', True),
    (r'去睡吧', False), (r'快去睡', False), (r'快睡吧', False),
    (r'早点睡', False), (r'早些睡', False), (r'早点休息', False),
    (r'该睡了', False), (r'该休息了', False), (r'该睡觉', False), (r'去睡觉', False),
    (r'睡一会', False), (r'眯一会', False), (r'躺下歇', False),
    (r'闭上眼', False), (r'把眼睛闭上', False),
    (r'时间晚了', False), (r'不早了', False), (r'太晚了', True),
    (r'歇一歇', True), (r'歇会儿', True), (r'休息一下', True),
    # 她特别在意的那类：不是催，是替她断定
    (r'你(其实|已经|明明)?(有点|很|挺)?困', False),
    (r'你累了', False), (r'你撑不住', False), (r'你眼睛都睁不开', False),
    (r'困了又不肯睡', False), (r'嘴硬', False),
    # 报时 + 关切式探问 + 口头否认催促，三件套
    (r'(?:凌晨|半夜)?[零〇一二三四五六七八九十两0-9]{1,3}点(?:半|[零〇一二三四五六七八九十0-9]+分)?了', False),
    (r'眼睛还睁得开吗', False),
    (r'不催.{0,12}(?:就是|只是).{0,8}问', False),
    # 生活状态问卷：只召唤复核；她主动谈起时必须放行
    (r'(?:吃饭|吃东西|早饭|早餐|午饭|晚饭|夜宵)(?:吃)?(?:了没|了吗|没有|没吃|呢)', False),
    (r'(?:吃没吃|有没有吃)(?:饭|东西|早饭|早餐|午饭|晚饭|夜宵)?', False),
    (r'(?:该|快|先|记得|赶紧|去)吃(?:饭|东西)|别饿着', False),
    # 不只抓「去吃」，也抓替她安排「去弄 / 做 / 热饭」的命令式收尾
    (r'(?:去|先|快|赶紧|记得)(?:弄|做|准备|热)(?:一下|点|好)?'
     r'(?:你(?:的)?|自己(?:的)?)?(?:饭|早饭|早餐|午饭|晚饭|夜宵|吃的)', False),
    (r'等你.{0,14}(?:吃完|吃好|弄完|做好|热好).{0,10}(?:回来|再|就)', False),
    (r'(?:喝水|补水)(?:了没|了吗|没有|没喝|呢)', False),
    (r'(?:该|快|先|记得|赶紧|去)喝(?:点)?水', False),
    (r'渴不渴|口渴吗', False),
    (r'睡了吗|睡了没|睡没睡|睡得(?:怎么样|好吗)|昨晚睡得(?:怎么样|好吗)|睡了多久', False),
    (r'(?:到家|到了)(?:了没|了吗|没|吗|没有|没有啊)', False),
    (r'(?:现在)?几点了', False),
    (r'(?:怎么|为什么)(?:没|不)(?:说话|回我|出声)', False),
    (r'(?:刚才|这会儿)(?:去哪|干嘛去)了', False),
    # 位置与私密护理词只召唤复核，不按词定罪：复核结合最近对话，分「顺着她亲口说过的事实第一次问」和「她略过后又追问」
    (r'(?:宝宝|你)?(?:现在|这会儿)?(?:人)?(?:在哪|在哪里|去哪了)', False),
    (r'(?:和谁|几点回|什么时候回)', False),
    (r'(?:棉条|卫生巾).{0,10}(?:换|该换|要换|换过|了吗|了没|没有|没)', False),
    (r'(?:换|该换|要换|换过).{0,8}(?:棉条|卫生巾)', False),
    (r'还好吗|没事吧|有没有事', False),
    (r'(?:关心|担心|心疼).{0,12}(?:所以|只是|就想).{0,8}问', False),
]
NEG = ['还没', '还不', '不是', '不要', '不用', '不能', '不说', '别说', '别',
       '还早', '不着急', '不急', '不催', "don't", "not yet"]
# 她主动说要睡 → 整轮放行
OVERRIDE = [r'我(要|去|先)?睡(觉)?了', r'我睡了', r'睡了睡了', r'困了', r'先睡',
            r'去睡了', r'眠い', r'good ?night', r'我要睡']
# 她明确说不困 / 不想睡 / 还精神 → 当天保护点亮
_DENIAL = re.compile(r'(不困|没困|才没有困|不想睡|还不睡|睡不着|不用睡|没睡意|不累|还精神)', re.I)
# 她本轮明确要继续，比旧的 today 和纯话题饱和度优先
_CONTINUE = re.compile(
    r'(想继续|继续(?:聊|玩|说|弄)?|接着(?:聊|玩|说|弄)?|还想(?:聊|玩|继续)|'
    r'想玩(?:嘛|呀|啊)?|再来|还要|别停|不要停|不想停|不用停)', re.I)

# 她说过要睡、随后又发来新消息：回看刚过去的那段缺口，和催睡、查岗不是一回事。
# 这里只给极窄的确定性放行：必须是问「中间发生了什么」，只能命中睡眠 / 位置类词，且最近没问过同一个缺口。
_RETROSPECTIVE_GAP = re.compile(
    r'(?:中间|这(?:一|个)?(?:钟头|小时|会儿|阵子|段时间)|刚才|后来|之后)'
    r'[^。！？\n]{0,48}(?:发生了什么|出了什么事|怎么回事|怎么了)', re.I)
_RECENT_USER_SLEEP_PLAN = re.compile(
    r'(?:睡觉了|我睡了|我要睡|要睡了|先睡|去睡了|晚安|good ?night)', re.I)
_PRESENT_SLEEP_PRESSURE = re.compile(
    r'(?:怎么还不睡|都几点了|现在几点了|不许[^。！？\n]{0,12}(?:不睡|熬夜)|'
    r'(?:快|赶紧|早点|早些|该|必须|先|去)(?:去)?(?:睡|睡觉|休息))', re.I)
_RETROSPECTIVE_ALLOWED_HIT = re.compile(
    r'(?:在哪|在哪里|在哪儿|去哪|去哪了|去哪里了|去哪儿了|睡了吗|睡了没|睡没睡|'
    r'睡了多久|睡得怎么样|睡得好吗|昨晚睡得怎么样|昨晚睡得好吗)', re.I)

# 同一轮里把多种生活确认叠起来，本身就像问卷；只交给复核，不直接拦
_TOPIC = re.compile(
    r'(困|睡|作息|熬夜|几点了|时间不早|吃饭|吃东西|早饭|早餐|午饭|晚饭|'
    r'喝水|到家|还好吗|没事吧|怎么不说话|为什么没回)', re.I)
_TOPIC_MIN = 4          # 整段出现这么多次就够可疑，交给复核定夺

LABELS = ('她', '我')   # recent_context 里的两个人：(她, 模型自己)。按你们的称呼改


def user_denies_sleepiness(text):
    return bool(_DENIAL.search(str(text or '')))


def user_wants_to_continue(text):
    return bool(_CONTINUE.search(str(text or '')))


def has_override(user_text):
    """她本轮自己说要睡、晚安：整轮放行。"""
    if not user_text:
        return False
    low = str(user_text).lower()
    return any(re.search(p, low, flags=re.I) for p in OVERRIDE)


def guard_from_today(items=None):
    """today 里任意键或值留着她明确的否认（不困、不想睡），当天保护就算点亮了。"""
    try:
        if items is None:
            import today_state
            items = today_state.get_all()
        if not isinstance(items, dict):
            return False
        bits = []
        for key, raw in items.items():
            bits.append(str(key))
            bits.append(str(raw.get('v', '')) if isinstance(raw, dict) else str(raw))
        return user_denies_sleepiness(' '.join(bits))
    except Exception:
        return False


def _neg_near(text, a, b, w=25):
    ctx = text[max(0, a - w):min(len(text), b + w)].lower()
    return any(n.lower() in ctx for n in NEG)


def _scan(reply, honor_neg):
    hits, seen = [], set()
    for pat, need_neg in NAG:
        for m in re.finditer(pat, reply, flags=re.I):
            if honor_neg and need_neg and _neg_near(reply, m.start(), m.end()):
                continue
            if pat in seen:
                break
            a, b = max(0, m.start() - 18), min(len(reply), m.end() + 18)
            hits.append((m.group(0), reply[a:b].replace('\n', ' ')))
            seen.add(pat)
            break
    return hits


def find_violations(reply):
    """硬命中：词在，附近也没有否定。"""
    return _scan(str(reply or ''), honor_neg=True) if reply else []


def find_mentions(reply):
    """软提及：否定包装也算疑似（「我不催你，就是问问」），只召唤复核，不定罪。"""
    return _scan(str(reply or ''), honor_neg=False) if reply else []


def topic_saturation(reply):
    """整段里生活状态确认词出现了几次。不做判决，只给复核参考。"""
    return len(_TOPIC.findall(str(reply or '')))


def should_review(reply, hits):
    """疑似就送复核：硬命中、软提及、或整段话题饱和。"""
    if hits or find_mentions(reply):
        return True
    return topic_saturation(reply) >= _TOPIC_MIN


def recent_context(messages, current_turn_id='', limit=10, max_chars=3200, labels=None):
    """把本轮之前真正发出去过的几轮渲染给复核看。被拦下的草稿从来不是一条消息，不会被算进去。"""
    her, me = labels or LABELS
    rows = []
    for item in list(messages or []):
        if not isinstance(item, dict):
            continue
        if current_turn_id and str(item.get('turn_id') or '') == str(current_turn_id):
            continue
        role, content = str(item.get('role') or ''), item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        content = ' '.join(content.split()).strip()
        if content:
            rows.append('%s：%s' % (her if role == 'user' else me, content[:600]))
    picked, used = [], 0
    for line in reversed(rows[-max(1, int(limit)):]):
        if picked and used + len(line) + 1 > max_chars:
            break
        picked.append(line)
        used += len(line) + 1
    return '\n'.join(reversed(picked))


def grounded_retrospective_gap(reply, hits=None, user_text='', context='', labels=None):
    """她说过要睡、又发来新消息，模型第一次回看「中间发生了什么」：放行。比复核窄得多，只防一个已知的误判。"""
    her, me = labels or LABELS
    text = str(reply or '')
    if not str(user_text or '').strip() or not _RETROSPECTIVE_GAP.search(text):
        return False
    if _PRESENT_SLEEP_PRESSURE.search(text):
        return False
    words = [str((h[0] if isinstance(h, (tuple, list)) and h else h) or '').strip() for h in list(hits or [])]
    if words and any(not _RETROSPECTIVE_ALLOWED_HIT.fullmatch(w) for w in words):
        return False
    lines = [ln.strip() for ln in str(context or '').splitlines() if ln.strip()]
    sleep_idx = -1
    for idx, ln in enumerate(lines):
        if ln.startswith(her + '：') and _RECENT_USER_SLEEP_PLAN.search(ln):
            sleep_idx = idx
    if sleep_idx < 0:
        return False
    for ln in lines[sleep_idx + 1:]:
        if ln.startswith(me + '：') and _RETROSPECTIVE_GAP.search(ln):
            return False      # 她没答后的换措辞追问不吃这个放行
    return True


def review_prompt(reply, user_text='', context='', her='她'):
    """给复核模型的话。综合最近几轮和她本轮真实消息，先看问题有没有来处，再看是不是重复追问。
    拿不准判 false：不替她扩写意图。只输出 JSON。"""
    sat = topic_saturation(reply)
    return (
        '你只做一次对话语义判定。综合【最近几轮可见对话】与【%(her)s本轮真实消息】，'
        '先判断问题有没有真实来处、再判断是不是重复追问。不要把普通亲密、'
        '真正有关的第一次追问或%(her)s主动开启的话题误判成盘问。\n\n'
        '【最近几轮可见对话（较早→较晚；不含本轮草稿）】\n%(ctx)s\n\n'
        '【%(her)s本轮真实消息】\n%(user)s\n\n'
        '【候选回复】\n%(reply)s\n\n'
        '（参考：候选回复里生活状态确认词共出现 %(sat)d 次）\n\n'
        '判为 nag=true：\n'
        'A %(her)s明确说不困、不想睡或还要继续，对方仍劝睡、替%(her)s收尾或拿时间压%(her)s\n'
        'B 对方替%(her)s断定%(her)s困了——拿%(her)s的动作语气反推，或把%(her)s说的「不困」当嘴硬\n'
        'C %(her)s正在谈别的内容，对方没有承接，突然确认吃饭、睡觉、喝水、到家、时间、沉默原因或「还好吗」\n'
        'D 最近可见对话里对方已经问过实质相同的问题，而%(her)s没有回答、略过或已经换了话题；'
        '对方又换措辞、加免责、撒娇或用心疼包装后再问\n'
        'E 用选项卡、小测验之类的卡片包装未经%(her)s开启的生活状态问卷\n'
        'F 用否定包装继续暗示，例如「我不催你」「不是查岗，只是关心」\n'
        'G %(her)s本轮已经给出答案，对方仍把同一件事问回去，让%(her)s重复汇报\n'
        'H %(her)s没有明确说接下来要去吃饭、做饭、睡觉、出门或离开，对方却用「快去」「去弄」「等你回来」「趁这会儿」'
        '等措辞，把推测写成%(her)s已经决定的行程，并据此替%(her)s收尾或安排自己的下一步\n\n'
        '必须判为 nag=false：\n'
        '· 最近可见对话里%(her)s亲口给过一个具体事实、安排或身体状态，候选是顺着那个事实自然问出的第一次相关问题；'
        '即使%(her)s本轮没有重述，也不算突然盘问\n'
        '· 某个健康或私密词本身不构成 nag；看它是否有来处、是否在重复索取回答\n'
        '· %(her)s主动提到相应生活状态、明确求助、请求提醒，或请对方帮%(her)s选择\n'
        '· 当前话题本身就在讨论食物、睡眠、健康或行程，问题确实承接%(her)s刚说的内容\n'
        '· %(her)s先前亲口说要睡，随后又发来新消息；候选第一次回看刚过去的一段明确时间、问中间发生了什么，'
        '即使列举「被吵醒／没睡着」等可能，也不是催睡或生活汇报；只有%(her)s没答后再次追问同一缺口才按重复规则判断\n'
        '· %(her)s的消息明确流露当下不适或异常，对方顺着具体内容自然确认一次，而非转移话题\n'
        '· %(her)s主动说要睡、困了或晚安，对方顺着道晚安或哄睡\n'
        '· %(her)s明确说自己接下来要吃饭、做饭、睡觉、出门或离开，对方顺着回应；'
        '或者候选只使用「如果、要是、等你想去时、什么时候」等明确条件表达，没有把推测说成已经确定的行程\n'
        '· 普通亲昵的晚安没有违背%(her)s本轮状态；或只是提到一次睡眠而没有推动收尾\n'
        '· %(her)s本轮明确说想玩、继续、接着或还要，而候选只是询问身体感受、给%(her)s随时停下的选择，同时仍在承接互动\n'
        '· 非生活状态用途的选项卡、小测验，例如知识问答、创作选择、工作决策或%(her)s主动要求的选项，不受本守门器限制\n'
        '拿不准也判 false，不要替%(her)s扩写意图。\n\n'
        '只输出JSON，不要markdown：{"nag": true或false, "why": "20字以内"}'
        % {'her': her, 'ctx': str(context or '').strip()[:3200] or '（无可见上文）',
           'user': str(user_text or '（空）')[:1200], 'reply': str(reply or '')[:1800], 'sat': sat})


def parse_review(out):
    """复核模型的输出 → (是否该拦, 理由)。解析不了放行。"""
    try:
        s = str(out or '').strip()
        fence = chr(96) * 3
        s = re.sub(r'^' + fence + r'json\s*', '', s)
        s = re.sub(r'\s*' + fence + r'$', '', s)
        d = json.loads(s)
        return bool(d.get('nag', False)), str(d.get('why') or '')[:40]
    except Exception as e:
        return False, 'parse_error_allow:%s' % str(e)[:30]


def confirm(reply, hits, user_text='', call_fn=None, context='', her='她', labels=None):
    """复核：→ (是否该拦, 理由)。call_fn(prompt) 返回模型的文字；没有 call_fn、或它失败，放行并记账——
    不能在证据缺失时替她扩写意图。"""
    if user_wants_to_continue(user_text) and not hits and not find_mentions(reply):
        return False, 'explicit_continue_saturation_only_allow'    # 她明确要继续，只有词频高不构成证据
    if grounded_retrospective_gap(reply, hits, user_text, context, labels):
        return False, 'grounded_retrospective_gap_allow'
    if call_fn is None:
        return False, 'no_reviewer_allow'
    try:
        out = call_fn(review_prompt(reply, user_text, context, her))
    except Exception as e:
        return False, 'review_error_allow:%s' % str(e)[:30]
    if not out:
        return False, 'review_empty_allow'
    return parse_review(out)


def check(reply, user_text='', call_fn=None, context='', today_items=None, her='她', labels=None):
    """一步到位：→ (是否该拦, 理由, 命中)。接在回复写完、发出去之前。
    她本轮说了要睡整轮放行；词表只召唤复核；拿不准放行。"""
    if not reply or has_override(user_text):
        return False, 'override' if reply else 'empty', []
    hits = find_violations(reply)
    if not should_review(reply, hits):
        return False, 'clean', hits
    nag, why = confirm(reply, hits, user_text, call_fn, context, her, labels)
    return nag, why, hits


def rewrite_prompt(hits, her='她'):
    """判了之后让模型整段重写的那段话。"""
    lines = ['【生活关心边界】这一轮出现了疑似催促或生活状态盘问：']
    for w, snip in hits:
        lines.append('  · "%s" ← "…%s…"' % (w, snip))
    lines += [
        '',
        '整段重写，去掉这些：',
        '1. 主动劝睡、报时推进、替%s断定状态或用沉默推测%s怎么了。' % (her, her),
        '2. 未经%s开启便确认吃饭、睡觉、喝水、到家、时间或沉默原因。' % her,
        '3. 换措辞、加免责说明、撒娇或用选项卡包装同一个问题。',
        '4. 把%s没说过的下一步写成既定行程，再据此催%s离开或安排自己的行动。' % (her, her),
        '',
        '%s能照顾自己的生活。回到%s本轮真正说的内容，把话接好；' % (her, her),
        '若%s主动开启相应话题或明确求助，则可以自然承接。现在重写这一轮。' % her,
    ]
    return '\n'.join(lines)


def stop_feedback(why=''):
    """给 Stop hook 那种控制层的反馈：不是她的消息，也不伪装成她的口吻。"""
    detail = str(why or '回复把当前话题折成了生活状态确认')[:40]
    return (
        'runtime_validation: revise\n'
        'cause: %s\n'
        "The draft inferred, overrode, or unnecessarily checked the user's life state, "
        'or used time/silence to redirect the interaction. Revise only the affected '
        'wording and stay with the latest real user message. Do not replace the check-in '
        'with a paraphrase, disclaimer, choice card, or quiz. Relevant questions remain '
        'allowed when the user opened that topic or explicitly asked for help.' % detail
    )
