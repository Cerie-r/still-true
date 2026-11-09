# -*- coding: utf-8 -*-
"""living_context 的几条底线。只用标准库：python3 -m unittest discover -s tests"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import living_context as lc  # noqa: E402

D0 = datetime.date(2026, 11, 1)
REAL_TODAY = lc.today        # 真的那个 today()，按 DAY_STARTS_AT 算；测几点算新的一天时用


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (lc.PATH, lc.ARCHIVE_PATH, lc.today, lc.now, lc.GRACE_DAYS, lc.CHANNELS)
        lc.PATH = os.path.join(self.tmp.name, 'lc.json')
        lc.ARCHIVE_PATH = os.path.join(self.tmp.name, 'lc_archive.json')
        self.day = D0
        lc.today = lambda: self.day

    def tearDown(self):
        lc.PATH, lc.ARCHIVE_PATH, lc.today, lc.now, lc.GRACE_DAYS, lc.CHANNELS = self.saved
        self.tmp.cleanup()

    def archive(self):
        with open(lc.ARCHIVE_PATH, encoding='utf-8') as f:
            return json.load(f)['items']

    def go(self, days):
        self.day = D0 + datetime.timedelta(days=days)

    def raw(self):
        with open(lc.PATH, encoding='utf-8') as f:
            return json.load(f)


class Unverified(Base):
    """到期没核实的：递到面前问一句；满三个她在的日子还没动静才归档，她不在的日子不算。"""

    def setUp(self):
        super().setUp()
        self.here = True
        self.saved_here = lc.present_today
        lc.present_today = lambda: self.here

    def tearDown(self):
        lc.present_today = self.saved_here
        super().tearDown()

    def test_days_she_is_away_do_not_count(self):
        lc.set_item('考试', '在准备期末', ttl_days=3)
        self.here = False                      # 只有模型自己起的轮次在读
        for k in range(4, 30):
            self.go(k)
            lc.render_breath_block()
            lc.render_snapshot()
            lc.render_due_for_today()
        fresh, stale = lc.get_items()
        self.assertEqual([x['key'] for x in stale], ['考试'])
        self.assertFalse(os.path.exists(lc.ARCHIVE_PATH))

    def test_archived_after_three_days_she_was_here(self):
        lc.set_item('考试', '在准备期末', ttl_days=3)
        self.go(5); lc.render_breath_block(); lc.render_breath_block()   # 同一天看几次只算一天
        self.go(9); lc.render_snapshot()
        self.go(9); fresh, stale = lc.get_items()
        self.assertEqual([x['key'] for x in stale], ['考试'])           # 才两天
        self.go(12); lc.render_due_for_today()
        self.go(12); fresh, stale = lc.get_items()
        self.assertEqual([x['key'] for x in stale], ['考试'])           # 第三天当天还在
        self.go(13); fresh, stale = lc.get_items()
        self.assertEqual(stale, [])                                       # 第四个她在的日子收起来
        tomb = self.raw()['dropped'][0]
        self.assertEqual((tomb['key'], tomb['reason']), ('考试', 'expired'))
        self.assertIn('已到期归档', lc.render_pending_block('api'))

    def test_breath_says_unverified_and_how_it_ends(self):
        lc.set_item('考试', '在准备期末', ttl_days=3)
        self.go(5)
        b = lc.render_breath_block()
        self.assertIn('还没核实', b)
        self.assertIn('满三个她在的日子', b)

    def test_none_keeps_it_hanging(self):
        lc.GRACE_DAYS = None
        lc.set_item('考试', '在准备期末', ttl_days=3)
        for k in range(5, 20):
            self.go(k)
            lc.render_breath_block()
        fresh, stale = lc.get_items()
        self.assertEqual([x['key'] for x in stale], ['考试'])

    def test_today_tail_counts_days_she_was_here(self):
        lc.set_item('考试', '在准备期末', ttl_days=3)
        self.go(10)
        self.here = False
        self.assertIn('考试', lc.render_due_for_today())   # 她不在也照样带，只是不占那两天
        self.here = True
        self.go(11)
        self.assertIn('考试', lc.render_due_for_today())
        self.assertIn('考试', lc.render_due_for_today())
        self.go(15)
        self.assertIn('考试', lc.render_due_for_today())
        self.go(16)
        self.assertEqual(lc.render_due_for_today(), '')    # 带满两天：这里不再提
        self.assertIn('考试', lc.render_breath_block())     # 但还挂着（这才第三个她在的日子）

    def test_renewing_starts_over(self):
        lc.set_item('考试', '在准备期末', ttl_days=3)
        self.go(10); lc.render_due_for_today()
        self.go(11); lc.render_due_for_today()
        lc.set_item('考试', '还在准备，下周考', ttl_days=7)
        fresh, stale = lc.get_items()
        self.assertEqual([x['key'] for x in fresh], ['考试'])
        self.go(19)
        self.assertIn('还在准备', lc.render_due_for_today())

    def test_nothing_due_means_empty_tail(self):
        lc.set_item('考试', '在准备期末', ttl_days=30)
        self.assertEqual(lc.render_due_for_today(), '')

    def test_snapshot_and_pending_say_unverified(self):
        lc.set_item('考试', '在准备期末', ttl_days=3)
        lc.mark_synced('api')
        self.go(5)
        snap = lc.render_snapshot()
        self.assertIn('考试（到期了，还没核实）', snap)
        self.assertIn('满三个她在的日子', snap)
        block = lc.render_pending_block('api')       # fresh→未核实的翻转会再挂一次
        self.assertIn('考试（到期了，还没核实）', block)
        self.assertIn('「考试」', block)


class Tombstones(Base):
    """墓碑会走：通道收了就清，两周不收也清；同键只留一块。"""

    def test_dropping_after_asking_leaves_one_tombstone(self):
        lc.set_item('考试', '在准备期末')
        self.assertEqual(lc.drop_item('考试'), 1)
        pend = lc.pending_items('api')
        self.assertEqual([(x['key'], bool(x.get('dropped'))) for x in pend], [('考试', True)])

    def test_default_channels_let_tombstones_go(self):
        lc.set_item('考试', '在准备期末')
        lc.drop_item('考试')
        lc.mark_synced('api')
        self.assertEqual(self.raw().get('dropped'), [])

    def test_tombstones_leave_after_two_weeks_even_unsynced(self):
        lc.set_item('考试', '在准备期末')
        lc.drop_item('考试')
        self.go(lc.TOMBSTONE_DAYS - 1)
        lc.get_items()
        self.assertEqual(len(self.raw().get('dropped')), 1)
        self.go(lc.TOMBSTONE_DAYS)
        lc.get_items()
        self.assertEqual(self.raw().get('dropped'), [])

    def test_same_key_keeps_only_latest_tombstone(self):
        for i in range(5):
            lc.set_item('在读', '第 %d 章' % i)
            lc.drop_item('在读', reason='closed')
        tombs = self.raw()['dropped']
        self.assertEqual(len(tombs), 1)
        self.assertEqual(tombs[0]['value'], '第 4 章')

    def test_key_set_again_hides_its_tombstone(self):
        lc.set_item('在读', '第 1 章')
        lc.drop_item('在读', reason='closed')
        lc.set_item('在读', '第 2 章')
        pend = lc.pending_items('api')
        self.assertEqual([(x['key'], x.get('value'), bool(x.get('dropped'))) for x in pend],
                         [('在读', '第 2 章', False)])

    def test_legacy_two_channel_file(self):
        with open(lc.PATH, 'w', encoding='utf-8') as f:
            json.dump({'items': [], 'dropped': [
                {'key': '旧', 'value': 'x', 'reason': 'dropped', 'dropped_at': D0.isoformat(),
                 'synced': {'api': True, 'sdk': False}},
                {'key': '新', 'value': 'y', 'reason': 'dropped', 'dropped_at': D0.isoformat(),
                 'synced': {'api': False, 'sdk': False}},
            ]}, f)
        lc.get_items()
        self.assertEqual([t['key'] for t in self.raw()['dropped']], ['新'])

    def test_turn_writes_kept_a_week(self):
        lc.set_item('考试', '在准备期末', source_turn='t1', source_session='s1')
        self.go(lc.TURN_WRITES_DAYS - 1)
        lc.get_items()
        self.assertEqual(len(self.raw()['_turn_writes']), 1)
        self.go(lc.TURN_WRITES_DAYS)
        lc.get_items()
        self.assertEqual(self.raw()['_turn_writes'], [])

    def test_legacy_turn_writes_are_dated_from_now(self):
        with open(lc.PATH, 'w', encoding='utf-8') as f:
            json.dump({'items': [], '_turn_writes': [
                {'turn': 't0', 'session': 's0', 'key': 'k', 'revision': 'r', 'before': None}]}, f)
        lc.get_items()
        self.assertEqual(self.raw()['_turn_writes'][0]['day'], D0.isoformat())

    def test_undo_this_turn_still_works(self):
        lc.set_item('考试', '在准备期末', source_turn='t1', source_session='s1')
        lc.set_item('考试', '考完了', source_turn='t2', source_session='s1')
        self.assertEqual(lc.undo_turn_writes('t2', 's1'), {'restored': 1, 'skipped': 0})
        fresh, stale = lc.get_items()
        self.assertEqual(fresh[0]['value'], '在准备期末')



class Sync(Base):
    """摘要只认交给它的那几条。"""

    def test_mark_synced_only_what_the_summary_saw(self):
        lc.set_item('考试', '在准备期末')
        lc.set_item('出差', '下周去悉尼')
        lc.drop_item('出差')
        pending = lc.pending_items('api')        # 交给摘要的那一份
        lc.set_item('考试', '考完了')               # 写摘要那几十秒里，别的入口改了一条
        lc.set_item('搬家', '月底搬')               # 又新记了一条
        lc.mark_synced('api', pending)
        left = sorted((x['key'], x.get('value')) for x in lc.pending_items('api'))
        self.assertEqual(left, [('搬家', '月底搬'), ('考试', '考完了')])
        self.assertEqual(self.raw().get('dropped'), [])   # 墓碑摘要看过了，收掉

    def test_old_call_still_marks_everything(self):
        lc.set_item('考试', '在准备期末')
        lc.mark_synced('api')
        self.assertEqual(lc.pending_items('api'), [])



class SetItem(Base):
    """一件一行：还在只传 key；变了丢旧建新；until 写日子，没到不冲掉；满 30 天那天就问。"""

    def test_key_only_renews_and_keeps_the_sentence(self):
        lc.set_item('考试', '在准备期末', ttl_days=3)
        self.go(10)
        lc.set_item('考试')                                  # 她说还在
        fresh, stale = lc.get_items()
        x = fresh[0]
        self.assertEqual((x['value'], x['noted_at']), ('在准备期末', D0.isoformat()))
        self.assertEqual(x['recheck_after'], (self.day + datetime.timedelta(days=29)).isoformat())
        self.assertFalse(os.path.exists(lc.ARCHIVE_PATH))

    def test_a_month_means_day_thirty(self):
        lc.set_item('考试', '在准备期末')                    # 默认 30 天
        self.go(29)
        self.assertEqual([x['key'] for x in lc.get_items()[0]], ['考试'])      # 第 29 天还不问
        self.go(30)
        self.assertEqual([x['key'] for x in lc.get_items()[1]], ['考试'])      # 满一个月那天递回来
        lc.set_item('出差', '在悉尼', ttl_days=1)
        self.assertEqual([x['key'] for x in lc.get_items()[0]], ['出差'])      # ttl 1：今天还好好的
        self.go(31)
        self.assertIn('出差', [x['key'] for x in lc.get_items()[1]])          # 明天就问

    def test_renewing_does_not_wash_out_a_known_until(self):
        lc.set_item('学业', '期末在墨尔本', until='2026-11-13')
        self.go(9)
        lc.set_item('学业')                                  # 11/10 她随口说一句还在上课
        self.assertEqual(lc.get_items()[0][0]['recheck_after'], '2026-11-13')
        lc.set_item('学业', '考完了，在收拾行李')             # 新的一句，没给 until：回国那天还是那天
        x = lc.get_items()[0][0]
        self.assertEqual((x['value'], x['recheck_after'], x['until']), ('考完了，在收拾行李', '2026-11-13', '2026-11-13'))
        lc.set_item('学业', until='2026-11-20')              # 真改了日子就听新的
        self.assertEqual(lc.get_items()[0][0]['recheck_after'], '2026-11-20')
        lc.set_item('学业', ttl_days=7)                      # 明说要早点再看，也听
        x = lc.get_items()[0][0]
        self.assertEqual((x['recheck_after'], x['until']), ((self.day + datetime.timedelta(days=6)).isoformat(), ''))

    def test_until_that_has_passed_falls_back_to_ttl(self):
        lc.set_item('学业', '期末在墨尔本', until='2026-11-13')
        self.go(13)                                          # 11/14，过了那天
        self.assertEqual([x['key'] for x in lc.get_items()[1]], ['学业'])
        lc.set_item('学业')                                  # 她说还在
        x = lc.get_items()[0][0]
        self.assertEqual((x['recheck_after'], x['until']), ((self.day + datetime.timedelta(days=29)).isoformat(), ''))

    def test_new_sentence_retires_the_old_one(self):
        lc.set_item('学期', '期末在墨尔本')
        self.go(12)
        lc.set_item('学期', '放假在家')
        fresh, stale = lc.get_items()
        self.assertEqual((fresh[0]['value'], fresh[0]['noted_at']), ('放假在家', self.day.isoformat()))
        with open(lc.ARCHIVE_PATH, encoding='utf-8') as f:
            arc = json.load(f)['items']
        self.assertEqual([(a['value'], a['archive_reason']) for a in arc], [('期末在墨尔本', 'replaced')])

    def test_until_is_a_date(self):
        lc.set_item('学期', '期末在墨尔本', until='2026-11-13')
        self.assertEqual(lc.get_items()[0][0]['recheck_after'], '2026-11-13')
        lc.set_item('学期', until='2020-01-01')              # 早于今天的不认：原来那天还没到，还按那天
        self.assertEqual(lc.get_items()[0][0]['recheck_after'], '2026-11-13')
        lc.set_item('出差', '在悉尼', until='2020-01-01')     # 新记的一条给了个过去的日子：照 ttl 算
        self.assertEqual(lc.get_items()[0][1]['recheck_after'], (D0 + datetime.timedelta(days=29)).isoformat())

    def test_key_only_needs_an_existing_line(self):
        with self.assertRaises(ValueError):
            lc.set_item('没记过的')

    def test_undo_brings_the_old_sentence_back(self):
        lc.set_item('学期', '期末在墨尔本')
        lc.set_item('学期', '放假在家', source_turn='t1', source_session='s1')
        self.assertEqual([a['value'] for a in self.archive()], ['期末在墨尔本'])
        lc.undo_turn_writes('t1', 's1')
        self.assertEqual(lc.get_items()[0][0]['value'], '期末在墨尔本')
        self.assertEqual(self.archive(), [])                 # 又活了，归档里不能还标着「后来换了说法」
        self.assertEqual(lc.search_archive('学期'), [])

    def test_new_line_that_looks_like_an_old_one_is_held_first(self):
        lc.set_item('学期', '期末在墨尔本')
        lc.set_item('吃药', '每天早晚各一片抗过敏的')
        lc.set_item('人在哪', '放假回国了')                   # 跟哪条都不像：直接记
        self.assertEqual(len(lc.get_items()[0]), 3)
        with self.assertRaises(lc.SimilarLine) as held:       # 键上有同一个字、句子里也碰上：先不写
            lc.set_item('学业', '期末考完了')
        self.assertEqual([s['key'] for s in held.exception.similar], ['学期'])
        note = str(held.exception)
        self.assertIn('「学业」先没记', note)
        self.assertIn('「学期」期末在墨尔本', note)
        self.assertIn('写回那个 key', note)
        self.assertEqual(len(lc.get_items()[0]), 3)          # 真的没写
        lc.set_item('学期', '期末考完了')                     # 是同一件：写回原来的 key，就是「情况变了」
        self.assertEqual([x['value'] for x in lc.get_items()[0] if x['key'] == '学期'], ['期末考完了'])
        self.assertEqual([a['archive_reason'] for a in self.archive()], ['replaced'])
        lc.set_item('学业', '下学期的课选好了', force_new=True)   # 确定是另一件：带 force_new 再来
        self.assertEqual(len(lc.get_items()[0]), 4)
        lc.set_item('学业')                                   # 续期不是新开，不拦
        lc.set_item('学业', '选了五门')                       # 同键换句也不拦

    def test_a_key_that_was_archived_recently_is_offered_again(self):
        lc.set_item('家人来访', '妈妈这阵子住她那儿', until='2026-11-05')
        self.go(6)
        lc.drop_item('家人来访')                               # 妈妈回去了
        self.go(20)
        with self.assertRaises(lc.SimilarLine) as held:       # 月底又来了：模型另起了个名字
            lc.set_item('妈妈来住', '妈妈又来了，住到月底')
        s = held.exception.similar[0]
        self.assertEqual((s['key'], s.get('archived')), ('家人来访', True))
        self.assertIn('以前记过「家人来访」', str(held.exception))
        self.assertIn('结束了', str(held.exception))
        lc.set_item('家人来访', '妈妈又来了，住到月底')          # 沿用旧 key，归档里才是一条线
        self.assertEqual([x['key'] for x in lc.get_items()[0]], ['家人来访'])
        self.go(100)                                          # 太久以前收起来的就不提了
        lc.drop_item('家人来访')
        self.go(200)
        lc.set_item('妈妈来住', '第三次来')
        self.assertEqual([x['key'] for x in lc.get_items()[0]], ['妈妈来住'])

    def test_broken_file_is_set_aside_not_overwritten(self):
        lc.set_item('学期', '期末在墨尔本')
        lc.set_item('吃药', '每天一片')
        with open(lc.PATH, 'w', encoding='utf-8') as f:
            f.write('{"items": [ 手改漏了个逗号')
        lc.set_item('新的', '一条')
        self.assertEqual([x['key'] for x in self.raw()['items']], ['新的'])
        aside = [n for n in os.listdir(self.tmp.name) if n.startswith('lc.json.broken-')]
        self.assertEqual(len(aside), 1)                       # 坏的那份还在，挪到一边了
        with open(os.path.join(self.tmp.name, aside[0]), encoding='utf-8') as f:
            self.assertIn('手改漏了个逗号', f.read())

    def test_day_starts_at_six(self):
        saved = lc.DAY_STARTS_AT
        try:
            lc.DAY_STARTS_AT, lc.today = 6, REAL_TODAY
            lc.now = lambda: datetime.datetime(2026, 11, 10, 1, 30)   # 凌晨一点半
            self.assertEqual(lc.today(), datetime.date(2026, 11, 9))  # 还算 9 号
            lc.set_item('学期', '期末在墨尔本')
            self.assertEqual(lc.get_items()[0][0]['noted_at'], '2026-11-09')
            lc.now = lambda: datetime.datetime(2026, 11, 10, 6, 0)
            self.assertEqual(lc.today(), datetime.date(2026, 11, 10))  # 六点整起算新的一天
            lc.DAY_STARTS_AT = 0                                       # 别人按零点算也行
            lc.now = lambda: datetime.datetime(2026, 11, 10, 1, 30)
            self.assertEqual(lc.today(), datetime.date(2026, 11, 10))
        finally:
            lc.DAY_STARTS_AT = saved



class Archive(Base):
    """归档不是忘了：翻得到，标成以前。"""

    def test_found_by_key_or_words_and_marked_as_past(self):
        lc.set_item('吃药', '每天早晚各一片抗过敏的', until='2026-11-10')
        self.go(5)
        lc.drop_item('吃药')
        lc.set_item('学期', '期末在墨尔本')
        lc.set_item('学期', '放假在家')
        hits = lc.search_archive('那阵子吃的什么药')
        self.assertEqual([h['key'] for h in hits], ['吃药'])
        blk = lc.render_past_states(hits)
        self.assertIn('不是现在', blk)
        self.assertIn('how="结束了"', blk)
        self.assertIn('from="%s"' % D0.isoformat(), blk)
        hits = lc.search_archive('学期')
        self.assertEqual([(h['value'], h['archive_reason']) for h in hits], [('期末在墨尔本', 'replaced')])
        self.assertIn('后来换了说法', lc.render_past_states(hits))

    def test_nothing_found_is_empty(self):
        lc.set_item('学期', '期末在墨尔本')
        lc.drop_item('学期')
        self.assertEqual(lc.search_archive('完全不相干'), [])
        lc.set_item('吃药', '每天一片')
        lc.drop_item('吃药')
        self.assertEqual([h['key'] for h in lc.search_archive('药')], ['吃药'])   # 问得很短也找得到
        self.assertEqual(lc.render_past_states([]), '')

    def test_one_key_comes_back_as_a_whole_line(self):
        for i, v in enumerate(['在上课', '在复习', '在考试', '考完等成绩', '放假了']):
            self.go(i)
            lc.set_item('学期', v)
        lc.set_item('吃药', '每天一片')
        lc.drop_item('吃药')
        hits = lc.search_archive('学期')
        self.assertEqual([h['value'] for h in hits], ['在上课', '在复习', '在考试', '考完等成绩'])   # 四段都在，按时间排
        self.assertEqual([h['key'] for h in lc.search_archive('药')], ['吃药'])
        hits = lc.search_archive('学期吃药', limit=1)                   # limit 数的是键：只给碰得最多的那一件
        self.assertEqual({h['key'] for h in hits}, {'学期'})
        self.assertEqual(len(hits), 4)

    def test_saying_it_ended_after_it_archived_itself(self):
        lc.set_item('家人来访', '妈妈这阵子住她那儿', ttl_days=3)
        for k in (3, 4, 5, 6):
            self.go(k)
            lc.render_breath_block()                                   # 三个她在的日子没动静，第四天自己归档
        self.assertEqual(lc.get_items(), ([], []))
        self.assertIn('不代表结束', lc.render_past_states(lc.search_archive('妈妈')))
        self.go(10)
        self.assertEqual(lc.drop_item('家人来访'), 1)                   # 她现在才说妈妈回去了
        blk = lc.render_past_states(lc.search_archive('妈妈'))
        self.assertIn('how="结束了"', blk)
        self.assertNotIn('不代表结束', blk)
        self.assertEqual(self.archive()[0]['closed_at'], self.day.isoformat())
        self.assertEqual(lc.drop_item('家人来访'), 0)                   # 再扔一次：没有可扔的了
        self.assertEqual(lc.drop_item('从没记过'), 0)


if __name__ == '__main__':
    unittest.main()
