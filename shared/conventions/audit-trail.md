# Audit Trail Convention

Every octobots role follows these rules for work visibility and auditability.

## Two Channels, Two Purposes

| Channel | Purpose | Persistence | Who Reads |
|---------|---------|-------------|-----------|
| **Taskbox** | Real-time coordination | Ephemeral | Direct recipient |
| **GitHub Issues** | Audit trail, work log | Permanent | Everyone, forever |

**Rule: If it matters, it goes on the issue. Taskbox is a nudge, not a record.**

## When to Comment on GitHub Issues

Every role MUST comment on the relevant issue when:

| Event | Who | Comment Template |
|-------|-----|-----------------|
| Starting work | Developer | `🔧 **Started**: [brief approach]` |
| Meaningful progress | Developer | `📝 **Update**: [what changed, what's next]` |
| Blocked | Anyone | `🚫 **Blocked**: [what's blocking, what's needed]` |
| Question | Anyone | `❓ **Question**: [specific question, options if known]` |
| Task complete | Developer | `✅ **Done**: [summary of changes, PR link]` |
| QA started | QA | `🧪 **Testing**: [test plan summary]` |
| Bug found | QA | `🐛 **Bug**: [severity, reproduction, evidence]` |
| QA passed | QA | `✅ **Verified**: [what was tested, evidence]` |
| Story created | BA | `📋 **Stories created**: [list with links]` |
| Tasks created | Tech Lead | `🔨 **Decomposed**: [task list with deps]` |
| Distributed | PM | `📬 **Assigned**: [who got what]` |
| Status update | PM | `📊 **Status**: [table of progress]` |

## How to Comment

```bash
# Comment on an issue
gh issue comment 103 --body "$(cat <<'EOF'
🔧 **Started**: Implementing login endpoint.

Approach: Using existing auth middleware + JWT library.
Files: `src/auth/routes.py`, `src/auth/service.py`
Expected: Done by end of session.
EOF
)"

# Comment with evidence (QA)
gh issue comment 103 --body "$(cat <<'EOF'
🐛 **Bug**: Login fails with uppercase email

**Severity:** Major
**Steps:** POST /api/auth/login with email "User@Test.com"
**Expected:** Successful login
**Actual:** 500 Internal Server Error
**Console:** `ValueError: email normalization failed`
**Frequency:** Always
EOF
)"
```

## Issue Lifecycle

```
Created (BA/TL) → Assigned (PM) → In Progress (Dev) → Review (Dev) →
Testing (QA) → Done (QA verified) or → Bug Found (QA) → back to In Progress
```

Track via labels:

```bash
# Status transitions
gh issue edit 103 --add-label "in-progress" --remove-label "ready"
gh issue edit 103 --add-label "review" --remove-label "in-progress"
gh issue edit 103 --add-label "testing" --remove-label "review"
gh issue edit 103 --add-label "done" --remove-label "testing"
gh issue edit 103 --add-label "bug-found" --remove-label "testing"
```

## Cross-Referencing

Always link related items:

```bash
# Reference PR in issue
gh issue comment 103 --body "✅ **Done**: PR #45 submitted. Changes: login endpoint + tests."

# Reference issue in PR
gh pr create --title "Implement login endpoint" \
  --body "Closes #103. See issue for acceptance criteria."

# Reference parent epic
gh issue comment 103 --body "Part of epic #100."

# Reference blocking issue
gh issue comment 105 --body "🚫 **Blocked** by #103 (login endpoint not ready yet)."
```

## Taskbox + Issues Together

**Pattern: Nudge via taskbox, record on issue.**

```bash
# PM distributes task
# 1. Comment on the issue (record)
gh issue comment 103 --body "📬 **Assigned** to python-dev."

# 2. Nudge via taskbox (notification)
python octobots/skills/taskbox/scripts/relay.py send \
  --from project-manager --to python-dev \
  "TASK-003 (#103) assigned to you. Login endpoint. Deps clear. See issue for details."
```

```bash
# Developer completes work
# 1. Comment on the issue (record)
gh issue comment 103 --body "✅ **Done**: PR #45. JWT login with rate limiting. All tests pass."

# 2. Nudge PM via taskbox (notification)
python octobots/skills/taskbox/scripts/relay.py send \
  --from python-dev --to project-manager \
  "TASK-003 (#103) complete. PR #45 ready for review. Routing to QA."
```

## What Goes Where

| Information | Taskbox | GitHub Issue |
|-------------|---------|-------------|
| "Start working on #103" | ✅ nudge | ✅ "Started" comment |
| Implementation approach | ❌ | ✅ comment |
| "I'm blocked" | ✅ nudge to PM | ✅ comment with details |
| Bug reproduction steps | ❌ | ✅ full report |
| "Bug fixed, re-test" | ✅ nudge to QA | ✅ comment with PR link |
| Status summary | ✅ to user via PM | ✅ epic comment |
| Quick question | ✅ (if urgent) | ✅ (if needs record) |
| Design decision | ❌ | ✅ always on record |
