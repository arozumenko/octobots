# Multi-Repo Workspaces

How octobots works in projects with multiple git repositories under one workspace.

## Current Approach: Shared Workspace + Branches

All workers share the same workspace. Each creates a branch in the relevant sub-repo for their task. PM coordinates to avoid two workers editing the same repo simultaneously.

```
workspace/                     ← everyone works here
├── core/                      ← git repo
├── services/gateway/          ← git repo
├── services/membership/       ← git repo
├── frontend/                  ← git repo
└── octobots/                  ← octobots (own repo)
```

**Worker flow:**
1. PM assigns task: "Fix #42 in core/"
2. Worker: `cd core && git checkout -b feat/issue-42`
3. Worker makes changes, commits, pushes
4. Worker: `gh pr create --repo org/core`
5. Worker: `cd .. && git checkout main` (clean up)

**PM's job:** Don't assign two workers to the same sub-repo at the same time.

## Future: Isolated Worker Workspaces

For full parallel isolation, each code-writing worker gets their own copy of the entire workspace. The scout sets this up during project seeding.

```
workspace/
├── core/                      ← original repos
├── services/
├── octobots/
└── .workers/                  ← isolated workspaces
    ├── python-dev/
    │   ├── core/              ← own git clone, own branches
    │   ├── services/
    │   ├── .mcp.json → ../../.mcp.json
    │   ├── .env.octobots → ../../.env.octobots
    │   └── venv/ → ../../venv (or fresh)
    ├── js-dev/
    │   ├── core/
    │   ├── services/
    │   └── ...
    └── qa-engineer/
        ├── core/
        ├── services/
        └── ...
```

### Setup (by scout or manually)

```bash
# For each code-writing worker
for worker in python-dev js-dev qa-engineer; do
    mkdir -p .workers/$worker

    # Clone each sub-repo
    for repo in core services/gateway services/membership frontend; do
        git clone $(cd $repo && git remote get-url origin) .workers/$worker/$repo
    done

    # Symlink shared config
    ln -sf ../../.mcp.json .workers/$worker/.mcp.json
    ln -sf ../../.env.octobots .workers/$worker/.env.octobots
    ln -sf ../../octobots .workers/$worker/octobots
done
```

### Supervisor changes

The supervisor would `cd` each worker into their isolated workspace:
```bash
# Instead of all workers in workspace root:
tmux send-keys -t pane "cd .workers/js-dev" Enter
# Then launch claude there
```

### Benefits
- Full parallel isolation — no branch conflicts
- Each worker has their own git state
- PRs are the integration point
- Workers can run tests independently

### Trade-offs
- Disk space: N copies of the workspace
- Setup time: cloning repos on seed
- Sync: workers need to `git pull` to see each other's merged work
- Shared resources: databases, ports need per-worker config (.env.local)

### Resource Sharing Model

```
Shared (same for all workers):       Isolated (per worker):
├── Database (dev instance)           ├── Git repos (own clones, own branches)
├── Redis, message queues             ├── Ports (offset per worker)
├── venv / node_modules               ├── .env.local (worker-specific config)
├── Docker services                   └── App instance (own port)
├── .mcp.json
└── .env.octobots
```

**Shared resources are fine** — workers read the same DB, same Redis, same queues. Conflicts are rare in dev (different features touch different data). If a worker needs isolated test data, it creates records with a unique prefix and cleans up after.

**Ports must be unique.** Each worker runs the app on a different port:

```
Worker          API Port    Frontend Port
python-dev      8001        3001
js-dev          8002        3002
qa-engineer     8003        3003
```

The `.env.local` per worker handles this:
```bash
# .workers/python-dev/.env.local
PORT=8001
FRONTEND_PORT=3001
WORKER_ID=python-dev
```

**venv / node_modules are symlinked** — same deps, no reinstall:
```bash
ln -sf ../../venv .workers/python-dev/venv
ln -sf ../../node_modules .workers/js-dev/node_modules
```

Only create a fresh venv if the worker's branch changes dependencies.

### What Workers Need to Know

Each worker's CLAUDE.md should understand:

1. **You have your own copy of the code** — branch freely, commit freely
2. **Database is shared** — don't drop tables, prefix test data with your worker ID
3. **Your app runs on a unique port** — check `.env.local` for your port
4. **venv is shared** — don't `pip install` new packages without checking with tech lead
5. **PRs are how your work reaches main** — always create a PR, never push to main
6. **Pull before branching** — `git pull origin main` to avoid stale code

### When to use
- **Shared workspace (current):** Small team, sequential tasks, same-repo work is rare
- **Isolated workspaces (future):** Large team, parallel tasks, workers frequently touch same repos
