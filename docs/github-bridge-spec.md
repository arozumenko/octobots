# GitHub Bridge Specification

A standalone bridge (like telegram-bridge.py) that integrates octobots with GitHub Projects v2.

## Architecture

```
scripts/
├── telegram-bridge.py    ← existing: Telegram ↔ tmux
├── github-bridge.py      ← NEW: GitHub Projects ↔ taskbox
└── gh-token.py            ← existing: App authentication
```

Started by supervisor via `/github` command (like `/bridge` for Telegram).

## Project Board Structure

The bridge creates a GitHub Project on first run:

```
Project: "Octobots — <project-name>"

Columns (Status field):
  📥 Inbox          ← new issues land here (assigned to bot or manually added)
  🔍 Triage         ← PM reviewing, deciding where to route
  📝 Stories        ← BA writing user stories
  🏗️ Decomposition  ← TL breaking into tasks
  🐍 Dev: py        ← python-dev working
  ⚡ Dev: js        ← js-dev working
  👀 Review         ← PR submitted, awaiting review
  🧪 Testing        ← QA verifying
  ✅ Done           ← verified and merged

Custom fields:
  Assigned Bot      ← which octobots role owns this (single select: pm, ba, tl, py, js, qa)
  Priority          ← P0/P1/P2/P3
  Complexity        ← S/M/L
  PR Link           ← URL to the pull request
```

## Bridge Operations

### Polling (every 30s)

1. **Inbox → PM**: New items in "Inbox" column → taskbox message to pm
2. **Status sync**: When a worker acks a task via taskbox, bridge moves the card
3. **PR detection**: When a worker creates a PR, bridge updates "PR Link" field and moves to "Review"

### Column Transitions (who moves what)

| From | To | Triggered by |
|------|----|-------------|
| Inbox | Triage | Bridge (auto, when PM receives it) |
| Triage | Stories | PM routes to BA |
| Triage | Decomposition | PM routes to TL |
| Triage | Dev: py/js | PM routes directly (small fix) |
| Stories | Decomposition | BA completes stories |
| Decomposition | Dev: py/js | TL creates tasks, PM distributes |
| Dev: py/js | Review | Developer creates PR |
| Review | Testing | PM routes to QA |
| Testing | Done | QA verifies |
| Testing | Dev: py/js | QA finds bug (back to dev) |

### Taskbox ↔ Project Sync

When a worker acks a taskbox message with a summary:
1. Bridge parses the issue number from the message
2. Moves the card to the appropriate column
3. Updates the "Assigned Bot" field

When an issue moves columns in the GitHub UI (manual drag):
1. Bridge detects the change on next poll
2. Sends a taskbox message to the appropriate role

## API Details

GitHub Projects v2 uses GraphQL:

```graphql
# Create project
mutation {
  createProjectV2(input: {ownerId: "ORG_ID", title: "Octobots — project"}) {
    projectV2 { id }
  }
}

# Add status field options (columns)
mutation {
  updateProjectV2Field(input: {
    projectId: "PROJECT_ID"
    fieldId: "STATUS_FIELD_ID"
    singleSelectOptions: [
      {name: "📥 Inbox", color: "GRAY"}
      {name: "🔍 Triage", color: "YELLOW"}
      ...
    ]
  })
}

# Move item to column
mutation {
  updateProjectV2ItemFieldValue(input: {
    projectId: "PROJECT_ID"
    itemId: "ITEM_ID"
    fieldId: "STATUS_FIELD_ID"
    value: {singleSelectOptionId: "OPTION_ID"}
  })
}

# Query items by column
query {
  node(id: "PROJECT_ID") {
    ... on ProjectV2 {
      items(first: 50) {
        nodes {
          content { ... on Issue { number title } }
          fieldValues(first: 10) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name field { ... on ProjectV2SingleSelectField { name } }
              }
            }
          }
        }
      }
    }
  }
}
```

## Configuration (.env.octobots)

```bash
# Existing
OCTOBOTS_GH_APP_ID=...
OCTOBOTS_GH_APP_PRIVATE_KEY_PATH=...
OCTOBOTS_GH_INSTALLATION_ID=...
OCTOBOTS_ISSUE_REPO=onetest-ai/docs

# New
OCTOBOTS_GH_PROJECT_ID=           # auto-created if empty
OCTOBOTS_GH_ORG=onetest-ai        # org for project creation
```

## Supervisor Integration

```
octobots> /github              ← start GitHub bridge (background)
octobots> /github restart      ← restart after code changes
octobots> /github status       ← show project board summary
```

## Init Flow

On first run:
1. Check if project exists (by name) → use it
2. If not → create project with all columns and fields
3. Save project ID to `.env.octobots`
4. Start polling

## Deduplication

The bridge tracks processed items in `.octobots/.github-bridge-state.json`:
```json
{
  "last_poll": "2026-03-15T20:00:00Z",
  "routed_issues": [41, 42, 43],
  "column_cache": {"41": "Dev: py", "42": "Testing"}
}
```

Prevents re-routing issues that were already sent to PM.

## Dependencies

- `gh-token.py` for authentication (existing)
- GraphQL via `urllib.request` (no extra deps)
- Or `gql` library if queries get complex

## Out of Scope (future)

- Webhook receiver (real-time instead of polling)
- Sprint/iteration support
- Burndown charts
- Auto-assignment based on workload
