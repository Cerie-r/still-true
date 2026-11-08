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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (lc.PATH, lc.ARCHIVE_PATH, lc.today, lc.GRACE_DAYS, lc.CHANNELS)
        lc.PATH = os.path.join(self.tmp.name, 'lc.json')
        lc.ARCHIVE_PATH = os.path.join(self.tmp.name, 'lc_archive.json')
        self.day = D0
        lc.today = lambda: self.day

    def tearDown(self):
        lc.PATH, lc.ARCHIVE_PATH, lc.today, lc.GRACE_DAYS, lc.CHANNELS = self.saved
        self.tmp.cleanup()

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


if __name__ == '__main__':
    unittest.main()
