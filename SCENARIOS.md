# OpenSculpt — Real-World Scenarios

OpenSculpt is validated through real-world operational scenarios.  
Each scenario must pass four phases:

**Setup → Ingestion → Operation → Evolution**

---

# Priority 1 (Demo-Ready)

## 1. Sales CRM Operator

**Ask**:  
"Set up a sales system. Capture leads, track deals, follow up automatically."

**Setup**:  
- Deploy CRM (SuiteCRM / EspoCRM via Docker)  
- Configure pipelines, deal stages, contacts  
- Set up email + automation workflows  

**Ingestion**:  
- Import leads, contacts, historical deals  
- Sync email conversations and form submissions  

**Operation**:  
- Track deals across pipeline  
- Automate follow-ups and reminders  
- Generate sales reports  

**Evolution**:  
- Learn deal success patterns  
- Improve lead scoring  
- Detect stalled deals and suggest actions  

---

## 2. Customer Support Manager

**Ask**:  
"Handle customer support. Set up ticketing, classify issues, auto-reply."

**Setup**:  
- Deploy helpdesk (Zammad / osTicket)  
- Connect email, chat, support channels  
- Define ticket categories and SLAs  

**Ingestion**:  
- Import past tickets and conversations  
- Sync incoming support requests  

**Operation**:  
- Classify and route tickets  
- Generate replies and suggestions  
- Track resolution times  

**Evolution**:  
- Learn issue patterns  
- Build knowledge base automatically  
- Predict churn risk  

---

## 3. Internal Knowledge System

**Ask**:  
"Build a knowledge system from documents, notes, PDFs, emails."

**Setup**:  
- Ingest documents and data sources  
- Generate embeddings and semantic index  
- Configure search and retrieval  

**Ingestion**:  
- Import PDFs, docs, emails, notes  
- Extract entities and relationships  

**Operation**:  
- Answer internal queries  
- Retrieve relevant knowledge  
- Maintain document structure  

**Evolution**:  
- Merge duplicates and refine concepts  
- Build knowledge graph  
- Improve retrieval accuracy  

---

## 4. DevOps and Deployment Operator

**Ask**:  
"Set up CI/CD. Build, test, deploy, monitor, recover."

**Setup**:  
- Configure repositories and pipelines  
- Deploy CI/CD tools (GitHub Actions, GitLab CI)  
- Set up monitoring and alerting  

**Ingestion**:  
- Import code repositories  
- Collect logs, metrics, deployment history  

**Operation**:  
- Build, test, deploy automatically  
- Monitor systems and uptime  
- Trigger rollbacks on failure  

**Evolution**:  
- Learn safe deployment patterns  
- Predict risky changes  
- Improve recovery strategies  

---

## 5. Company-in-a-Box

**Ask**:  
"Run operations for my startup. Sales, support, marketing, finance connected."

**Setup**:  
- Deploy multiple systems (CRM, support, finance, marketing)  
- Connect data flows between systems  
- Create unified dashboard  

**Ingestion**:  
- Sync all business data across systems  

**Operation**:  
- Coordinate workflows across departments  
- Maintain unified reporting  
- Trigger cross-functional automations  

**Evolution**:  
- Learn dependencies across teams  
- Optimize workflows globally  
- Coordinate sub-agents intelligently  

---

# Priority 2

## 6. Marketing Campaign Operator

**Ask**:
"Plan and run marketing campaigns. Create content, track performance, optimize spend."

**Setup**:
- Find and deploy best available marketing automation tool for this environment
- Connect analytics and ad platform APIs
- Create audience segments and campaign templates

**Ingestion**:
- Import customer lists and past campaign data
- Pull engagement metrics (opens, clicks, conversions)
- Sync email and social performance history

**Operation**:
- Launch email drip campaigns with A/B testing
- Generate content from templates and briefs
- Monitor CTR, conversion rates, cost-per-acquisition
- Pause underperforming campaigns automatically

**Evolution**:
- Learn which subject lines and content types convert best
- Predict campaign ROI before launch
- Build reusable playbooks: "Product Launch", "Re-engagement", "Seasonal Sale"
- Auto-adjust timing based on audience patterns

---

## 7. E-commerce Store Builder

**Ask**:
"Create and run an online store. Manage products, orders, payments."

**Setup**:
- Find and deploy best available e-commerce platform for this environment
- Configure payment processing and shipping
- Deploy storefront

**Ingestion**:
- Import product catalog (name, price, SKU, images, descriptions)
- Import customer accounts and order history
- Sync inventory levels

**Operation**:
- Process orders: payment → fulfillment → shipping → tracking
- Send confirmation and shipping notification emails
- Auto-update inventory on sale and restock
- Generate daily sales reports

**Evolution**:
- Predict demand by product and season
- Optimize pricing from competitor and conversion data
- Recommend product bundles from co-purchase patterns
- Detect abandoned carts → trigger recovery emails

---

## 8. Accounting and Finance Assistant

**Ask**:
"Manage finances. Track expenses, invoices, generate tax reports."

**Setup**:
- Find and deploy accounting system suitable for this environment
- Configure chart of accounts, tax rates, fiscal year
- Connect bank feed import

**Ingestion**:
- Import bank transactions from files or API
- Import vendor invoices (extract line items from PDFs)
- Import customer invoices and payment history

**Operation**:
- Auto-categorize transactions (food, travel, software, payroll)
- Generate monthly P&L, balance sheet, cash flow statements
- Send invoice reminders for overdue payments
- Flag unusual transactions for review

**Evolution**:
- Predict cash flow 30/60/90 days ahead
- Detect anomalies: duplicate invoices, unexpected charges
- Learn user's categorization preferences over time
- Suggest tax optimization opportunities

---

## 9. HR and Hiring Coordinator

**Ask**:
"Manage hiring pipeline. Post jobs, screen resumes, schedule interviews, onboard new hires."

**Setup**:
- Find and deploy HR/ATS system for this environment
- Configure job templates, interview stages, evaluation criteria
- Connect calendar for interview scheduling

**Ingestion**:
- Import existing employee database
- Import resumes from job boards or email
- Parse resumes → extract skills, experience, education

**Operation**:
- Post jobs to multiple boards from one template
- Screen resumes: score candidates against requirements
- Schedule interviews: find mutual availability, send invites
- Onboarding checklist: IT setup, docs, training

**Evolution**:
- Learn which candidate traits predict success
- Detect bias in screening and flag it
- Predict time-to-hire per role
- Suggest interview questions based on role and candidate

---

## 10. Business Analytics Operator

**Ask**:
"Analyze my business data. Build dashboards, find insights, recommend decisions."

**Setup**:
- Find and deploy BI/analytics tool for this environment
- Connect data sources (databases, APIs, files)
- Create initial dashboards: revenue, users, operations

**Ingestion**:
- Import historical data from spreadsheets and databases
- Set up scheduled data syncs
- Clean and normalize data across sources

**Operation**:
- Auto-generate weekly business reports (KPIs, trends, anomalies)
- Answer ad-hoc questions: "What was our best month?"
- Alert on metric changes: revenue drops, churn spikes

**Evolution**:
- Improve forecasting from feedback (actual vs predicted)
- Learn which metrics the user checks most → prioritize
- Detect correlations across business areas
- Recommend actions based on data patterns

---

# Priority 3

## 11. Cybersecurity Defense Agent

**Ask**:
"Secure my systems. Monitor for threats, detect intrusions, respond automatically."

**Setup**:
- Find and deploy security monitoring tools available in this environment
- Configure detection rules: brute force, file integrity, suspicious access
- Set up threat intelligence feeds

**Ingestion**:
- Collect system logs, auth logs, application logs
- Import IP reputation and vulnerability databases
- Aggregate logs from all monitored services

**Operation**:
- Real-time log analysis: detect attack patterns
- Auto-block attacking IPs
- Alert on file system changes to critical paths
- Generate daily security posture reports

**Evolution**:
- Learn normal patterns per host → reduce false positives
- Predict attack vectors from correlated signals
- Auto-harden: suggest patches, disable unused services
- Learn from incidents to strengthen defenses

---

## 12. Social Media Operations Agent

**Ask**:
"Manage all my social media. Schedule posts, respond to comments, track what works."

**Setup**:
- Connect available social media platform APIs
- Configure posting schedule per platform
- Define brand voice guidelines and content pillars

**Ingestion**:
- Import existing content library
- Pull historical engagement data
- Monitor competitor accounts for trends

**Operation**:
- Generate platform-specific content from a single brief
- Schedule posts with optimal timing per platform
- Monitor mentions → draft replies for approval
- Weekly engagement reports

**Evolution**:
- Learn which content types perform best per platform
- Predict engagement before posting
- Identify trending topics → suggest timely posts
- A/B test captions and learn from results

---

## 13. Legal Workflow Assistant

**Ask**:
"Manage contracts and compliance. Track deadlines, review documents, handle approvals."

**Setup**:
- Set up document management and contract tracking
- Configure contract templates (NDA, MSA, SOW)
- Set up approval workflows (draft → review → sign → archive)

**Ingestion**:
- Import existing contracts
- Extract key terms: parties, dates, amounts, renewal clauses
- Build searchable contract database

**Operation**:
- Alert before contract renewals and expirations
- Generate drafts from templates
- Track approval status and send reminders
- Maintain compliance checklists

**Evolution**:
- Learn which clauses get negotiated most → suggest alternatives
- Detect risky terms across portfolio
- Predict contract cycle time
- Build clause library from signed contracts

---

## 14. Real Estate Property Manager

**Ask**:
"Manage my rental properties. Track tenants, collect rent, handle maintenance."

**Setup**:
- Set up property management system
- Configure properties: units, rent amounts, lease terms
- Set up payment collection

**Ingestion**:
- Import property portfolio
- Import tenant data and lease history
- Import maintenance history and vendor contacts

**Operation**:
- Auto-send rent reminders before due date
- Track payments: received, late, fees
- Route maintenance requests: submit → categorize → assign vendor → track
- Monthly P&L per property

**Evolution**:
- Predict vacancy risk from lease expiry + market conditions
- Optimize rent pricing from comparable listings
- Learn maintenance patterns → schedule preventive work
- Predict tenant churn from payment and request patterns

---

## 15. Healthcare Administration Assistant

**Ask**:
"Manage my clinic operations. Schedule patients, handle billing, maintain records."

**Setup**:
- Find and deploy EHR/practice management system
- Configure providers, services, fee schedule
- Set up appointment types and scheduling rules

**Ingestion**:
- Import patient demographics
- Import insurance plans and coverage
- Import appointment and billing history

**Operation**:
- Online scheduling with confirmation and reminders
- Check-in: verify insurance, collect copay, update records
- Generate claims and submit electronically
- Track claim lifecycle: submitted → accepted → paid

**Evolution**:
- Predict no-shows → overbook intelligently
- Improve billing accuracy: flag coding errors before submission
- Learn demand patterns → suggest staffing
- Detect documentation gaps → remind providers

---

# Priority 4 — Individual User Scenarios

OpenSculpt isn't just for businesses. A single person should get value from day one.

## 16. Personal File Organizer

**Ask**:
"Organize my messy files. Sort Downloads, Documents, Desktop into a clean structure."

**Setup**:
- Scan target directories
- Classify files by type, content, project, date
- Create organized folder structure

**Ingestion**:
- Read file metadata (name, type, size, dates)
- For documents: detect topic and language
- For images: detect duplicates via hash
- For code: detect language and project

**Operation**:
- Move files into organized folders
- Rename with consistent naming convention
- Detect and merge duplicates
- Report what was moved and why

**Evolution**:
- Learn user's filing preferences from corrections
- Watch for new downloads → auto-sort in real-time
- Suggest archive/delete for unused files
- Compress old projects

---

## 17. Personal Finance Tracker

**Ask**:
"Track my spending. Show me where my money goes, help me budget."

**Setup**:
- Create local transaction database
- Define categories: rent, food, transport, entertainment, subscriptions
- Set monthly budget targets

**Ingestion**:
- Import bank statements from CSV
- Parse transactions → extract merchant, amount, date
- Import credit card statements

**Operation**:
- Auto-categorize every transaction
- Daily spending summary
- Weekly budget check: "80% of food budget spent with 10 days left"
- Monthly report: spending by category, month-over-month comparison

**Evolution**:
- Learn categorization from user corrections
- Predict month-end balance from spending velocity
- Detect forgotten subscriptions
- Suggest savings opportunities

---

## 18. Research and Learning Assistant

**Ask**:
"Help me research a topic. Find sources, summarize them, build a knowledge base I can query."

**Setup**:
- Configure research topic and scope
- Set up knowledge base for the domain
- Connect sources: academic APIs, web, local PDFs

**Ingestion**:
- Search and collect relevant papers/articles
- Extract key findings, methods, conclusions
- Build citation and concept graph

**Operation**:
- Answer questions from knowledge base
- Generate literature review summaries
- Track new publications (daily/weekly digest)
- Create study notes from ingested material

**Evolution**:
- Learn which subtopics the user cares about → prioritize
- Detect emerging trends in the field
- Connect ideas across sources
- Refine search based on what user found useful

---

## 19. Personal Life Operator

**Ask**:
"Help me stay on top of my life. Track habits, manage todos, remind me of important things."

**Setup**:
- Create local task and habit database
- Configure daily routine and goals
- Import calendar events and contacts

**Ingestion**:
- Import existing todos from files or exports
- Import contacts with birthdays and important dates
- Import calendar subscriptions

**Operation**:
- Morning briefing: meetings, todos, reminders
- Habit tracking with streaks
- Smart reminders: "Haven't called Dad in 3 weeks", "Insurance renews in 10 days"
- Weekly review: done, overdue, upcoming

**Evolution**:
- Learn productivity patterns → schedule deep work optimally
- Predict procrastination → nudge earlier
- Detect habit drift and suggest adjustments
- Learn priority patterns from user behavior

---

## 20. Data Pipeline Builder

**Ask**:
"Build ETL pipelines. Pull data from APIs, transform it, load into my database."

**Setup**:
- Find and deploy pipeline orchestration tool for this environment
- Configure source connections (APIs, databases, files)
- Configure target database

**Ingestion**:
- Define extraction jobs per source
- Pull sample data and infer schema
- Create staging tables

**Operation**:
- Run pipelines on schedule
- Transform: clean, deduplicate, join, aggregate
- Load with upsert logic
- Alert on failures or data quality issues

**Evolution**:
- Detect upstream schema changes → auto-adjust
- Predict runtime from data volume trends
- Optimize query performance
- Learn data quality rules from corrections

---

# What "Evolution" Means

1. **Tool selection** — discovers and picks the best tools available in the environment
2. **Schema adaptation** — learns how each user/business structures their data
3. **Workflow composition** — combines tools into reusable patterns
4. **Memory and continuity** — remembers decisions, failures, preferences across sessions
5. **Self-maintenance** — updates, repairs, adapts when things break
6. **Policy-aware autonomy** — acts within permissions and safety boundaries
7. **Domain specialization** — a sales OS thinks differently from a personal finance tracker

---

# Verification Philosophy

The OS must never lie about what it accomplished. Three tiers:

| Tier | Meaning | Dashboard |
|------|---------|-----------|
| **Verified** | A concrete check proved it works (service responds, data exists, files organized) | ✓ green |
| **Completed unverified** | Sub-agent finished but no concrete proof available | ⚠ yellow |
| **Failed** | Concrete check proved it didn't work, or sub-agent errored | ✗ red |

**The LLM plans the verification at the same time as the phase** — before doing the work, based on what it discovers about the environment. It writes a check command that's appropriate for THIS machine (might be Docker, might be systemd, might be just files). At verification time, the command runs mechanically — the LLM never judges its own output.

---

# Testing Framework

These scenarios are used to test OpenSculpt on **any customer machine** — Windows, Mac, Linux, with or without Docker, with or without cloud access. The OS discovers the environment and adapts.

Each scenario must pass:

- **Phase 1 — Setup**: Can it find and configure the right tools for this environment?
- **Phase 2 — Ingestion**: Can it bring in data from whatever sources are available?
- **Phase 3 — Operation**: Can it maintain workflows reliably?
- **Phase 4 — Evolution**: Can it improve based on what it learned?

Every phase has a verification. No green ticks without proof.

---

**Goal:**
If OpenSculpt succeeds across these 20 scenarios — from enterprise CRM to personal file organization, on any machine — it becomes the first general-purpose agentic operating system that actually works for real people.