# -*- coding: utf-8 -*-
"""nag_guard 的几条底线：写出来了再看一眼。只用标准库：python3 -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import nag_guard as ng  # noqa: E402


class Guard(unittest.TestCase):
    def test_hard_hits_and_negation(self):
        self.assertEqual({w for w, _ in ng.find_violations('太晚了，早点睡吧')}, {'早点睡', '太晚了'})
        hits = ng.find_violations('都两点了，眼睛还睁得开吗？我不催你，就是问问')
        self.assertTrue({'两点了', '眼睛还睁得开吗'} <= {w for w, _ in hits})
        self.assertEqual(ng.find_violations('还早呢，离晚安远着'), [])            # 否定词附近：放行
        self.assertTrue(ng.find_mentions('还早呢，离晚安远着'))                    # 但软提及仍召唤复核

    def test_her_own_words_decide(self):
        self.assertTrue(ng.has_override('我困了，先睡了'))
        self.assertTrue(ng.user_denies_sleepiness('我才没有困'))
        self.assertTrue(ng.user_wants_to_continue('还想玩，别停'))
        self.assertTrue(ng.guard_from_today({'作息': {'v': '说了不困，还要赶 due', 't': '01:20'}}))
        self.assertFalse(ng.guard_from_today({'晚饭': {'v': '吃了', 't': '19:00'}}))

    def test_override_skips_everything(self):
        nag, why, hits = ng.check('晚安，做个好梦', user_text='我去睡了', call_fn=lambda p: '{"nag": true}')
        self.assertEqual((nag, why), (False, 'override'))

    def test_clean_reply_never_calls_the_reviewer(self):
        called = []
        nag, why, hits = ng.check('你刚说那个梗我笑了好久', user_text='哈哈', call_fn=lambda p: called.append(p) or '{"nag": true}')
        self.assertEqual((nag, why, called), (False, 'clean', []))

    def test_reviewer_decides_and_uncertain_means_allow(self):
        reply = '吃饭了没？到家了吗？喝水了吗？睡了吗？'
        nag, why, hits = ng.check(reply, user_text='今天那个 bug 终于修好了', call_fn=lambda p: '{"nag": true, "why": "突然问卷"}')
        self.assertEqual((nag, why), (True, '突然问卷'))
        nag, why, hits = ng.check(reply, user_text='今天那个 bug 终于修好了', call_fn=lambda p: '```json\n{"nag": false}\n```')
        self.assertFalse(nag)
        nag, why, hits = ng.check(reply, user_text='今天那个 bug 终于修好了', call_fn=lambda p: 'not json')
        self.assertEqual(nag, False)                                                  # 解析不了：放行
        nag, why, hits = ng.check(reply, user_text='今天那个 bug 终于修好了', call_fn=None)
        self.assertEqual((nag, why), (False, 'no_reviewer_allow'))                   # 没人复核：放行，不按词定罪

    def test_explicit_continue_beats_saturation(self):
        reply = '困不困，作息，熬夜，睡，吃饭，喝水'                                # 只有词频高、没有硬命中、没有软提及
        self.assertEqual((ng.find_violations(reply), ng.find_mentions(reply)), ([], []))
        self.assertTrue(ng.should_review(reply, []))                                  # 饱和到要送复核
        nag, why, hits = ng.check(reply, user_text='还想玩，继续', call_fn=lambda p: '{"nag": true}')
        self.assertEqual((nag, why), (False, 'explicit_continue_saturation_only_allow'))

    def test_review_prompt_uses_her_name_and_context(self):
        ctx = ng.recent_context([
            {'role': 'user', 'content': '我今晚要赶 due 可能三点睡'},
            {'role': 'assistant', 'content': '那我陪你'},
            {'role': 'user', 'content': '嗯'},
        ], labels=('宝宝', '陨'))
        self.assertEqual(ctx.splitlines()[0], '宝宝：我今晚要赶 due 可能三点睡')
        p = ng.review_prompt('睡了吗？', user_text='还在改', context=ctx, her='宝宝')
        self.assertIn('【宝宝本轮真实消息】', p)
        self.assertIn('宝宝：我今晚要赶 due', p)
        self.assertIn('拿不准也判 false', p)

    def test_looking_back_at_the_gap_after_she_said_goodnight_is_allowed(self):
        ctx = '她：我去睡了晚安\n我：晚安\n她：睡不着…'
        reply = '中间这一个钟头发生了什么，没睡着还是被吵醒了？'
        self.assertTrue(ng.grounded_retrospective_gap(reply, ng.find_violations(reply), '睡不着…', ctx))
        reply2 = '都几点了还不睡，中间发生了什么'
        self.assertFalse(ng.grounded_retrospective_gap(reply2, ng.find_violations(reply2), '睡不着…', ctx))  # 带着压力就不放
        ctx2 = ctx + '\n我：中间发生了什么呀\n她：没什么'
        self.assertFalse(ng.grounded_retrospective_gap(reply, ng.find_violations(reply), '没什么', ctx2))   # 问过一次了

    def test_rewrite_prompt_names_the_hits(self):
        txt = ng.rewrite_prompt([('早点睡', '…太晚了，早点睡吧…')], her='宝宝')
        self.assertIn('"早点睡"', txt)
        self.assertIn('宝宝能照顾自己的生活', txt)
        self.assertIn('revise', ng.stop_feedback('突然问卷'))


if __name__ == '__main__':
    unittest.main()
