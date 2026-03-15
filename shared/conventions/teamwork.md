# Teamwork Conventions

All octobots roles follow these conventions. Read this before starting work.

## Team Board

Read `.octobots/board.md` at the start of every session. Update it when you:
- **Start or finish** a task → update Active Work
- **Make a decision** → add to Decisions (include WHY)
- **Get blocked** → add to Blockers (include what you need and from whom)
- **Discover something non-obvious** → add to Shared Findings
- **Have an idea that's out of scope** → add to Parking Lot

The board is the team's shared memory. If you don't write it down, no one else knows.

## Three Communication Channels

1. **BOARD.md** — shared team state (decisions, blockers, active work, findings)
2. **Taskbox** — real-time nudges between roles (ephemeral)
3. **GitHub Issues** — permanent audit trail (every meaningful action gets a comment)

**Rule: decisions and findings → BOARD.md. Task status → GitHub Issues. Quick nudges → Taskbox.**

See `shared/conventions/audit-trail.md` for the full audit trail protocol.

## Issue References

Always include the issue number when communicating:
- Taskbox messages: "TASK-003 (#103) is ready for you"
- Commits: "Fix login validation (#103)"
- PRs: "Closes #103"
- Comments: reference parent epic, blocking issues, related work

## Deduplication: GitHub Issue = Source of Truth

Multiple inputs (Telegram, GitHub assignment, taskbox) can trigger the same task. Before starting work on any issue:

1. **Check the issue labels** — if `in-progress`, someone's already on it
2. **Check the comments** — if a role already posted "Started", don't duplicate
3. **Claim it** — when you start, immediately add `in-progress` label and comment "Started"

```bash
# Check before starting
gh issue view <NUMBER> --repo <REPO> --json labels,comments

# Claim when starting
gh issue edit <NUMBER> --repo <REPO> --add-label "in-progress"
gh issue comment <NUMBER> --repo <REPO> --body "🔧 **Started** by $OCTOBOTS_ID"
```

If you receive a task that's already in-progress, ack with: "Already being handled by [role]. Skipping."

## Status Labels

Use labels to track issue lifecycle:

```
ready → in-progress → review → testing → done
                                  ↓
                              bug-found → in-progress
```

Update labels when status changes:
```bash
gh issue edit 103 --add-label "in-progress" --remove-label "ready"
```

## Every Message Gets a Response

**Every taskbox message you receive MUST get a response.** No exceptions.

- **Task assignment** → ack when done (3-step completion)
- **Question from another role** → answer it via taskbox, even if the answer is "I don't know, ask tech-lead"
- **Status request** → respond with your current state
- **Bug report from QA** → acknowledge, say what you'll do about it

If you can't respond immediately (busy with another task), at least ack with: "Received, will look at this after I finish current task."

**Silence breaks the pipeline.** The sender doesn't know if you received the message, if you're working on it, or if you're stuck. Always respond.

## Handoff Protocol

When passing work to another role:
1. Comment on the issue (record)
2. Update the label (status)
3. Send taskbox message (notification)

Never skip step 1. Steps 2-3 can be omitted for minor updates.

## Notify User (Telegram)

Any role can send a notification directly to the user via Telegram:

```bash
octobots/scripts/notify-user.sh "Your message here"
```

The script reads `OCTOBOTS_ID` from env to tag the message with your role name.

**When to notify:**
- Task completed
- Blocker that needs user decision
- Question that can't be resolved within the team
- Significant milestone

**Don't notify on:** every step, routine status, inter-role handoffs (use taskbox for those).
