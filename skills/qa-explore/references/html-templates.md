# QA Audit — HTML Report Templates

## Shared CSS

```css
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: linear-gradient(135deg, #0a1f0f 0%, #0d2818 100%);
    color: #e8f5e9;
    padding: 20px;
    line-height: 1.6;
    min-height: 100vh;
}
.container { max-width: 1200px; margin: 0 auto; }
.branding { text-align: center; margin-bottom: 20px; padding: 20px; }
.branding h1 { color: #4ade80; font-size: 2em; margin: 0; }
.branding p { opacity: 0.6; font-size: 0.9em; margin-top: 4px; }
header {
    background: linear-gradient(135deg, #1a4d2e 0%, #0d2818 100%);
    padding: 40px;
    border-radius: 12px;
    margin-bottom: 30px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.3);
}
h1 {
    font-size: 2.2em;
    margin-bottom: 10px;
    background: linear-gradient(45deg, #4ade80, #22c55e);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
h2 { color: #4ade80; margin-bottom: 15px; }
.meta-info {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 20px;
    margin: 20px 0;
}
.meta-card {
    background: rgba(255,255,255,0.05);
    padding: 15px;
    border-radius: 8px;
    border-left: 4px solid #4ade80;
}
.summary, .section {
    background: #0d2818;
    padding: 30px;
    border-radius: 12px;
    margin-bottom: 30px;
    box-shadow: 0 5px 20px rgba(0,0,0,0.2);
}
.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 20px;
    margin-top: 20px;
}
.stat-box {
    background: rgba(255,255,255,0.05);
    padding: 20px;
    border-radius: 8px;
    text-align: center;
}
.stat-number { font-size: 2.4em; font-weight: bold; color: #4ade80; }
.specialists-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 15px;
    margin-top: 20px;
}
.specialist-card {
    background: rgba(255,255,255,0.05);
    padding: 12px 15px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    gap: 12px;
}
.specialist-icon {
    font-size: 1.8em;
    width: 40px;
    text-align: center;
}
.specialist-info h3 { font-size: 0.95em; color: #4ade80; margin-bottom: 2px; }
.specialist-info p { font-size: 0.8em; opacity: 0.7; }
.bug-card {
    background: #0d2818;
    padding: 25px;
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 5px 20px rgba(0,0,0,0.2);
    border-left: 6px solid;
}
.bug-card.priority-critical { border-left-color: #ff4757; }
.bug-card.priority-high     { border-left-color: #ffa502; }
.bug-card.priority-medium   { border-left-color: #ffd32a; }
.bug-card.priority-low      { border-left-color: #3498db; }
.bug-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 15px;
}
.bug-title { font-size: 1.3em; flex: 1; color: #fff; }
.bug-badges { display: flex; gap: 8px; flex-wrap: wrap; }
.badge {
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 0.8em;
    font-weight: 600;
}
.badge-critical  { background: #ff4757; color: #fff; }
.badge-high      { background: #ffa502; color: #fff; }
.badge-medium    { background: #ffd32a; color: #333; }
.badge-low       { background: #3498db; color: #fff; }
.badge-conf      { background: rgba(255,255,255,0.12); color: #a5d6a7; }
.badge-category  { background: rgba(74,222,128,0.15); color: #4ade80; }
.bug-specialist {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 12px 0;
    padding: 10px;
    background: rgba(255,255,255,0.04);
    border-radius: 8px;
}
.bug-specialist .icon { font-size: 1.5em; }
.bug-section { margin: 12px 0; }
.bug-section h4 { color: #4ade80; margin-bottom: 6px; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em; }
.bug-section p { color: #c8e6c9; line-height: 1.7; }
.fix-prompt {
    background: rgba(0,0,0,0.35);
    padding: 14px;
    border-radius: 6px;
    overflow-x: auto;
    color: #4ade80;
    border: 1px solid rgba(74,222,128,0.25);
    font-family: 'Courier New', monospace;
    font-size: 0.88em;
    line-height: 1.5;
    white-space: pre-wrap;
    word-wrap: break-word;
}
.recommendations {
    background: #0d2818;
    padding: 30px;
    border-radius: 12px;
    margin-top: 30px;
}
.recommendations ol { margin-left: 20px; margin-top: 12px; }
.recommendations li { margin: 10px 0; color: #c8e6c9; padding-left: 8px; }
footer {
    text-align: center;
    margin-top: 50px;
    opacity: 0.5;
    font-size: 0.85em;
    padding: 20px;
}
@media (max-width: 768px) {
    .bug-header { flex-direction: column; }
    h1 { font-size: 1.6em; }
    header { padding: 20px; }
}
@media print {
    body { background: #0a1f0f; }
    .bug-card, .persona-card { page-break-inside: avoid; }
}
```

---

## Bug Audit Report Template

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QA Audit — {SITE_NAME}</title>
    <style>
        /* [paste shared CSS here] */

        /* Persona additions */
        .persona-card {
            background: #0d2818;
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 30px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.2);
            border-left: 6px solid #4ade80;
        }
        .persona-header {
            display: flex;
            align-items: center;
            gap: 20px;
            margin-bottom: 20px;
            padding-bottom: 20px;
            border-bottom: 1px solid rgba(74,222,128,0.15);
        }
        .persona-avatar {
            width: 72px;
            height: 72px;
            border-radius: 50%;
            background: linear-gradient(135deg, #1a4d2e, #4ade80);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 2em;
            flex-shrink: 0;
        }
        .persona-info h2 { color: #4ade80; margin: 0 0 6px 0; }
        .persona-subtitle { opacity: 0.75; font-size: 0.9em; }
        .scores-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 15px;
            margin: 15px 0;
        }
        .score-card {
            background: rgba(255,255,255,0.05);
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }
        .score-number { font-size: 2em; font-weight: bold; color: #4ade80; }
        .feedback-quote {
            background: rgba(255,255,255,0.04);
            padding: 12px 15px;
            border-radius: 8px;
            border-left: 3px solid #4ade80;
            font-style: italic;
            margin: 8px 0;
            color: #c8e6c9;
        }
        .feature-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-top: 15px;
        }
        .feature-box {
            background: rgba(255,255,255,0.04);
            padding: 12px 15px;
            border-radius: 8px;
        }
        .feature-box h4 { color: #4ade80; margin-bottom: 6px; font-size: 0.9em; }
        @media (max-width: 600px) { .feature-grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
<div class="container">

    <div class="branding">
        <h1>🤖 Octobots QA Audit</h1>
        <p>Multi-Specialist Analysis</p>
    </div>

    <header>
        <h1>Bug Audit Report</h1>
        <div class="meta-info">
            <div class="meta-card"><strong>Target</strong><br>{TARGET}</div>
            <div class="meta-card"><strong>Date</strong><br>{DATE}</div>
            <div class="meta-card"><strong>Specialists</strong><br>{SPECIALIST_COUNT} active</div>
            <div class="meta-card"><strong>Analysis</strong><br>{ANALYSIS_TYPE}</div>
        </div>
    </header>

    <div class="summary">
        <h2>Summary</h2>
        <div class="stats-grid">
            <div class="stat-box">
                <div class="stat-number">{TOTAL}</div>
                <div>Total Issues</div>
            </div>
            <div class="stat-box">
                <div class="stat-number" style="color:#ff4757">{CRITICAL}</div>
                <div>Critical (8–10)</div>
            </div>
            <div class="stat-box">
                <div class="stat-number" style="color:#ffa502">{HIGH}</div>
                <div>High (5–7)</div>
            </div>
            <div class="stat-box">
                <div class="stat-number" style="color:#ffd32a">{MEDIUM}</div>
                <div>Medium (4)</div>
            </div>
            <div class="stat-box">
                <div class="stat-number" style="color:#3498db">{LOW}</div>
                <div>Low (1–3)</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Specialists Who Participated</h2>
        <div class="specialists-grid">
            <!-- For each specialist who found issues: -->
            <div class="specialist-card">
                <div class="specialist-icon">{ICON}</div>
                <div class="specialist-info">
                    <h3>{NAME}</h3>
                    <p>{SPECIALTY}</p>
                    <p><strong>{COUNT}</strong> issues</p>
                </div>
            </div>
        </div>
    </div>

    <h2 style="margin-bottom:20px">Issues Found</h2>

    <!-- For each bug, sorted by priority desc: -->
    <div class="bug-card priority-{LEVEL}">
        <div class="bug-header">
            <h3 class="bug-title">{N}. {TITLE}</h3>
            <div class="bug-badges">
                <span class="badge badge-{LEVEL}">Priority {PRIORITY}/10</span>
                <span class="badge badge-conf">Confidence {CONFIDENCE}/10</span>
                <!-- for each type: -->
                <span class="badge badge-category">{TYPE}</span>
            </div>
        </div>

        <div class="bug-specialist">
            <span class="icon">{SPECIALIST_ICON}</span>
            <div>
                <strong>{SPECIALIST_NAME}</strong>
                <br><span style="opacity:0.65;font-size:0.9em">{SPECIALIST_SPECIALTY}</span>
            </div>
        </div>

        <div class="bug-section">
            <h4>Why this is a bug</h4>
            <p>{REASONING}</p>
        </div>
        <div class="bug-section">
            <h4>Suggested fix</h4>
            <p>{FIX}</p>
        </div>
        <div class="bug-section">
            <h4>Fix Prompt</h4>
            <pre class="fix-prompt">{FIX_PROMPT}</pre>
        </div>
    </div>
    <!-- end bug loop -->

    <div class="recommendations">
        <h2>Top Recommendations</h2>
        <ol>
            <li>{REC_1}</li>
            <li>{REC_2}</li>
            <li>{REC_3}</li>
        </ol>
    </div>

    <footer>
        Generated by Octobots QA Audit &bull; {DATE}
    </footer>

</div>
</body>
</html>
```

---

## Persona Feedback Report Template

Same `<head>` and shared CSS, plus the persona-specific additions above.

```html
<!-- header section -->
<header>
    <h1>{SITE_NAME} — Persona Feedback</h1>
    <div class="meta-info">
        <div class="meta-card"><strong>Target</strong><br>{TARGET}</div>
        <div class="meta-card"><strong>Date</strong><br>{DATE}</div>
        <div class="meta-card"><strong>Overall Score</strong><br>{OVERALL}/10</div>
        <div class="meta-card"><strong>NPS</strong><br>{NPS}/10</div>
    </div>
</header>

<!-- overall summary -->
<div class="summary">
    <h2>Overall Assessment</h2>
    <p><strong>Page purpose:</strong> {PURPOSE}</p>
    <p style="margin-top:8px">{SUMMARY}</p>
    <div class="scores-grid" style="margin-top:20px">
        <div class="score-card">
            <div class="score-number">{OVERALL}</div><div>Overall</div>
        </div>
        <div class="score-card">
            <div class="score-number">{VISUAL}</div><div>Visual Design</div>
        </div>
        <div class="score-card">
            <div class="score-number">{USABILITY}</div><div>Usability</div>
        </div>
        <div class="score-card">
            <div class="score-number">{CONTENT}</div><div>Content</div>
        </div>
    </div>
</div>

<!-- for each persona -->
<div class="persona-card">
    <div class="persona-header">
        <div class="persona-avatar">{EMOJI}</div>
        <div class="persona-info">
            <h2>{NAME}</h2>
            <p class="persona-subtitle">{AGE}, {GENDER} &bull; {BACKGROUND}</p>
            <p style="margin-top:6px;opacity:0.8">{BIOGRAPHY}</p>
        </div>
    </div>

    <div class="scores-grid">
        <div class="score-card">
            <div class="score-number">{SCORE}</div><div>Overall</div>
        </div>
        <div class="score-card">
            <div class="score-number">{VISUAL_SCORE}</div><div>Visual</div>
        </div>
        <div class="score-card">
            <div class="score-number">{USABILITY_SCORE}</div><div>Usability</div>
        </div>
        <div class="score-card">
            <div class="score-number">{CONTENT_SCORE}</div><div>Content</div>
        </div>
        <div class="score-card">
            <div class="score-number">{NPS_SCORE}</div><div>NPS</div>
        </div>
    </div>

    <div class="bug-section">
        <h4>Visual Design</h4>
        <div class="feedback-quote">{VISUAL_FEEDBACK}</div>
    </div>
    <div class="bug-section">
        <h4>Usability</h4>
        <div class="feedback-quote">{USABILITY_FEEDBACK}</div>
    </div>
    <div class="bug-section">
        <h4>Content Relevance</h4>
        <div class="feedback-quote">{CONTENT_FEEDBACK}</div>
    </div>

    <div class="feature-grid">
        <div class="feature-box">
            <h4>✅ Appealing</h4>
            <p>{APPEALING}</p>
        </div>
        <div class="feature-box">
            <h4>❌ Missing / Lacking</h4>
            <p>{LACKING}</p>
        </div>
    </div>
</div>
<!-- end persona loop -->
```

---

## Test Cases Report Template

```html
<!-- header -->
<header>
    <h1>Test Cases — {SITE_NAME}</h1>
    <div class="meta-info">
        <div class="meta-card"><strong>Target</strong><br>{TARGET}</div>
        <div class="meta-card"><strong>Date</strong><br>{DATE}</div>
        <div class="meta-card"><strong>Total Cases</strong><br>{COUNT}</div>
        <div class="meta-card"><strong>Type</strong><br>{SUITE_TYPE}</div>
    </div>
</header>

<!-- for each test suite (Smoke / Regression / Accessibility / Negative) -->
<div class="section">
    <h2>{SUITE_NAME} Tests ({SUITE_COUNT})</h2>

    <!-- for each test case -->
    <div class="bug-card priority-low" style="border-left-color:#4ade80">
        <div class="bug-header">
            <h3 class="bug-title">TC-{N}: {TEST_TITLE}</h3>
            <div class="bug-badges">
                <span class="badge badge-category">{CATEGORY}</span>
                <span class="badge badge-conf">{PRIORITY}</span>
            </div>
        </div>
        <div class="bug-section">
            <h4>Preconditions</h4>
            <p>{PRECONDITIONS}</p>
        </div>
        <div class="bug-section">
            <h4>Steps</h4>
            <pre class="fix-prompt">{STEPS}</pre>
        </div>
        <div class="bug-section">
            <h4>Expected Result</h4>
            <p>{EXPECTED}</p>
        </div>
    </div>
</div>
```

---

## Priority Level Mapping

| Priority score | CSS class | Badge class | Color |
|---|---|---|---|
| 8–10 | `priority-critical` | `badge-critical` | `#ff4757` red |
| 5–7 | `priority-high` | `badge-high` | `#ffa502` orange |
| 4 | `priority-medium` | `badge-medium` | `#ffd32a` yellow |
| 1–3 | `priority-low` | `badge-low` | `#3498db` blue |

## Report File Naming

- Bug audit: `.octobots/reports/{site}-bug-audit-{YYYY-MM-DD}.html`
- Persona feedback: `.octobots/reports/{site}-persona-feedback-{YYYY-MM-DD}.html`
- Test cases: `.octobots/reports/{site}-test-cases-{YYYY-MM-DD}.html`
