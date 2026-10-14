# -*- coding: utf-8 -*-
"""Living Context · 快照回执（SDK 长会话用）

长会话不换窗、不 breath，背景快照只能挂在用户消息后面。可它不该每轮都挂：
同一份内容挂第二次，是在花钱让模型重读它已经读过的东西。

做法：给快照内容算一个 revision（sha256）。某个 session 收过同一 revision，
并且那段文字还留在会话上下文里（没被 compact 掉），就什么都不挂；否则挂一次，
发出去以后记一张回执。内容一变，revision 就变，自然会再挂一次。

「还留在上下文里」怎么判断：Claude Agent SDK 会把会话写成 transcript（jsonl），
这里直接去 transcript 里找 marker；遇到 compact 边界就当作之前的都没了。
没有 transcript 可查的环境，退化成「同一 revision 只挂一次」。
"""
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

DB = os.environ.get('HINT_SNAPSHOTS_DB', 'hint_snapshots.db')
TRANSCRIPTS = Path(os.environ.get('HINT_TRANSCRIPTS_DIR', ''))  # 例：~/.claude/projects/<项目目录名>
KINDS = ('today', 'ctx')
PATTERN = re.compile(r'<state_snapshot kind="(%s)" revision="([a-f0-9]{64})">' % '|'.join(KINDS))


def _connect():
    c = sqlite3.connect(DB, timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS receipts (sid TEXT, kind TEXT, revision TEXT, PRIMARY KEY(sid, kind))')
    return c


def _still_in_context(sid, marker):
    """transcript 里、最后一个 compact 边界之后，还能找到这段 marker 吗？"""
    path = TRANSCRIPTS / (sid + '.jsonl')
    if not str(TRANSCRIPTS) or not path.exists():
        return True   # 查不了就当还在：宁可少挂一次，也不每轮重挂
    found = False
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            obj = json.loads(line)
            if obj.get('subtype') == 'compact_boundary' or obj.get('isCompactSummary'):
                found = False
                continue
            if obj.get('type') != 'user':
                continue
            content = obj.get('message', {}).get('content', '')
            texts = [content] if isinstance(content, str) else [
                b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']
            if any(marker in t for t in texts):
                found = True
    except (OSError, ValueError, TypeError):
        return True
    return found


def prepare(sid, kind, body):
    """返回要挂的文本；不需要挂就返回空串。"""
    if kind not in KINDS:
        raise ValueError(kind)
    revision = hashlib.sha256(body.encode('utf-8')).hexdigest()
    marker = '<state_snapshot kind="%s" revision="%s">' % (kind, revision)
    if sid and re.fullmatch(r'[a-zA-Z0-9-]+', sid):
        with _connect() as c:
            row = c.execute('SELECT revision FROM receipts WHERE sid=? AND kind=?', (sid, kind)).fetchone()
        if row and row[0] == revision and _still_in_context(sid, marker):
            return ''
    return marker + '\n' + body + '\n</state_snapshot>'


def commit(sid, sent_text):
    """消息真的发出去之后调用：把这次挂上的快照记成回执。"""
    if not sid:
        return
    with _connect() as c:
        for kind, revision in PATTERN.findall(sent_text or ''):
            c.execute('INSERT OR REPLACE INTO receipts VALUES (?, ?, ?)', (sid, kind, revision))
