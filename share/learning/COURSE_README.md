# Paper-Trading Automation Course Package

This folder contains the initial planning and curriculum documents for turning the Autonomous Futures System into a paper-trading automation course.

## Start Here

### 1. Course Overview

Read [COURSE_OVERVIEW.md](COURSE_OVERVIEW.md) first.

This is the short, shareable introduction for potential students, collaborators, or customers. It explains:

- What students build
- Who the course is for
- Expected outcomes
- Course tracks
- Technology stack
- Estimated costs
- Capstone requirements

### 2. Detailed Syllabus

Read [COURSE_SYLLABUS.md](COURSE_SYLLABUS.md) for the complete curriculum.

It includes:

- Module-by-module lessons
- Estimated time
- Assignments
- Deliverables
- Completion checks
- Capstone evaluation criteria

### 3. Stack and Build Options

Read [COURSE_BUILD_STACK_OPTIONS.md](COURSE_BUILD_STACK_OPTIONS.md) for the technical and product breakdown.

It explains:

- The current system architecture
- Alternative technologies
- Hosting, storage, broker, dashboard, and notification options
- Approximate operating costs
- Recommended build path
- Possible product formats

---

## Course Promise

Students build a working, paper-only trading operations system that:

- Receives authenticated market alerts
- Validates incoming data
- Produces deterministic strategy decisions
- Independently enforces risk rules
- Simulates bracket orders
- Journals every decision
- Replays historical sessions
- Sends notifications
- Displays operational status
- Runs locally or on a cloud server

The course does not promise profitability and does not require live trading.

---

## Recommended Course Tracks

| Track | Student Goal |
|---|---|
| Operator | Configure, test, deploy, and operate the supplied system |
| Builder | Implement and understand the system module by module |
| Advanced | Extend the system with databases, frontend apps, AI review, or broker simulation |

---

## Course Packaging Checklist

### Curriculum

- [x] One-page course overview
- [x] Detailed course syllabus
- [x] Stack, cost, and build-options guide
- [ ] Lesson scripts
- [ ] Assignment instructions
- [ ] Instructor answer keys
- [ ] Knowledge checks and quizzes

### Repository

- [ ] Create clean student starter branch or repository
- [ ] Create completed instructor branch or repository
- [ ] Remove private logs, credentials, and personal data
- [ ] Replace personal strategy details with course-safe examples
- [ ] Add setup automation
- [ ] Add student-friendly error messages
- [ ] Add tagged checkpoints for each module

### Learning Assets

- [ ] Architecture diagram image
- [ ] Alert-payload worksheet
- [ ] Risk-policy worksheet
- [ ] Replay datasets
- [ ] Expected replay reports
- [ ] Dashboard screenshots
- [ ] Deployment checklist
- [ ] Troubleshooting guide

### Publishing

- [ ] Record a short end-to-end demo
- [ ] Record module lessons
- [ ] Choose course platform
- [ ] Set pricing and support model
- [ ] Create sales-page copy
- [ ] Review educational and financial-risk disclaimers
- [ ] Run a small beta cohort

---

## Recommended Next Build Step

Create a sanitized student starter repository.

The starter version should include:

- Project skeleton
- Sample market-state payloads
- Paper-only safety configuration
- Failing or incomplete module tests
- Guided TODOs
- Replay fixtures
- Setup and troubleshooting instructions

The completed instructor version should include the full working implementation, answer keys, completed tests, and module checkpoint tags.

