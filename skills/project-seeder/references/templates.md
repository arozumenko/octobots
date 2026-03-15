# Project Seeder Templates

## AGENTS.md Template

```markdown
# Project Name

One-paragraph description: what it does, who it's for, what it's built with.

## Tech Stack

- **Language:** Python 3.12 / TypeScript 5.x / etc.
- **Framework:** FastAPI / Next.js / Django / Express / etc.
- **Database:** PostgreSQL / MongoDB / SQLite / etc.
- **Cache:** Redis / Memcached / none
- **Infra:** Docker / Kubernetes / Vercel / etc.
- **CI:** GitHub Actions / GitLab CI / Jenkins

## Repository Structure

<!-- Annotated directory tree, depth 2-3 -->
```
src/
├── api/            ← Route handlers
├── services/       ← Business logic
├── models/         ← Database models
├── utils/          ← Shared utilities
tests/
├── unit/           ← Fast, no external deps
├── integration/    ← Requires database
docker/
├── Dockerfile
├── docker-compose.yml
```

## Build & Run

```bash
# Install dependencies
[exact command from project]

# Run in development
[exact command]

# Run tests
[exact command]

# Lint / format
[exact command]

# Build for production
[exact command]
```

## Environment Setup

Required environment variables:

| Variable | Purpose | Example |
|----------|---------|---------|
| `DATABASE_URL` | PostgreSQL connection | `postgresql://user:pass@localhost/db` |
| `SECRET_KEY` | JWT signing | random string |
| `API_KEY` | External service | from provider dashboard |

Setup: `cp .env.example .env` and fill in values.

## Coding Conventions

<!-- Only include patterns actually detected in the codebase -->

- **Naming:** snake_case for files/vars, PascalCase for classes
- **Imports:** stdlib → third-party → local, absolute imports
- **Error handling:** Custom exception classes in `src/exceptions.py`
- **Code org:** Thin routes → service layer → repository → database
- **Types:** Strict mode, all public functions typed
- **Comments:** Docstrings on public APIs, no inline obvious comments

## Testing

- **Framework:** pytest / jest / vitest
- **Run:** `[exact command]`
- **Structure:** `tests/` mirrors `src/`, one test file per module
- **Fixtures:** Shared in `conftest.py` / `__tests__/setup.ts`
- **Coverage:** `[command]` (currently at X%)

## CI/CD

- **Trigger:** Push to main, PRs
- **Pipeline:** lint → type-check → test → build → deploy
- **Config:** `.github/workflows/ci.yml`

## Notes

<!-- Non-obvious things: why a dep is pinned, known tech debt, gotchas -->
- `libfoo` pinned to 2.3.1 due to breaking change in 2.4
- Database migrations must run before starting the app
- The `/legacy/` directory is deprecated but still serves traffic
```

---

## .octobots/profile.md Template

```markdown
---
project: my-project
team: platform
issue-tracker: https://github.com/org/repo/issues
default-branch: main
languages: [python]
---

# My Project

Brief description.

## Tech Stack
- Primary framework + version
- Database
- Key dependencies

## Build & Test
- Install: `command`
- Test: `command`
- Lint: `command`

## Conventions
- Top 3-5 conventions detected
```

---

## .octobots/architecture.md Template

```markdown
# Architecture

## System Overview

<!-- One paragraph: what the system does at a high level -->

## Components

| Component | Purpose | Tech | Port/Path |
|-----------|---------|------|-----------|
| API Gateway | Request routing, auth | FastAPI | :8000 |
| Worker | Background jobs | Celery | N/A |
| Frontend | User interface | Next.js | :3000 |
| Database | Persistence | PostgreSQL | :5432 |

## Data Flow

```
User → Frontend (Next.js) → API Gateway (FastAPI) → Database (PostgreSQL)
                                    ↓
                              Worker (Celery) → External APIs
```

## API Boundaries

### Internal APIs
- `POST /api/users` — Create user (auth required)
- `GET /api/users/:id` — Get user (auth required)

### External Dependencies
- **Stripe** — Payment processing (webhook at `/webhooks/stripe`)
- **SendGrid** — Email delivery

## Database

### Key Tables
| Table | Purpose | Key Relations |
|-------|---------|---------------|
| users | User accounts | has_many: orders |
| orders | Purchase records | belongs_to: users |

### Migrations
- Tool: Alembic / Prisma / Knex
- Run: `[command]`
- Location: `migrations/`
```

---

## .octobots/conventions.md Template

```markdown
# Coding Conventions

Detected from codebase analysis. These are descriptive (what IS), not prescriptive.

## File Naming
- Source: `snake_case.py` / `camelCase.ts` / `PascalCase.tsx`
- Tests: `test_module.py` / `module.test.ts` / `module.spec.ts`
- Components: `ComponentName.tsx` / `ComponentName/index.tsx`

## Code Organization
- Routes/handlers: thin, delegate to services
- Services: business logic, no framework deps
- Models: data layer, ORM definitions
- Utils: shared helpers, no side effects

## Import Style
```python
# 1. stdlib
# 2. third-party
# 3. local (absolute imports)
```

## Error Handling
- Custom exceptions in `src/exceptions.py`
- Global error handler in middleware
- Specific catches only, no bare except

## Naming
- Variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private: `_prefixed`

## Git
- Branch: `feat/`, `fix/`, `chore/`
- Commits: conventional commits / free-form
- PR: squash merge / merge commit
```

---

## .octobots/testing.md Template

```markdown
# Test Infrastructure

## Framework
- **Unit/Integration:** pytest / jest / vitest
- **E2E:** Playwright / Cypress / none
- **API:** httpx / supertest / curl

## Commands
```bash
# All tests
[command]

# Unit only
[command]

# Integration (requires DB)
[command]

# E2E (requires running app)
[command]

# Coverage
[command]
```

## Structure
```
tests/
├── unit/              ← No external deps, fast
├── integration/       ← Real DB, slower
├── e2e/               ← Full stack, Playwright
├── conftest.py        ← Shared fixtures
└── fixtures/          ← Test data files
```

## Fixtures & Setup
- Database: [real / mocked / in-memory SQLite]
- Auth: [fixture / factory / skip]
- Test data: [factories / fixtures / inline]

## Patterns Detected
- Arrange-Act-Assert structure
- One test file per source module
- Shared fixtures in conftest.py
- Test markers: @pytest.mark.slow, @pytest.mark.integration

## CI Integration
- Tests run on: [push / PR / both]
- Config: `.github/workflows/test.yml`
- Timeout: [X minutes]
- Coverage threshold: [X% or none]

## Known Issues
- [Any flaky tests, slow tests, skip reasons]
```
