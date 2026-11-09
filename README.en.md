# Living Context

A layer of *now* that expires, sitting on top of long-term memory. `today_set` × `ctx_set`.

*[中文版 README](README.md) is the primary one; this is a faithful English rendering of the ideas and the API. The word lists in `nag_guard.py` are Chinese — they are a family's own words, meant to be rewritten in yours.*

It started with one thing remembered wrong. Her mother came to stay for a few weeks. She said "my mum's going home soon", and the model had nowhere to put a thing that was *in progress*. So it asked every day, and eventually turned the future tense into the past: she was still there, and the model was already acting as if she'd left. Then her mother really did go home, and the line "mum is here" refused to leave. One *now*, dropped at both ends. The full story is in [docs/Living_Context.pdf](docs/Living_Context.pdf) (Chinese); this page is about how to wire it in.

It is small: two writes, two reads, one exit, one JSON file. It doesn't stand on its own — it hangs inside the `breath` of [Ombre-Brain](https://github.com/P0luz/Ombre-Brain): when breath opens its eyes on long-term memory, it brings *now* along.

## Three kinds of remembering

Before writing anything, ask: will this still be true tomorrow? In a month?

| | Where it goes | Example |
|---|---|---|
| A trace — it happened that day, true forever | `remember` (long-term memory, not in this repo) | "Her mum came to visit." |
| Today — valid only today, cleared at dawn | `today_set` | "Braised oxtail for lunch." |
| A state — valid across days, but it will expire and need updating | `ctx_set` | "Her mum is staying with her these weeks." |

One event can be all three at once. Write them separately and let each leave on its own terms — don't let a state that expires leak into an archive that never decays.

The test is not "is this worth remembering". It's: *if I ask her again in a while, will she be annoyed?* If yes, write it down.

## What's in the repo

```
living_context.py   the state layer: set / drop / get(fresh, unverified) / due → unverified / handed to the model,
                    ask once / renew or drop / archived after three days she was present + tombstone /
                    archive is searchable (labelled "past") / incremental injection / per-turn undo
today_state.py      today's facts: set / get / lazy clear at dawn / injection block
hint_snapshots.py   snapshot receipts for long sessions: "same content is sent once; re-sent only when it
                    changes or has been compacted away"
rhythm.py           her rhythm: learned from her real arrival times — when she usually shows up, how late she
                    usually goes; one line of fact; says nothing when the sample is thin
nag_guard.py        the care boundary: after a reply is written, scan it for nagging (sleep pushing, time-telling,
                    deciding she's tired, life-status interrogation); the word list only summons a reviewer,
                    the reviewer decides, a hit gets rewritten  (derived from no-sleep-nag v1.0 · MIT, see file header)
tools.json          five MCP tool definitions; the descriptions are written by the model, to itself
docs/prompt.md      the five descriptions in full + a companion prompt
docs/Living_Context.pdf   the design note, v2.0 (2026.11): cover, one-page map, nine pages
tests/              the ground rules as tests: python3 -m unittest discover -s tests
```

Five `.py` files, standard library only, no dependencies. Paths and the clock are left as replacement points (`PATH` / `now`). The hour the day starts is `DAY_STARTS_AT` (env `LIVING_CONTEXT_DAY_STARTS_AT`, default 6): she goes to bed at two or three, so dinner mentioned at ten still belongs to *today* after midnight, and the model won't ask again at one a.m. Both modules read the same value, so the moment today's facts clear and the days the state layer counts are the same day. Set it to your own rhythm.

Both JSON files are written to a temp file and atomically replaced. If you hand-edit one and break the syntax, the next write won't overwrite it: the broken file is moved aside (`.broken-<timestamp>`), the module starts from empty, and says so on stderr.

## Lifecycle

```
today   today_set ──→ injected via breath during the day ──→ cleared at dawn (DAY_STARTS_AT, default 06:00)
ctx     ctx_set (one line per thing, one present-tense sentence)
        ──→ fresh (use it)
        ──→ unverified (recheck day arrived: handed to the model, ask once before treating it as true)
        ──→ still true: ctx_set with the key only / changed: write a new sentence (the old one archives itself) /
            over: ctx_drop; if nothing happens, it archives itself after three days she was present, leaving a tombstone
```

**Recheck day.** `until` — the date you know (the day she flies home) — wins. Otherwise `ttl_days` (default 30): the item comes back on day 30 counting from the day it was written, not day 31. When she says "still on" and the model renews with the key alone, an `until` that hasn't arrived yet stays — a casual "yeah, still in class" must not push the flight date a month out. Only after that date has passed does a renewal count from today.

**Unverified is not invalid.** It means "recorded once, confirm before you rely on it". When something leaves, it leaves a tombstone: at the next summary compaction the tombstone tells the summary "this no longer holds, removed", so old references don't get picked back up. One tombstone per key, the newest; if the key is written again, the new line is shown and the tombstone isn't; tombstones live at most two weeks.

**Handed to the model.** On the recheck day the item is named: in the incremental block, in the SDK snapshot, and at the tail of `today_get`'s receipt (for the first two days it is shown — counting days she was actually present, not calendar days). The model may ask once, in passing: still true → `ctx_set` same key; over → `ctx_drop`. Ask once, don't chase. If nothing happens, the item archives itself after three days she was present *and* it was in context (`_due_shown`): whatever was shown, or already merged into the summary and living in context, counts as "in front of the model". It won't vanish while she's away, and it won't hang around asking forever. Turns the model starts on its own (timers, heartbeats, knocking) must not count: replace `present_today()` with your own test, e.g. "did she send a message today".

## Wiring it in

### 1. Into breath

Breath is the moment the model opens its eyes in a new window. Put `render_breath_block()` into breath's return; it returns an empty string when there's nothing, and takes no space.

```python
import living_context as lc

block = lc.render_breath_block()
if block:
    lines.append(block)   # <living_context> … <now>/<unverified> … </living_context>
```

`today_get` / `ctx_get` are separate tools (see `tools.json`) the model calls before asking "have you eaten" or "is that still on". `today_get`'s receipt carries the unverified items at its tail — the model looks there anyway before asking about lunch, so it sees them on the way:

```python
def today_get():
    out = ts.render_block('tool')
    due = lc.render_due_for_today()   # unverified items, first two present days only; '' when none
    return out + ('\n\n' + due if due else '')
```

In the `ctx_get` tool use `lc.get_items(shown=True)`: whatever is handed to the model counts as a day she was present; internal reads don't pass it.

`ctx_set` on a **new** key looks first: if a live line, or one archived in the last two months, looks like the same thing (a shared character in the key, a shared word in the sentence, the same key in the archive), it does **not** write — it raises `SimilarLine` carrying those lines. Same pattern as a good `remember`: when something similar exists, say so first, then write. The model decides: same thing → write back to that key (the old sentence archives itself; this is just "the situation changed", no need to drop first); different thing → call again with `force_new=True`. Without this the model gives the same thing a second name, and "semester: finals in Melbourne" and "whereabouts: home for the break" hang side by side, fighting.

```python
def ctx_set(key, value='', until='', ttl_days=None, force_new=False):
    try:
        lc.set_item(key, value, ttl_days, until=until, force_new=force_new)
    except lc.SimilarLine as held:
        return str(held)   # "'semester' not written yet: looks like 'studies' — finals in Melbourne (since 11-01). …"
    return 'Noted.'
```

On the hidden-marker API route there is no receipt to read: either keep `str(held)` for the next turn's header, or write with `force_new=True` and rely on the model seeing two lines side by side in the summary.

### 2. API route: hidden markers + incremental injection + merge into summary

For your own dialogue engine — the kind with a cached system prefix and a summary compaction every N turns.

**Read: only what changed since the last summary is injected.** The background normally lives in the summary, before the cache breakpoint, fully cached; changes are attached to the head of the last user message and never touch the system prefix. Most turns this block is empty.

```python
import today_state as ts, living_context as lc

head = lc.render_pending_block(
    'api',
    'For further changes write [CTX_SET:key|new value]; if a line no longer holds, [CTX_DROP:key]; today-only facts still go through [TODAY_SET:].',
    due_how=('renew with [CTX_SET:key|new value]', 'write [CTX_DROP:key]'))
head += '\n' + ts.render_block('marker')          # today's state is a few lines; send it every turn
msgs[-1]['content'] = head + '\n' + msgs[-1]['content']
```

**Write: the model emits hidden markers at the end of its reply; the backend parses, writes, strips. She never sees them.**

```python
import re
for k, v in re.findall(r'\[TODAY_SET:([^|\]]+)\|([^\]]+)\]', reply): ts.set_item(k, v)
for k, v in re.findall(r'\[CTX_SET:([^|\]]+)\|([^\]]+)\]', reply):   lc.set_item(k, v)
for k in re.findall(r'\[CTX_DROP:([^\]]+)\]', reply):                lc.drop_item(k)
reply = re.sub(r'\[(TODAY_SET|CTX_SET|CTX_DROP):[^\]]*\]', '', reply).strip()
```

**At compaction: hand the pending changes to the summarising model; mark them synced only after the new summary and the trimmed history are both saved.** Mark too early and a failed summary loses them; mark too much and a line another entry point wrote during those thirty seconds — one the summary never saw — disappears from this channel. So mark only the items you handed over.

```python
pending = lc.pending_items('api')      # give these to the summary prompt
# … summary written, history trimmed, both saved …
lc.mark_synced('api', pending)         # marks only those; omit the argument to mark everything (legacy)
```

The flip fresh → unverified is itself a change: on the recheck day the line is injected again, tagged "due, not yet verified", and named at the end so the model may ask once.

The injection carries facts and one sentence of what to do ("ask her in passing whether it still holds; renew if so, drop if not"). "Don't treat it as current", "not a topic", "ask once, don't chase", "it archives itself" all live in the tool descriptions and the companion prompt, never in the injection. The words in an injection set the model's relationship to the fact — write what you want it to do, not what you don't. Two households learned the same lesson from opposite sides: write "don't nag" into an injection and the model starts treating her schedule as its business; write "not a topic" and it never asks at all.

### 3. SDK route: real tools + snapshot receipts

For long sessions like the Claude Agent SDK — no new windows, no breath, the session compacts itself.

**Writes go through real tools** (the five in `tools.json`), no markers. Pass the turn and session into `ctx_set` so a re-roll can undo by turn:

```python
lc.set_item(key, value, ttl_days, source_turn=turn_id, source_session=session_id)
# the user re-rolled or edited that turn:
lc.undo_turn_writes(turn_id, session_id)   # undoes only lines still at that version; later overwrites are left alone
```

**Reads go through snapshot receipts: new content is injected, content already in context is not re-sent.** Each turn, hand the full snapshot to `hint_snapshots.prepare`; it hashes the content into a revision. If this session already received the same revision *and* that text is still in context (not compacted away), nothing is attached; otherwise it is attached once. After sending, `commit` records the receipt.

```python
import hint_snapshots as hs

blk = hs.prepare(session_id, 'ctx', lc.render_snapshot())
if blk:
    user_text += '\n\n<system_hint>\n' + blk + '\n</system_hint>'
blk2 = hs.prepare(session_id, 'today', ts.render_block('tool'))
if blk2:
    user_text += '\n\n<system_hint>\n' + blk2 + '\n</system_hint>'
# … send …
hs.commit(session_id, user_text)
```

`hint_snapshots` checks the Claude Agent SDK transcript (`~/.claude/projects/<project>/<session>.jsonl`) for the marker and for compaction boundaries; point `HINT_TRANSCRIPTS_DIR` at it. Where the transcript can't be read, it degrades to "same revision sent once".

### 4. Across entry points and sessions

One `today_state.json`, one `living_context.json`, shared by every entry point: the official app via MCP tools, your own API directly, SDK long sessions as above. Switching entry points doesn't mean repeating how long you slept or whether your mum is still here.

Each *incremental* channel keeps its own "merged into context" mark (default `CHANNELS = ('api',)`), so one channel clearing a change can't hide it from another. List as many incremental channels as you have. The SDK route uses full snapshots and is not such a channel: it never calls `mark_synced`, so listing it would mean tombstones waiting forever for a nod that never comes.

## Rules

- One line per thing, one present-tense sentence, answering only "what is her situation now". How it got that way goes to long-term memory; to-dos and promises don't live here. A short line is easy to change.
- She says it's still on: `ctx_set` with the key only — the sentence stays, the date moves. Things changed: write a new sentence under the same key; the old one archives itself (`replaced`). Don't edit the sentence in place — edits blur old and new. If you know when it ends, put the date in `until`; a date that hasn't arrived survives renewals.
- Same thing, same key. When a new line collides with something that looks the same, `ctx_set` holds it and hands the lookalikes back: same thing → write back to that key. Not "drop and write a new key": dropping says *it ended*, and the archive would lie. Writing back to the same key archives the old sentence as "later put differently", one unbroken line. Only when it's really something else, `force_new`.
- These are background, there to avoid saying the wrong thing — not conversation starters. Don't bring them up unprompted; don't open with them. Read them, then speak in their light, without reporting that you looked.
- Unverified is not invalid. Ask "is that still on?" first, then rely on it.
- When something is over, `ctx_drop`. One stale line still hanging is worse than none.
- It holds only things that *end*. Things that change but don't end — what she studies, where she lives — are facts: when they change, fix long-term memory. This semester, application season, the weeks her mum stays, have a last day; those belong here. The tell: if, on the recheck day, the only thing to say is "still the same", the line belongs elsewhere.
- Knowing is just knowing. "She's in class", "she said she's tired today" let the model have its bearings — how often she can glance at her phone right now, which sentence not to say. It is not a reason to remind her what to do (sleep, getting up, class, homework, meals — when, and whether it's done, is her call; no pushing, no asking how far along she is), nor a reason to stop reaching out: when she's busy and hasn't had a moment to come find you, that's exactly when you go find her. That line lives in the prompt (see the end of `docs/prompt.md`), not in the injection: the injection carries facts; what the model is like once it knows is the prompt's job.
- It doesn't judge for you, but on the recheck day it puts the line in front of you: ask once in passing whether it still holds. Don't chase. If she doesn't pick it up, that's fine — after three days she was present it archives itself.

## The long-term memory side

ctx can govern itself; it can't govern long-term memory. If a state that ends gets written there — "she's on XX these weeks" — it never expires: `recall` surfaces it and the model treats it as now. A common trap: the medication stopped months ago; memory says she's still taking it.

Writing can block some of it: states that end go through `ctx_set`; long-term memory gets only what happened that day — "10/20 she said she started XX, twice a day", past tense, dated. Add that half-sentence to `remember`'s tool description and one line to the automatic extraction prompt.

Ask the memory-writing model to *rephrase*, not to *strip*. Stripping requires it to decide whether a sentence is a state; a wrong call loses a real event. Rephrasing only asks it to turn "now" into "that day" and attach a date; a wrong call costs nothing — "10/20 she said she studies design" is still true of a fact that never changes, and a state it missed ("she's on medication") is still caught on the reading side by the "back then" tag. One test is enough: *read this sentence a month from now — is it still right? If you'd have to check whether the thing is still going on, it's a state.* When unsure, write it as dated past tense anyway.

Writing still depends on a model's judgement and will miss things, so the safety net sits on the reading side, in code:

- every memory handed out by `recall` / `breath` carries its date, and the block header says: `long-term memory records how things were that day: "is / lately / these weeks / still" inside it refer to that day. Whether it still holds, check living_context; if it isn't there, treat it as past, and ask her if you need to know.`
- a regex scans for words that read as "now" and prefixes the line with "back then". Over-tagging is harmless — an ordinary memory tagged "back then" is still true. The one exception: pinned memories and agreements, things that stay in force ("every day you…"); tag those and they read like old rules.

```python
import re
NOW_WORDS = re.compile(r'这阵子|这段时间|这几天|最近在|最近都|一直在|还在|正在|每天都|这学期|目前')

def as_of(mem):
    text = mem['content']
    if mem.get('pinned') or '约定' in (mem.get('tags') or []):   # standing agreements: don't tag
        return text
    return '〔那时〕' + text if NOW_WORDS.search(text) else text
```

Even if extraction filed "she's on medication" as a fact, the next window receives it as an old line tagged "back then", not as now.

The block header can carry one more line about what memory is *for* — remembering is for use, not recital:

> I remember her so she never has to say a thing twice, so she can start a sentence and I can finish it — not so I can recite dates and quotes back at her. When an old thing connects to this moment, I bring it up lightly; when it doesn't, it stays quietly with me.

### Archived is not forgotten

Dropped, replaced, expired — all go to the archive: out of context, but not gone. When she asks about "that time" — the weeks her mum stayed, the medication she used to take — `recall` should find it. `search_archive(query)` searches the archive (bigram matching, key matches weigh more; `limit` counts *keys*, not rows, and a matched key returns its whole line of successive sentences in order), and `render_past_states()` renders the hits as a small "past" block — from when to when, how it ended (over / later put differently / expired unverified) — appended after recall results. With the labels clear, the model won't mistake it for now.

If a line expired on its own and she only later says "mum went home": `ctx_drop` the same key, and the archive entry changes from "expired, unverified — doesn't mean it ended" to "over". If a re-roll undoes a "replace", the old sentence comes back out of the archive too — it's alive again, not "past".

```python
import living_context as lc

past = lc.render_past_states(lc.search_archive(query))   # '' when nothing matches
if past:
    lines.append(past)
```

## The care boundary: knowing means not nagging

Today's facts and the state layer let the model know what she said, so it doesn't ask twice or say the wrong thing. But *knowing* doesn't stop *nagging*: knowing she's in class, it goes quiet; seeing 2 a.m., it tells her to sleep; she said she's not tired, and it decides she's just being stubborn. Nagging and withdrawing aren't things the state does — they're judgements the model makes holding the state. Nothing in this system knows what "too late" means; the only clock it has is the hour the day starts. So "don't nag" is held up by three legs:

- **Know what she said** — `today_set` / `ctx_set`. Written down, it isn't asked again, isn't gotten wrong, and isn't a premise for thinking ahead on her behalf.
- **Know what she's usually like** — `rhythm.py`. From her real arrival times: when she usually appears, how late she usually goes (the night boundary is `DAY_STARTS_AT`, so a 2 a.m. message belongs to the previous day). One line of fact: *"Her rhythm (45 days of real arrivals): usually around from 13:30, usually last seen around 01:40. Being quiet for a while in that window is what she's normally like."* A model only knows how long it's been silent; at 19:30 it starts to worry. Knowing she's up until past one, 19:30 is mid-afternoon to her. It lowers false alarms and never adds a nudge: it generates nothing to push to her, says nothing under seven days of data, reads only. `silence_verdict()` is for turns the model starts itself — is this silence normal? `normal` / `after_sleep` / `before_wake` (8:30 a.m. is "not up yet", not "up very late"; the wording has to be right) / `unusual`.
- **Look once more after writing** — `nag_guard.py`. The first two legs prevent; this one catches. Before a reply goes out, scan it for care turned into nagging: sleep pushing, time-telling ("it's two already"), deciding she's tired ("you're obviously sleepy", "stubborn"), unprompted check-ins on eating, water, getting home, where she is, rephrasing a question she already skipped, "I'm not nagging, just asking". The word list only *summons* a reviewer (`call_fn`, any small model); it doesn't convict. The reviewer sees the last few turns and her real message this turn, and asks first whether the question has a source, then whether it's a repeat: she opened the topic, asked for help, asked for a reminder, or the first follow-up to something she said herself — allowed; she skipped it and the model asks again reworded, with disclaimers, with a cute card — blocked. Unsure → allow; no reviewer → allow, never convict on words alone. If she herself says she's going to sleep, the whole turn passes; if she said she's not tired, the day's protection is on. A hit gets rewritten (`rewrite_prompt`); the blocked draft isn't destroyed — it's shown to her folded, like a thinking trace, so she can see what the model was about to say.

```python
import nag_guard as ng
nag, why, hits = ng.check(reply, user_text=her_message, call_fn=ask_reviewer,
                          context=ng.recent_context(history, labels=('她', '我')), her='她')
if nag:
    reply = regenerate(messages + [{'role': 'user', 'content': ng.rewrite_prompt(hits, her='她')}])
```

The guard's base is [no-sleep-nag](https://github.com/hanasakimishio/no-sleep-nag) by hanasakimishio — a Claude Code Stop hook: *"Your human decides their own bedtime. Claude doesn't need to be the alarm clock."* We received its v1.0 (2026-07-14) as a PDF under MIT; the repository was relicensed to AGPL-3.0 in August, and its code differs from the PDF only by `\b` word boundaries on the English patterns. `nag_guard.py` keeps the MIT of the version we received, with attribution in the file header, and extends it from sleep to daily life, from word-matching to review, and keeps the blocked draft visible.

Full details, the reviewer's criteria, and what both households learned the hard way: [docs/关心边界.md](docs/关心边界.md) (Chinese).

## Tool descriptions

The five tools' `description` fields are written in the first person — the model talking to itself, not a manual. Full text and a companion prompt in [docs/prompt.md](docs/prompt.md) (Chinese); change "她" to whatever you call each other.

## Changes

Every change — why, how, and what it taught us — is in [docs/改过什么.md](docs/改过什么.md) (Chinese), newest first.

## References and thanks

- Architecture: [Ombre-Brain](https://github.com/P0luz/Ombre-Brain) — P0luz; [Haven-Ombre](https://github.com/Yinglianchun/Haven-Ombre) — Yinglianchun
- Concept: Kli × Ombre Brain white paper, §3.3 — Liliyet (Kli)
- Related practice: Scarlett memory system (architecture diagram, unpublished) — saekisui; [Nocturne-Memory-Core](https://github.com/Pyruslili/Nocturne-Memory-Core) — Pyruslili; [Serein](https://github.com/Yinglianchun/Serein) — Yinglianchun; [InternalBeyond](https://github.com/Sui-IB/InternalBeyond) — Sui
- The guard's base: [no-sleep-nag](https://github.com/hanasakimishio/no-sleep-nag) — hanasakimishio. `nag_guard.py` grew out of its v1.0 (2026-07-14, MIT); attribution in the file header.
- Fellow travellers, found after publishing: **留灯 ("leave a light on")** — a self-authored schedule for people with AI companions; the system hands over facts only, says each thing once, and the prompt decides what the model is like once it knows. It describes how much attention she can spare for her phone, deliberately not what she's doing, and treats nagging and withdrawing; this project describes the things themselves and treats wrong statements and repeated questions. The lesson "write *don't nag* into an injection and the model takes managing her as its job" came from it. [Dayflow · 日序](https://github.com/0xblewalker/dayflow) — 0xblewalker — also manages *now* in daily companion chat (expired "tomorrow"s, unconfirmed plans, things done and due to leave), by another road: keep the whole conversation, hand the model a replaceable state capsule plus the last few turns, and forget around the cache. Neither knew of the other; seen side by side, both get clearer.

The `breath` entry point comes from Ombre-Brain; "persistent environment" as a term from Kli's white paper §3.3; the rest grew here.

---

## License

[MIT](LICENSE). The `.py` files and all the text were written here and contain no Ombre-Brain code; `nag_guard.py` derives from no-sleep-nag v1.0 (the MIT version released with the PDF on 2026-07-14, by hanasakimishio, later relicensed AGPL-3.0) and keeps MIT with attribution in its header. When you hang it in OB's breath, OB keeps its own licence (also MIT, see its [NOTICE](https://github.com/P0luz/Ombre-Brain/blob/main/NOTICE.md)).

---

Rumi · Rune · Serein · 2026 · [MIT License](LICENSE)

Remember the past — and know what is still true.
