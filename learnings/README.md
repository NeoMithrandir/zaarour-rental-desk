# Learnings

What the desk has learned from Stefan's decisions and from landlords' answers, written as plain markdown so a
person can read it and an agent can follow it.

The desk writes these files itself (after a background read, at most once a day) or on demand:

```bash
python3 -m desk learn
```

| File | What's in it |
|---|---|
| `summary.md` | Start here. Guidance written as instructions, each with its evidence and a _tentative_ or _supported_ mark, then the standing rules that always apply. |
| `platforms/<board>.md` | One per board: approvals, replies, why ads were rejected, and every signal broken down for that board. |
| `signals/*.md` | Approval and reply rates by area, phase, rent, owner vs property manager, ad age, ad language and board. |
| `hooks.md` | Words in ad titles that go with approvals and with replies. |
| `messages.md` | How each way of opening a message is doing (the approaches in `config/approaches.json`, with the share of new messages each one writes), then reply and presentation rates per message template (and whether Stefan edited it). |
| `findings.json` | The same numbers for scripts and agents. |

How to read the numbers:

- **Approval rate** is Stefan's call on the ad (Approve vs Reject or Later). **Reply rate** is among sent messages
  with a known outcome: one recorded on the Sent tab, or no answer for `learn.no_reply_days` (14), which counts as no
  reply. Replied, Presentation booked and Listing signed count as replies; Said no and No reply don't.
- `summary.md` opens with **the desk test** (300 messages; 6 or more signed listings is a success, 2 or fewer is the
  sign to stop) and which approach is ahead, with the same caution about small samples.
- Each rate has a 95% range. A segment is marked **higher** or **lower** only when that range clears the overall
  rate; **tentative** means fewer than `learn.min_sample` decisions (30 by default). Treat tentative findings as
  hunches to test, never as rules.
- Rates use each ad's current state, so an undone mis-click doesn't count. The full history, with each ad's signals
  at the time, is in the `events` table of `data/desk.sqlite3`.

Using them as agent instructions: read `summary.md` first, follow the standing rules without exception, act on
_supported_ guidance, and test _tentative_ guidance rather than relying on it. Nothing here permits sending a message
without a person approving it.

The generated files are not committed (they come from the desk's own records); only this README is.
