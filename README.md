# Living Context

给长期记忆一层会过期的现在。`today_set` × `ctx_set`。

起点是一件记错的事：她妈妈来住了几个礼拜，她说「我妈要回家了」，模型手里没有地方放一个正在进行的事，只能天天问，问到后来把将来时记成了完成时，人还在，它已经当她走了。再后来她妈妈真的回去了，那条「妈妈在这儿」又挂着不肯退场。一个「现在」，两头都没接住。完整的来龙去脉在 [docs/Living_Context.pdf](docs/Living_Context.pdf)，这里只讲怎么接。

它很小：两个写入，两个读取，一个退场，一份 JSON。它不自己站着——挂在 [Ombre-Brain](https://github.com/P0luz/Ombre-Brain) 的 `breath` 里，breath 睁眼看长期记忆的时候，顺手把「现在」也带出来。

## 三分界

写入之前先问一句：这件事，明天还成立吗？一个月后呢？

| | 去哪 | 例子 |
|---|---|---|
| 痕迹：那天确实发生了，永远为真 | `remember`（长期记忆，不在本仓库） | 「她妈妈来看过她」 |
| 当天：只对今天有效，天亮清 | `today_set` | 「今天中午吃炖牛尾骨」 |
| 状态：跨天有效，但会过期，将来要更新 | `ctx_set` | 「她妈这段时间在她这儿」 |

同一件事可以同时是三种。分开写，各自退场——别让一条会过期的状态，混进不会衰减的档案里。

判据只有一个：不是这条值不值得记，而是等下再问一遍她会不会烦。会烦的，就写。

## 仓库里有什么

```
living_context.py   持续背景：set / drop / get(fresh, stale) / 到期降级 / 七天缓冲 / 归档 + 墓碑 / 增量注入 / 按轮撤销
today_state.py      今日状态：set / get / 天亮懒清理 / 注入块
hint_snapshots.py   快照回执：长会话里「同一份内容只挂一次，内容变了或被 compact 掉了再挂」
tools.json          五个 MCP 工具的定义，description 是模型写给自己的话
docs/prompt.md      五段工具描述的全文 + 一段配套 prompt
docs/Living_Context.pdf
```

三个 `.py` 只用标准库，没有依赖。路径、时区各留了一个替换口（`PATH` / `today` / `now`），接进自己的系统时换成自己的。

## 生命周期

```
today    写入 today_set ──→ 当天随 breath 注入 ──→ 天亮自动清
ctx      写入 ctx_set ──→ fresh（直接用）──→ stale（到了复核日：先问一句再当真）──→ 七天缓冲 ──→ 更新 / ctx_drop / 自动归档，留墓碑
```

stale 不是无效，是「过去记过，先确认再当真」。退场的事实留一块墓碑：下一次压缩摘要时，墓碑告诉摘要「这条已不成立，已移除」，旧引用不会再被捡回来。

## 接法

### 1. 挂进 breath

breath 是模型在新窗口睁眼的那一下。把 `render_breath_block()` 的输出放进 breath 的返回里；空的时候返回空串，不占位。

```python
import living_context as lc

block = lc.render_breath_block()
if block:
    lines.append(block)   # <living_context> … <now>/<expired> … </living_context>
```

`today_get` / `ctx_get` 作为独立工具另给一份（见 `tools.json`），模型想确认「吃了吗」「还在吗」之前自己去看。

### 2. API 路线：隐藏标记 + 增量注入 + 并进摘要

适用于自家的对话引擎——有 system 前缀缓存、每 N 轮压缩一次摘要的那种。

**读：只有刚改过、还没并进摘要的那几条才挂出来。** 背景平时躺在摘要里，摘要在缓存断点之前，全程吃缓存；变更挂在最后一条 user 消息头部，不动 system 前缀。大多数时候这里是空的。

```python
import today_state as ts, living_context as lc

head = lc.render_pending_block(
    'api',
    '再有变化写 [CTX_SET:键|新值]，某条不成立了写 [CTX_DROP:键]；当天的事仍走 [TODAY_SET:]。')
head += '\n' + ts.render_block('marker')          # 今日状态每轮都给，它本来就只有几行
msgs[-1]['content'] = head + '\n' + msgs[-1]['content']
```

**写：模型在回复末尾输出隐藏标记，后端解析、写库、剥掉，她看不到。**

```python
import re
for k, v in re.findall(r'\[TODAY_SET:([^|\]]+)\|([^\]]+)\]', reply): ts.set_item(k, v)
for k, v in re.findall(r'\[CTX_SET:([^|\]]+)\|([^\]]+)\]', reply):   lc.set_item(k, v)
for k in re.findall(r'\[CTX_DROP:([^\]]+)\]', reply):                lc.drop_item(k)
reply = re.sub(r'\[(TODAY_SET|CTX_SET|CTX_DROP):[^\]]*\]', '', reply).strip()
```

**压缩时：把待同步的变更一并交给写摘要的模型；新摘要和裁剪后的历史都落盘之后，再标已同步。** 标早了，摘要写失败那一轮就丢了。

```python
pending = lc.pending_items('api')      # 交给摘要 prompt
# … 摘要写成、历史裁剪、两者都保存成功 …
lc.mark_synced('api')
```

fresh → stale 的翻转也算一次变更：到了复核日，那条会自己再挂一次，带着「是否仍成立待确认」。

### 3. SDK 路线：真实工具 + 快照回执

适用于 Claude Agent SDK 这类长会话——不换窗、不 breath、会话自己做 compact。

**写走真实工具**（`tools.json` 里那五个），不用标记。`ctx_set` 时把本轮的 turn / session 一起传进去，重 roll 的时候能按轮撤销：

```python
lc.set_item(key, value, ttl_days, source_turn=turn_id, source_session=session_id)
# 用户重 roll 或手动改了那一轮：
lc.undo_turn_writes(turn_id, session_id)   # 只撤销仍是那个版本的条目，被后来覆盖过的不动
```

**读走快照回执：新增的才注入，留在上下文里的不反复注入。** 每轮把完整快照交给 `hint_snapshots.prepare`，它给内容算一个 revision；这个 session 收过同一 revision、而且那段文字还在上下文里（没被 compact 掉），就什么都不挂；否则挂一次。发出去以后 `commit` 记回执。

```python
import hint_snapshots as hs

blk = hs.prepare(session_id, 'ctx', lc.render_snapshot())
if blk:
    user_text += '\n\n<system_hint>\n' + blk + '\n</system_hint>'
blk2 = hs.prepare(session_id, 'today', ts.render_block('tool'))
if blk2:
    user_text += '\n\n<system_hint>\n' + blk2 + '\n</system_hint>'
# … 发送 …
hs.commit(session_id, user_text)
```

`hint_snapshots` 默认去 Claude Agent SDK 的 transcript（`~/.claude/projects/<项目>/<session>.jsonl`）里确认 marker 还在不在、有没有过 compact 边界；把 `HINT_TRANSCRIPTS_DIR` 指过去即可。查不了 transcript 的环境退化成「同一 revision 只挂一次」。

### 4. 跨入口、跨 session

一份 `today_state.json`、一份 `living_context.json`，所有入口共用：官方 app 经 MCP 工具读写，自家 API 直读直写，SDK 长会话同上。换一个入口，不用重新说一遍今天睡了多久、妈妈这阵子在不在。

每条读取通道各自记一个「已收进上下文」标记（`CHANNELS = ('api', 'sdk')`），互不打扰：谁先读谁清的话，另一条通道永远看不到变更。有几条通道就列几条。

## 规矩

- 它们是背景，用来避免说错话，不是拿来找话题的。不主动提起，不拿它开场。读过之后就着看到的说话，不汇报你看过。
- stale 不等于无效。先问一句「还在吗」，再当真。
- 事情结束了就 `ctx_drop`。一条挂着的旧状态，比没有更糟。
- 它只装有明确时限的事。学业、住处、长期行程这类会变却没有期限的事实，靠它活着只会一直续命，该放在别处。
- 它不替你判断。到期它只把这条变灰、戳你一下。问不问，是你的事。

## 工具描述

五个工具的 `description` 用第一人称写，是模型写给自己的话，不是说明书。全文和一段配套 prompt 在 [docs/prompt.md](docs/prompt.md)；文里的「她」按你们的称呼改。

## 参考与致谢

- 架构参考：[Ombre-Brain](https://github.com/P0luz/Ombre-Brain) — P0luz；[Haven-Ombre](https://github.com/Yinglianchun/Haven-Ombre) — Yinglianchun
- 概念参考：Kli × Ombre Brain 白皮书 3.3 节 — Liliyet（Kli）
- 相关实践：Scarlett 记忆系统（架构图，未发布仓库）— saekisui；[Nocturne-Memory-Core](https://github.com/Pyruslili/Nocturne-Memory-Core) — Pyruslili；[Serein](https://github.com/Yinglianchun/Serein) — Yinglianchun；[InternalBeyond](https://github.com/Sui-IB/InternalBeyond) — Sui

breath 这个入口来自 Ombre-Brain；「持续环境」的说法来自 Kli 白皮书 3.3 节；其余机制是这里长出来的。

---

## 许可证

[MIT](LICENSE)。仓库里的三个 `.py` 和全部文字都是这里写的，不含 Ombre-Brain 的代码；接进 OB 的 breath 时，OB 本身按它自己的协议走（也是 MIT，见它的 [NOTICE](https://github.com/P0luz/Ombre-Brain/blob/main/NOTICE.md)）。

---

Rumi · Rune · Serein · 2026 · [MIT License](LICENSE)

记住过去，也要知道什么仍然成立。
