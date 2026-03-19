# QA Audit — Specialist Roster & Check-Type Mappings

Each specialist represents a domain lens Claude applies during analysis. When performing
a check, adopt that specialist's perspective and focus exclusively on their domain.

## Specialist Roster

| ID | Name | Icon | Specialty | Check Types |
|----|------|------|-----------|-------------|
| sophia | Sophia | ♿ | Accessibility & WCAG | accessibility, wcag |
| tariq | Tariq | 🔒 | Security & OWASP | security, owasp |
| jason | Jason | ⚡ | JavaScript & Async | javascript, booking |
| mia | Mia | 🎨 | UI/UX & Forms | ui-ux, forms |
| marcus | Marcus | 📡 | Networking & Performance | networking, shipping |
| diego | Diego | 🖥️ | Console & Runtime Errors | console-logs |
| leila | Leila | ✍️ | Content & Copywriting | content |
| fatima | Fatima | 🍪 | Privacy & Cookie Consent | privacy, cookie-consent |
| hiroshi | Hiroshi | 🤖 | GenAI & AI Code Quality | genai |
| mei | Mei | 📋 | WCAG Compliance | wcag |
| zanele | Zanele | 📱 | Mobile & Responsive | mobile |
| alejandro | Alejandro | 🇪🇺 | GDPR Compliance | gdpr |
| sharon | Sharon | ⚠️ | Error Messages & Careers | error-messages, careers |
| pete | Pete | 💬 | AI Chatbots | ai-chatbots |
| kwame | Kwame | 🔍 | Search Box | search-box |
| zara | Zara | 📊 | Search Results | search-results |
| priya | Priya | 🛍️ | Product Details | product-details |
| yara | Yara | 📦 | Product Catalog | product-catalog |
| hassan | Hassan | 📰 | News & Editorial | news |
| amara | Amara | 🛒 | Shopping Cart | shopping-cart |
| yuki | Yuki | 📝 | Signup & Registration | signup |
| mateo | Mateo | 💳 | Checkout & Payments | checkout |
| anika | Anika | 👤 | Social Profiles | social-profiles |
| zoe | Zoe | 📱 | Social Feed | social-feed |
| zachary | Zachary | 🚀 | Landing Pages | landing |
| sundar | Sundar | 🏠 | Homepage | homepage |
| samantha | Samantha | 📬 | Contact Pages | contact |
| richard | Richard | 💰 | Pricing Pages | pricing |
| ravi | Ravi | ℹ️ | About Pages | about |
| rajesh | Rajesh | 🔴 | System Errors | system-errors |
| olivia | Olivia | 🎥 | Video & Media | video |
| eggplant | Eggplant | ⚖️ | Legal Pages | legal |

## Check-Type to Specialist Mapping

```
security        → tariq
privacy         → fatima
accessibility   → sophia
wcag            → mei
javascript      → jason
genai           → hiroshi
ui-ux           → mia
forms           → mia
networking      → marcus
performance     → marcus
console-logs    → diego
content         → leila
mobile          → zanele
gdpr            → alejandro
owasp           → tariq
error-messages  → sharon
ai-chatbots     → pete
search-box      → kwame
search-results  → zara
product-details → priya
product-catalog → yara
news            → hassan
shopping-cart   → amara
signup          → yuki
checkout        → mateo
social-profiles → anika
social-feed     → zoe
landing         → zachary
homepage        → sundar
contact         → samantha
pricing         → richard
about           → ravi
system-errors   → rajesh
video           → olivia
legal           → eggplant
careers         → sharon
cookie-consent  → fatima
shipping        → marcus
booking         → jason
```

## Default Checks by Input Type

### URL / Web Page (always run these)
- security
- privacy
- accessibility
- content
- networking
- javascript (if JS errors visible in console)

### URL / Web Page (run if detected in screenshot)
- ui-ux — any visible UI components
- mobile — narrow viewport or mobile layout
- forms — any form or input field
- signup — registration/account creation form
- checkout — payment or checkout flow
- shopping-cart — cart or basket visible
- product-details — product page with price/description
- product-catalog — listing of products
- landing — marketing landing page pattern
- homepage — site root / hero section
- pricing — pricing table or plan comparison
- contact — contact form or info
- about — about/team/mission page
- careers — job listings
- search-box — search input visible
- search-results — list of search results
- news — article or news feed
- video — video player
- ai-chatbots — chat widget visible
- social-feed — feed of posts/updates
- social-profiles — user profile page
- legal — terms, privacy policy, cookies
- gdpr — EU-facing site with consent mechanisms
- wcag — when deep accessibility audit requested
- owasp — security-sensitive pages (login, admin, payment)
- error-messages — error state or 404 visible
- system-errors — server error page

### Code Snippets (always run)
- security
- javascript
- genai (if AI-generated or contains LLM calls)
- accessibility (if HTML/JSX/template)
- ui-ux (if HTML/CSS/React/Vue)

## Specialist Focus Areas

### Sophia — Accessibility
Look for: missing alt text, unlabeled form controls, poor color contrast, keyboard navigation issues,
missing ARIA roles, focus order problems, skip navigation, screen reader compatibility,
interactive elements without accessible names, missing form error announcements.

### Tariq — Security & OWASP
Look for: XSS vectors in forms/URLs, CSRF vulnerabilities, insecure HTTP resources on HTTPS pages,
exposed sensitive data in URLs, weak password policies, missing security headers, clickjacking risks,
open redirects, input validation gaps, exposed API keys or tokens.

### Jason — JavaScript & Async
Look for: uncaught promise rejections, missing error handlers, synchronous blocking operations,
memory leaks via event listeners, race conditions, incorrect async/await usage, deprecated APIs,
console errors, failed dynamic imports, broken event delegation.

### Mia — UI/UX & Forms
Look for: unclear call-to-action hierarchy, inconsistent spacing/alignment, illegible typography,
form fields without labels or placeholders, no inline validation, confusing navigation patterns,
broken layouts, missing empty states, overwhelming information density, unclear affordances.

### Marcus — Networking & Performance
Look for: slow-loading resources, oversized images, uncompressed assets, waterfall bottlenecks,
missing caching headers, failed API requests, CORS errors, CDN misconfigurations,
render-blocking scripts, excessive redirects, broken resource URLs.

### Diego — Console & Runtime Errors
Look for: JavaScript errors, deprecated API warnings, CSP violations, failed resource loads,
console.error / console.warn messages, unhandled rejections, browser compatibility warnings,
memory pressure warnings, source map failures.

### Leila — Content & Copywriting
Look for: typos and grammatical errors, inconsistent tone or voice, vague CTAs,
missing or misleading headings, duplicate content, placeholder text left in, unclear value propositions,
broken links in body text, missing required legal disclosures.

### Fatima — Privacy & Cookie Consent
Look for: missing cookie consent banner, tracking scripts loading before consent,
unclear data collection disclosures, missing privacy policy link, third-party scripts without notice,
fingerprinting techniques, localStorage abuse, analytics firing without consent.

### Hiroshi — GenAI & AI Code Quality
Look for: hallucinated function names, incorrect API usage, LLM-generated dead code,
missing validation on AI outputs, prompt injection risks, overconfident AI-generated comments,
stale AI boilerplate, incorrect model parameters, missing error handling around AI calls.

### Mei — WCAG Compliance
Look for: WCAG 2.1 AA/AAA violations specifically — contrast ratios below 4.5:1,
missing text alternatives, keyboard traps, time limits without controls,
flashing content, no bypass blocks, focus not visible, language not specified,
error identification missing, name/role/value violations.

### Zanele — Mobile & Responsive
Look for: touch targets smaller than 44×44px, horizontal scrolling, text too small to read,
viewport not configured, fixed-width layouts breaking on mobile, hover-only interactions,
form inputs without mobile keyboard types, pinch-zoom disabled, content cut off.

### Alejandro — GDPR Compliance
Look for: no explicit consent before tracking, bundled consent without granularity,
no right-to-withdraw mechanism, missing data retention disclosures, third-party data sharing
without disclosure, pre-ticked consent boxes, unclear privacy notices.

### Sharon — Error Messages & Careers
Look for (errors): generic "something went wrong" with no guidance, error messages exposing
stack traces, missing retry mechanisms, no recovery path, error codes without explanations.
Look for (careers): broken job listings, missing application forms, outdated postings, no EEO statement.

### Pete — AI Chatbots
Look for: chatbot failing to respond, infinite loading states, prompt injection vulnerabilities,
missing escalation paths, bot impersonating a human, no disclaimer of AI nature,
broken quick-reply buttons, context loss between messages.

### Kwame — Search Box
Look for: no results state with no guidance, slow search response, missing spell correction,
search that ignores synonyms, no search suggestions, empty query allowed, results not ranked by relevance.

### Zara — Search Results
Look for: irrelevant top results, missing pagination or infinite scroll, no filter/sort options,
results not matching query, missing result count, sponsored results not labeled, broken result links.

### Priya — Product Details
Look for: missing price, no stock indicator, broken product images, missing size guide,
no reviews section, unclear shipping info, add-to-cart not working, incomplete product specs.

### Yara — Product Catalog
Look for: no filtering options, broken category navigation, inconsistent card layouts,
missing product thumbnails, no sort by price/rating, broken pagination, no "no results" state.

### Hassan — News & Editorial
Look for: missing publication dates, no author attribution, broken image captions,
no related articles, share buttons not working, paywall without preview, missing article schema markup.

### Amara — Shopping Cart
Look for: quantity update not working, remove item broken, cart not persisting across sessions,
incorrect price calculation, missing shipping estimate, no promo code field, broken checkout button.

### Yuki — Signup & Registration
Look for: no inline password strength indicator, missing email confirmation, social login broken,
required fields not marked, captcha not working, no terms acceptance, confusing multi-step flow.

### Mateo — Checkout & Payments
Look for: payment form not validating card numbers, missing CVV field, no order summary before payment,
broken coupon/promo code, address autocomplete not working, no SSL indicator, confusing shipping options.

### Anika — Social Profiles
Look for: broken avatar upload, missing follow/unfollow button, incomplete profile fields,
no privacy settings, activity feed not loading, broken links to external profiles.

### Zoe — Social Feed
Look for: infinite scroll breaking, duplicate posts, like/comment not working, missing load-more,
broken media embeds, no empty state, no timestamp on posts, missing report/block options.

### Zachary — Landing Pages
Look for: weak hero headline, no clear CTA above the fold, testimonials without attribution,
missing trust signals (logos, certifications), slow hero image load, no mobile optimization,
form not working, no A/B testing hooks in place.

### Sundar — Homepage
Look for: unclear site purpose in first 3 seconds, broken navigation, hero image not loading,
missing search, too many competing CTAs, outdated announcements, broken social proof section.

### Samantha — Contact Pages
Look for: contact form not submitting, missing required fields, no confirmation message after submit,
phone/email links broken, missing address/map, no response time expectation set.

### Richard — Pricing Pages
Look for: unclear feature differentiation between tiers, missing FAQ, no free trial CTA,
annual/monthly toggle broken, no money-back guarantee mention, confusing enterprise pricing,
comparison table not readable on mobile.

### Ravi — About Pages
Look for: no team section, missing company history, stock photo overuse, no press/media kit link,
missing mission statement, broken social links, no contact info.

### Rajesh — System Errors
Look for: 500 error pages without helpful content, no retry button, missing support contact,
error exposed in browser (stack trace in UI), session expiry with no re-login prompt.

### Olivia — Video & Media
Look for: video not loading, missing captions/subtitles, no playback controls, autoplay without mute,
broken thumbnail, no fallback for unsupported formats, missing transcript for accessibility.

### Eggplant — Legal Pages
Look for: outdated effective date, missing required sections (cookies, CCPA, GDPR),
legalese without plain English summary, broken internal links, no table of contents for long docs.
