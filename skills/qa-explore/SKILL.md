---
name: qa-explore
description: >
  Multi-specialist bug detection, persona feedback, and test case generation.
  Uses 30+ domain-specific expert lenses to analyze web pages for accessibility,
  security, UI/UX, performance, privacy, and more. Primary tool is browser-verify
  (CDP — cookies, localStorage, axe-core, performance, meta tags). MCPs provide
  additional Lighthouse and ARIA data when available. Browser-only — no code access needed.
  Use when asked to: "find bugs", "audit this page", "QA check", "check for
  accessibility/security/performance issues", "persona feedback", "user feedback",
  "generate test cases", "create tests", or any variation of bug detection or
  UX review. Output is a dark-mode HTML report saved to .octobots/reports/.
license: Apache-2.0
compatibility: >
  Primary: browser-verify skill (Chrome + Node 22, no npm install). Optional
  enhancement: mcp-accessibility-scanner (axe-core + Playwright),
  @danielsogl/lighthouse-mcp. Falls back to visual analysis from screenshot only.
metadata:
  author: octobots
  version: "0.3.0"
---

# QA Audit

Analyze **web pages** through **30+ specialist lenses** — each focused on a specific domain.
Produces dark-mode HTML reports with prioritized findings and ready-to-use fix prompts.

No external API. Claude performs all analysis directly. Specialist passes are enhanced
by browser MCPs when available — axe-core for real WCAG violations, Lighthouse for
objective performance metrics.

## Tool Capabilities

**browser-verify is the primary tool.** It provides what no MCP can: running JS
in page context, reading cookies and storage, enumerating trackers, injecting
axe-core, and measuring real performance timing. MCPs layer on top when available.

| Tool | Type | Role | Adds |
|------|------|------|------|
| **browser-verify** | Skill (CDP) | **Primary** | `inject-axe`, `get-storage`, `get-performance`, `get-meta`, `get-cookies`, `get-console`, `get-network`, device emulation, arbitrary JS eval |
| **Accessibility Scanner** | MCP (`mcp-accessibility-scanner`) | Enhancement | `scan_page`, `audit_keyboard` — axe-core via MCP (no Chrome required on host) |
| **Lighthouse** | MCP (`@danielsogl/lighthouse-mcp`) | Enhancement | `get_core_web_vitals`, `get_performance_score`, `get_security_audit`, `get_seo_analysis` |
| **Playwright** | MCP (`@playwright/mcp`) | Fallback | `browser_navigate`, `browser_snapshot`, `browser_take_screenshot` |

`mcp-accessibility-scanner` includes Playwright — if available, Playwright MCP
is redundant.

**browser-verify setup** (Chrome + Node 22, no npm install):
```bash
SCRIPTS="{OCTOBOTS_DIR}/skills/browser-verify/scripts"
bash "$SCRIPTS/chrome-launcher.sh" start --headless
# ... run audit ...
bash "$SCRIPTS/chrome-launcher.sh" stop
```

**Optional MCP enhancement (add to `.mcp.json`):**
```json
{
  "mcpServers": {
    "accessibility-scanner": { "command": "npx", "args": ["-y", "mcp-accessibility-scanner"] },
    "lighthouse": { "command": "npx", "args": ["@danielsogl/lighthouse-mcp@latest"] }
  }
}
```

## Modes

| Mode | When to use |
|------|-------------|
| **Bug Audit** | "find bugs", "audit this", "QA check", "check for [type] issues" |
| **Persona Feedback** | "user feedback", "persona analysis", "what would users think" |
| **Test Case Generation** | "generate test cases", "create tests", "test scenarios" |

For browser-based test *execution*, use the `playwright-testing` skill.

---

## Step 0: Start Browser and Gather Page Data

### A. Start browser-verify Chrome (primary)

Resolve `SCRIPTS` from the `browser-verify` skill directory, then:

```bash
bash "$SCRIPTS/chrome-launcher.sh" status   # check if already running
bash "$SCRIPTS/chrome-launcher.sh" start --headless   # start if not running
```

Keep Chrome running for the entire audit session — all specialist passes share it.
Stop it only after Step 6b.

### B. Gather data for each page

**Standard data collection per page — run these in order:**

```bash
node "$SCRIPTS/cdp.mjs" navigate "<url>"
node "$SCRIPTS/cdp.mjs" screenshot --output /tmp/audit-<page>.png
node "$SCRIPTS/cdp.mjs" inject-axe                    # WCAG 2.x violations
node "$SCRIPTS/cdp.mjs" get-performance               # timing + resources + CWV
node "$SCRIPTS/cdp.mjs" get-meta                      # meta tags, OG, structured data
node "$SCRIPTS/cdp.mjs" get-storage                   # localStorage + sessionStorage
node "$SCRIPTS/cdp.mjs" get-cookies                   # all cookies + flags
node "$SCRIPTS/cdp.mjs" get-console                   # JS errors and warnings
node "$SCRIPTS/cdp.mjs" get-network --status error    # failed requests (4xx/5xx)
```

Read the screenshot with the Read tool to get the visual view.

**Multi-page audit:** navigate to each URL in sequence using the same Chrome instance.
Each `navigate` call resets the console and network buffers for that page.
Run the full collection block above for every URL before moving to specialist analysis.

```bash
# Example: audit 3 pages
node "$SCRIPTS/cdp.mjs" navigate "https://example.com"
node "$SCRIPTS/cdp.mjs" screenshot --output /tmp/audit-home.png
node "$SCRIPTS/cdp.mjs" inject-axe
node "$SCRIPTS/cdp.mjs" get-performance
node "$SCRIPTS/cdp.mjs" get-meta
node "$SCRIPTS/cdp.mjs" get-storage
node "$SCRIPTS/cdp.mjs" get-cookies
node "$SCRIPTS/cdp.mjs" get-console
node "$SCRIPTS/cdp.mjs" get-network --status error

node "$SCRIPTS/cdp.mjs" navigate "https://example.com/pricing"
node "$SCRIPTS/cdp.mjs" screenshot --output /tmp/audit-pricing.png
node "$SCRIPTS/cdp.mjs" inject-axe
# ... repeat collection block
```

Collect all page data first, then run all specialist analysis passes.
This avoids re-navigating during analysis.

### C. MCP enhancement (when available)

After browser-verify data collection, try these if MCPs are configured:

```
Try: scan_page(url)              → Accessibility Scanner MCP — supplements inject-axe
Try: get_performance_score(url)  → Lighthouse MCP — adds lab scores vs field data
Try: get_security_audit(url)     → Lighthouse MCP — HTTPS / CSP header analysis
Try: get_seo_analysis(url)       → Lighthouse MCP — crawlability / indexing issues
```

MCP tools add objective lab scores and additional coverage on top of browser-verify data.
Never replace browser-verify with MCPs — they cannot access storage, cookies, or trackers.

### D. Fallback: no browser tools available

If browser-verify fails (Chrome not installed / Node version too old) and no MCP is
available: ask the user to upload a screenshot. Note missing data layers in the report.

### Option B: Code Snippet

Accept code inline, from a file, or from a git diff. Proceed directly to Step 1.

---

# Mode 1: Bug Audit

## Step 1: Determine Applicable Check Types

Read `references/testers.md` for the full check-type list and default selections.

**For web pages — always run:**
`security`, `privacy`, `accessibility`, `content`, `networking`

**For web pages — add conditionally** (detect from screenshot):
`ui-ux` if UI elements visible · `forms` if any form present · `mobile` if mobile viewport ·
`signup` / `checkout` / `shopping-cart` / `product-details` / `pricing` / `landing` /
`homepage` / `contact` / `search-box` / `news` / `video` / `ai-chatbots` / `legal` /
`gdpr` if EU-facing · `owasp` if security-sensitive page · `wcag` if deep a11y needed

**For code — always run:**
`security`, `javascript`, `genai` (if AI-generated), `accessibility` (if HTML/JSX),
`ui-ux` (if HTML/CSS/React/Vue)

If the user requested specific checks ("find security issues", "check accessibility"),
run only those.

## Step 2: Run Specialist Analysis Passes

For each applicable check type, adopt the specialist's perspective and analyze the
page exclusively through that domain lens.

Reference `references/testers.md` for each specialist's name, icon, and focus areas.

**Some passes use MCP data when available.** Run the MCP tool first, then layer
visual and ARIA-tree analysis on top. Never skip visual analysis even when MCP
data is present — MCPs catch rule violations, visual analysis catches UX issues
that automated tools miss.

### Tool-Enhanced Passes

All browser-verify data was collected in Step 0. Each specialist pass below
reads from that collected output. Run additional browser-verify commands only
when a pass needs data not already gathered (e.g. Fatima's tracker enumeration,
Zanele's mobile emulation). MCPs run once per pass when available.

---

**Sophia (accessibility) and Mei (WCAG)**

Primary — use `inject-axe` result from Step 0 data collection:
```
inject-axe output → violations[].impact → priority mapping:
  critical → 9, serious → 7, moderate → 5, minor → 2
```
Each violation has `id`, `description`, `helpUrl`, `nodes[].html`, `nodes[].failureSummary`.
Attribute to Sophia (general a11y/UX) or Mei (WCAG technical criteria) based on
the violation type.

If Accessibility Scanner MCP also ran:
```
scan_page(url, tags=["wcag2a", "wcag2aa", "wcag21aa", "best-practice"])
audit_keyboard(url)
```
Merge with axe results — deduplicate by violation `id`. MCP may add keyboard
audit findings not in the CDP axe run.

For visual issues axe cannot catch (cognitive load, layout clarity, focus
visibility), continue with screenshot analysis.

---

**Marcus (networking/performance)**

Primary — use `get-performance` and `get-network` results from Step 0:
```
get-performance output:
  timing.loadComplete        → total page load time
  timing.firstByte           → TTFB
  coreWebVitals.lcp          → Largest Contentful Paint (ms)
  coreWebVitals.cls          → Cumulative Layout Shift (score)
  resources.top20Slowest     → slow resources with sizeKB
get-network --status error  → any 4xx/5xx requests

Priority guide: loadComplete > 5000ms = critical(9), > 3000ms = high(7),
  > 1500ms = medium(4). LCP > 4000ms = critical, > 2500ms = high.
  CLS > 0.25 = high, > 0.1 = medium. Any 4xx/5xx = high.
```

If Lighthouse MCP also ran:
```
get_core_web_vitals(url, device="mobile")   → lab scores supplement field data
get_performance_score(url, device="mobile")
analyze_resources(url)
```
Lighthouse adds lab-measured scores and resource opportunity analysis.
Score → priority: <50 = critical(9), 50–74 = high(7), 75–89 = medium(4), 90+ = low(2).

For additional network detail:
```bash
node "$SCRIPTS/cdp.mjs" get-network --type xhr    # XHR/fetch calls specifically
node "$SCRIPTS/cdp.mjs" get-network --type script  # slow script loads
```

---

**Fatima (privacy/cookies)**

Use `get-cookies` and `get-storage` results from Step 0 — no MCP can provide this data:
```
get-cookies output:   name, value, domain, httpOnly, secure, expires per cookie
get-storage output:   localStorage keys/values, sessionStorage keys/values

Check for:
- Cookies missing httpOnly or secure flags
- Sensitive data in localStorage (tokens, PII, card numbers)
- Session data persisting across expected boundaries
```

For tracker enumeration, run additionally:
```bash
node "$SCRIPTS/cdp.mjs" evaluate "JSON.stringify({
  googleAnalytics: !!(window.ga || window.gtag || window.dataLayer),
  metaPixel: !!(window.fbq || window._fbq),
  hotjar: !!window.hj,
  intercom: !!window.Intercom,
  segment: !!window.analytics,
  mixpanel: !!window.mixpanel
})"
node "$SCRIPTS/cdp.mjs" evaluate "JSON.stringify({
  hasCookieBanner: !!document.querySelector('[class*=cookie],[class*=consent],[id*=cookie],[id*=consent],[class*=gdpr],[id*=gdpr]'),
  hasAcceptButton: !!document.querySelector('[class*=cookie] button,[class*=consent] button,button[id*=accept i],button[class*=accept i]')
})"
node "$SCRIPTS/cdp.mjs" evaluate "JSON.stringify((() => {
  const host = location.hostname;
  return [...document.querySelectorAll('script[src]')].map(s => s.src).filter(src => !src.includes(host));
})()"
```

---

**Tariq (security)**

If Lighthouse MCP available:
```
get_security_audit(url)
```
Catches HTTPS, mixed content, CSP header gaps.

Supplement with visual analysis: input validation risks, exposed tokens in
URLs, clickjacking potential, form security.

---

**Leila (content/SEO)**

Use `get-meta` result from Step 0:
```
get-meta output:
  title, description, keywords, viewport, robots, canonical
  og.*       → Open Graph tags for social sharing
  twitter.*  → Twitter card tags
  structuredData → JSON-LD structured data (schemas)

Check for: missing description, missing og:image/og:title, missing canonical,
  robots=noindex on live pages, broken/missing structured data schemas.
```

If Lighthouse MCP also ran:
```
get_seo_analysis(url)   → adds crawlability and indexing checks
```

Supplement with page text analysis from screenshot for copy quality, typos, clarity.

---

**Diego (console)**

Use `get-console` result from Step 0. browser-verify captures all levels
(error, warn, info, log, debug) including Runtime.consoleAPICalled events that
MCPs may miss.

If MCP also ran: `browser_console_messages()` — merge and deduplicate by message text.

---

**Zanele (mobile)**

```bash
node "$SCRIPTS/cdp.mjs" emulate mobile
node "$SCRIPTS/cdp.mjs" screenshot --output /tmp/audit-mobile.png
node "$SCRIPTS/cdp.mjs" evaluate "JSON.stringify({
  viewport: { w: window.innerWidth, h: window.innerHeight },
  isMobile: window.matchMedia('(max-width: 768px)').matches,
  touchTargets: [...document.querySelectorAll('a, button')]
    .map(el => { const r = el.getBoundingClientRect();
      return { text: el.textContent.trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height), tooSmall: r.width < 44 || r.height < 44 }; })
    .filter(t => t.tooSmall).slice(0, 15)
})"
node "$SCRIPTS/cdp.mjs" emulate desktop   # always restore after Zanele's pass
```
Read the mobile screenshot to analyze layout, font sizes, and overflow.
Touch targets under 44×44px are a WCAG 2.5.5 failure — flag as high priority.

---

### Visual + ARIA Analysis (all passes)

**Analysis discipline:**
- Stay in the specialist's lane — only flag what falls within their domain
- Base every finding on something visible in the screenshot, ARIA tree, console logs,
  or network data
- Never fabricate issues not supported by the evidence
- If unsure whether something is a real bug, lower the confidence score (1–5), don't omit it

**Finding schema — produce this for every issue found:**
```json
{
  "title": "Short descriptive title",
  "types": ["Category1", "Category2"],
  "priority": 7,
  "confidence": 8,
  "reasoning": "Why this is a problem and what user impact it has",
  "suggested_fix": "Plain English fix description",
  "fix_prompt": "Ready-to-paste Claude/developer prompt to fix this",
  "specialist_id": "sophia",
  "specialist_name": "Sophia",
  "specialist_icon": "♿",
  "specialist_specialty": "Accessibility & WCAG"
}
```

**Priority scale:**
- 8–10: Critical — will break UX, security risk, data loss, blocks core flow
- 5–7: High — degrades experience significantly, affects many users
- 4: Medium — noticeable issue, workaround exists
- 1–3: Low — minor polish, edge case, best-practice suggestion

**Confidence scale:**
- 8–10: Clearly visible in evidence, definitely a bug
- 5–7: Likely a bug but context-dependent
- 1–4: Possible issue, needs verification

## Step 3: Deduplicate and Rank

After all passes, review findings:
- Merge findings that describe the same underlying issue (keep the higher-priority attribution)
- Sort all findings by priority descending (critical first)
- For ties, sort by confidence descending

## Step 4: Generate Bug Audit HTML Report

Use the templates in `references/html-templates.md`.

Fill in all placeholders:
- `{TARGET}` — URL or file name
- `{DATE}` — today's date (YYYY-MM-DD)
- `{SPECIALIST_COUNT}` — number of specialists with findings
- `{ANALYSIS_TYPE}` — "Web Page" or "Code"
- `{TOTAL}`, `{CRITICAL}`, `{HIGH}`, `{MEDIUM}`, `{LOW}` — counts by priority band
- Priority band: 8–10 = critical, 5–7 = high, 4 = medium, 1–3 = low

For each specialist who found issues, add a card to the Specialists grid with their
icon, name, specialty, and issue count.

For each bug card:
- Priority CSS class: `priority-critical` / `priority-high` / `priority-medium` / `priority-low`
- Badge class follows the same pattern
- `fix_prompt` goes in the `<pre class="fix-prompt">` block — this is the most
  actionable part of the report, make it copy-paste ready

Top Recommendations section: synthesize the 3–5 highest-value actions across all findings.
Not just the top 3 bugs — strategic recommendations for the biggest impact.

**Save to:** `.octobots/reports/{site}-bug-audit-{YYYY-MM-DD}.html`

Create `.octobots/reports/` if it doesn't exist.

## Step 5: Present Findings in Chat

After saving the report, present a structured summary:

```
## QA Audit — {target}
{total} issues found across {specialist_count} specialist checks.

### 🔒 Tariq (Security) — 2 issues
**[Critical 9/10]** SQL Injection risk in search input
Impact: Attackers can extract database contents
Fix: Parameterize queries; never interpolate user input into SQL

**[High 6/10]** Missing Content-Security-Policy header
Impact: Increases XSS risk
Fix: Add CSP header with strict directives

---

### ♿ Sophia (Accessibility) — 3 issues
...
```

Order specialists by their highest-priority issue first.

## Step 6: Integration Hooks (offer after presenting findings)

After showing the summary, offer:

**If critical issues found:**
> "Create GitHub issues for critical findings? I can use the `issue-tracking` skill."

**If qa-engineer is a running worker:**
> "Route findings to qa-engineer for reproduction via taskbox?"

**To generate test cases from findings:**
> "Generate test cases targeting these bugs? (triggers Mode 3)"

Don't offer all three at once — pick the most relevant based on context.

## Step 6b: Stop browser-verify Chrome (if started)

If browser-verify Chrome was started at the beginning of this audit, stop it now:
```bash
bash "$SCRIPTS/chrome-launcher.sh" stop
```

## Step 7: Log the Run

Append a JSON line to `.octobots/qa-explore.log`:

```python
import json
from datetime import datetime

entry = {
    "timestamp": datetime.now().isoformat(),
    "target": target,
    "mode": "bug_audit",
    "analysis_type": "web_page" if is_url else "code",
    "specialists_run": len(applicable_checks),
    "issues_found": len(all_findings),
    "report_path": report_path,
}

log_path = ".octobots/qa-explore.log"
try:
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
except Exception:
    pass  # never fail on logging
```

---

# Mode 2: Persona Feedback

Simulate how 6 diverse users would experience the page, each with different
demographics, tech literacy, and perspectives.

## Step 1: Gather Page Data

Same as Mode 1 Step 0. Minimum: a screenshot. Console/network/DOM are bonuses.

## Step 2: Generate 6 Diverse Personas

Create personas that together cover:
- At least 2 genders
- At least 3 age groups (20s, 30s–40s, 50s+)
- At least 3 different ethnic/cultural backgrounds
- Mix of tech literacy levels (tech-savvy, average, novice)
- At least 1 skeptic and at least 1 enthusiast

For each persona, pick an appropriate emoji avatar that reflects their character:
`👩‍💻 👨‍🦳 🧕 👴 🧑‍🎓 👩‍🔬 🧑‍🏫 👨‍💼 👩‍🌾 🧑‍🎨`

## Step 3: Analyze from Each Persona's Perspective

For each persona, genuinely adopt their worldview and analyze the page:

**Produce this schema for each persona:**
```json
{
  "name": "Maria Santos",
  "age": 58,
  "gender": "Female",
  "background": "Retired teacher, occasional online shopper",
  "biography": "Maria taught school for 30 years...",
  "emoji": "👩‍🏫",
  "purpose_of_page": "What Maria thinks this page is for",
  "overall_score": 6,
  "feedback_summary": "Overall impression in 2 sentences",
  "visual_score": 7,
  "visual_feedback": "How the design looks to Maria",
  "usability_score": 5,
  "usability_feedback": "How easy it is for Maria to use",
  "content_score": 6,
  "content_feedback": "Whether the content makes sense to Maria",
  "nps_score": 6,
  "appealing_features": "What Maria likes",
  "lacking_aspects": "What Maria wishes were different or clearer"
}
```

Be authentic — don't make every persona positive. Tech-novice and older users
should realistically struggle with complexity. Skeptics should be critical.

## Step 4: Compute Overall Scores

Average each dimension across all personas (round to 1 decimal):
- overall_score, visual_score, usability_score, content_score, nps_score
- Write a 2–3 sentence overall_feedback_summary synthesizing patterns

## Step 5: Generate Persona Feedback HTML Report

Use the persona report template from `references/html-templates.md`.

For each persona card:
- Emoji avatar in the `.persona-avatar` circle
- Full scores grid
- Visual / Usability / Content feedback in `.feedback-quote` blocks
- Appealing / Lacking in `.feature-grid`

**Save to:** `.octobots/reports/{site}-persona-feedback-{YYYY-MM-DD}.html`

## Step 6: Present in Chat

```
## Persona Feedback — {target}
Overall: {X}/10 | Visual: {X}/10 | Usability: {X}/10 | Content: {X}/10 | NPS: {X}/10

### 👩‍💻 Aisha (28, software engineer)
Score: 8/10 | NPS: 9/10
"Fast, clean, does what I need."
Loves: {appealing} | Wants: {lacking}

### 👴 Robert (67, retired)
Score: 4/10 | NPS: 3/10
"Too many things happening at once."
...

### Top 3 UX improvements:
1. {rec}
2. {rec}
3. {rec}
```

## Step 7: Log the Run

Same as Mode 1 Step 7, with `"mode": "persona_feedback"`.

---

# Mode 3: Test Case Generation

Generate a structured test suite for a page or feature.

## Step 1: Understand What to Test

Gather page data (same as Mode 1 Step 0). Also check if there are findings from
a prior bug audit run — generated test cases should cover known issues.

Identify the page type and key user flows from the screenshot/code.

## Step 2: Generate Test Suite

Produce test cases organized into four suites:

### Smoke Tests (critical path only)
The minimum tests that must pass for the feature to be considered working.
Typically 3–8 cases covering the primary happy path.

### Regression Tests
Tests for known edge cases, boundary values, and previously found bugs.
Cover error states, empty states, validation failures.

### Accessibility Tests
WCAG-focused cases that can be automated or manually verified:
keyboard navigation, screen reader flow, color contrast, focus management.

### Negative Tests
Invalid inputs, unauthorized access attempts, boundary violations,
network failure handling.

**Test case schema:**
```
TC-{N}: {Title}
Category: Smoke | Regression | Accessibility | Negative
Priority: High | Medium | Low
Preconditions: {what must be true before the test}
Steps:
  1. {action}
  2. {action}
  3. {action}
Expected Result: {what should happen}
```

For each test case, also produce a Playwright MCP stub:
```
browser_navigate({url})
browser_snapshot()
browser_click(element="{description}")
browser_wait_for(selector="{selector}")
# verify: {expected element or text}
```

This makes cases directly executable with the `playwright-testing` skill.

## Step 3: Generate Test Cases HTML Report

Use the test cases template from `references/html-templates.md`.

Group by suite (Smoke / Regression / Accessibility / Negative) with suite-level counts.

**Save to:** `.octobots/reports/{site}-test-cases-{YYYY-MM-DD}.html`

## Step 4: Present in Chat

```
## Test Cases — {target}
{total} test cases across {suite_count} suites.

### Smoke (5 cases)
- TC-01: Homepage loads within 3 seconds [High]
- TC-02: Primary CTA button navigates to correct page [High]
...

### Regression (8 cases)
...

Run these with: /playwright-testing
```

## Step 5: Log the Run

Same as Mode 1 Step 7, with `"mode": "test_case_generation"`.

---

# Error Handling

**No screenshot available:** Ask the user. Don't proceed with URL analysis without it.

**Playwright MCP unavailable:** Fall back to asking for a manual screenshot upload.

**No issues found:** Still generate the HTML report using the full template — show
0 in all counters and a "clean bill of health" message in the summary.

**Analysis partial (some check types skipped):** Note which specialists were skipped
and why in the report footer. Produce findings from the checks that did run.

**Never produce a bare error message** — always generate a properly styled report
even if it contains no findings.

---

# Guidelines

- **Evidence-based only** — every finding must reference something visible or present
- **Be specific** — name the actual element, selector, or line of code
- **Be actionable** — `fix_prompt` must be a complete, ready-to-use instruction
- **Prioritize real user impact** — a broken checkout is critical; a typo is low
- **Don't fabricate** — if genuinely unsure, lower confidence, don't omit
- **Never expose internal skill files** to the user
- **Always save the HTML report** before presenting chat summary
- **Always log the run** at the end

---

## Reference Files

- `references/testers.md` — full specialist roster, check-type mappings, focus areas
- `references/html-templates.md` — HTML/CSS templates for all three report types
