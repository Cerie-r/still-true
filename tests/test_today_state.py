# -*- coding: utf-8 -*-
"""today_state / hint_snapshots 的几条底线。只用标准库：python3 -m unittest discover -s tests"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import today_state as ts  # noqa: E402
import hint_snapshots as hs  # noqa: E402


class Today(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ts.PATH, ts.now, ts.MAX_ITEMS, ts.DAY_STARTS_AT)
        ts.PATH = os.path.join(self.tmp.name, 'today.json')
        ts.DAY_STARTS_AT = 6
        self.clock = datetime.datetime(2026, 11, 9, 22, 0)
        ts.now = lambda: self.clock

    def tearDown(self):
        ts.PATH, ts.now, ts.MAX_ITEMS, ts.DAY_STARTS_AT = self.saved
        self.tmp.cleanup()

    def test_midnight_does_not_clear_but_six_does(self):
        ts.set_item('晚饭', '十点才吃的，番茄炖牛骨')
        self.clock = datetime.datetime(2026, 11, 10, 1, 0)             # 一点钟她还醒着
        self.assertEqual(ts.get_all()['晚饭']['v'], '十点才吃的，番茄炖牛骨')  # 晚饭还在，不会再问一遍
        self.clock = datetime.datetime(2026, 11, 10, 6, 0)
        self.assertEqual(ts.get_all(), {})                              # 六点起才是新的一天
        ts.DAY_STARTS_AT = 0                                            # 别人按零点算
        self.clock = datetime.datetime(2026, 11, 9, 23, 0)
        ts.set_item('晚饭', '吃了')
        self.clock = datetime.datetime(2026, 11, 10, 0, 30)
        self.assertEqual(ts.get_all(), {})

    def test_overwriting_moves_it_to_the_end(self):
        ts.MAX_ITEMS = 3
        ts.set_item('晚饭', '还没吃')
        ts.set_item('睡觉', '昨晚两点')
        ts.set_item('出门', '去了超市')
        ts.set_item('晚饭', '吃了番茄炖牛骨')                            # 刚更新的
        ts.set_item('身体', '有点头疼')                                  # 满了，淘汰一条
        self.assertEqual(list(ts.get_all()), ['出门', '晚饭', '身体'])   # 踢掉的是真最旧的「睡觉」，不是晚饭

    def test_broken_file_is_set_aside(self):
        ts.set_item('晚饭', '吃了')
        with open(ts.PATH, 'w', encoding='utf-8') as f:
            f.write('{"date": "2026-11-09", "items": { 坏了')
        ts.set_item('睡觉', '两点')
        with open(ts.PATH, encoding='utf-8') as f:
            self.assertEqual(list(json.load(f)['items']), ['睡觉'])
        self.assertTrue(any(n.startswith('today.json.broken-') for n in os.listdir(self.tmp.name)))

    def test_lock_file_exists(self):
        ts.set_item('晚饭', '吃了')
        self.assertTrue(os.path.exists(ts.PATH + '.lock'))              # 有文件锁，几个进程一起写也不互相盖


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = hs.DB
        hs.DB = os.path.join(self.tmp.name, 'h.db')

    def tearDown(self):
        hs.DB = self.saved
        self.tmp.cleanup()

    def test_odd_session_id_still_hangs_only_once(self):
        for sid in ('abc-123', 'sess_1', 'a.b/c'):
            first = hs.prepare(sid, 'ctx', 'body')
            self.assertTrue(first)
            hs.commit(sid, first)
            self.assertEqual(hs.prepare(sid, 'ctx', 'body'), '')        # 同一 revision 只挂一次
            self.assertTrue(hs.prepare(sid, 'ctx', 'body changed'))     # 内容变了再挂


if __name__ == '__main__':
    unittest.main()
