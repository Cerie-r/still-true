# -*- coding: utf-8 -*-
"""rhythm 的几条底线：知道她平常什么样，就不拿钟当证据。只用标准库：python3 -m unittest discover -s tests"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rhythm  # noqa: E402


def _days(n, first='13:30', last='01:40', start=datetime.date(2026, 10, 1)):
    """造 n 天记录：每天 first 出现、last（次日凌晨）最后一条，中间再来一条。"""
    out = []
    for i in range(n):
        d = start + datetime.timedelta(days=i)
        out.append('%s %s' % (d.isoformat(), first))
        out.append('%s 23:10' % d.isoformat())
        out.append('%s %s' % ((d + datetime.timedelta(days=1)).isoformat(), last))
    return out


class Rhythm(unittest.TestCase):
    def setUp(self):
        self.saved = rhythm.DAY_STARTS_AT
        rhythm.DAY_STARTS_AT = 6
        self.at = datetime.datetime(2026, 11, 9, 14, 0)

    def tearDown(self):
        rhythm.DAY_STARTS_AT = self.saved

    def test_night_messages_belong_to_the_day_before(self):
        p = rhythm.profile(_days(20), at=self.at)
        self.assertEqual((p['confidence'], p['n_days']), ('ok', 20))
        self.assertEqual((p['typical_first'], p['typical_last']), ('13:30', '01:40'))   # 最晚不是 00:0x
        self.assertEqual(p['typical_last_min'], 25 * 60 + 40)

    def test_too_few_days_says_so(self):
        p = rhythm.profile(_days(5), at=self.at)
        self.assertEqual(p['confidence'], 'low')
        self.assertEqual(rhythm.render_line(p), '')                                      # 样本不足一个字都不挂
        self.assertEqual(rhythm.silence_verdict(p, 300, self.at), 'unknown')
        p = rhythm.profile(_days(9), at=self.at)
        self.assertEqual(p['confidence'], 'thin')

    def test_old_days_fall_out_of_the_window(self):
        p = rhythm.profile(_days(20, start=datetime.date(2026, 8, 1)), at=self.at)     # 两个多月前
        self.assertEqual(p['confidence'], 'low')

    def test_silence_is_read_against_her_own_rhythm(self):
        p = rhythm.profile(_days(20), at=self.at)
        day = datetime.date(2026, 11, 9)
        at = lambda h, m=30: datetime.datetime.combine(day, datetime.time(h, m))
        self.assertEqual(rhythm.silence_verdict(p, 60, at(19)), 'normal')          # 七点半安静一小时：她下午刚过半
        self.assertEqual(rhythm.silence_verdict(p, 300, at(19)), 'unusual')        # 常态时段里安静五小时才叫不一样
        self.assertEqual(rhythm.silence_verdict(p, 90, at(23)), 'normal')          # 十一点半安静一个半钟头：她的夜还长
        self.assertEqual(rhythm.silence_verdict(p, 30, at(3)), 'after_sleep')      # 凌晨三点：过了她最晚的点
        self.assertEqual(rhythm.silence_verdict(p, 600, at(8)), 'before_wake')     # 早上八点半是还没起，不是熬得晚

    def test_line_says_what_she_is_like_not_what_to_do(self):
        line = rhythm.render_line(rhythm.profile(_days(20), at=self.at))
        self.assertIn('常态出现约 13:30', line)
        self.assertIn('常态最晚约 01:40', line)
        self.assertIn('平常的样子', line)
        for words in ('不要', '别', '催', '该睡'):
            self.assertNotIn(words, line)



if __name__ == '__main__':
    unittest.main()
