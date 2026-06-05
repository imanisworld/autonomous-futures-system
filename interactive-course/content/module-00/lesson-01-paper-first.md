---
id: lesson-01-paper-first
module: module-00
title: Paper First
estimated_minutes: 20
interaction: worksheet
required: true
deliverable: paper-only-safety-policy
---

# Paper First

## Why It Matters

Automation repeats mistakes quickly. During this course, execution stays simulated.

## Understand

Paper trading can expose logic errors, weak safeguards, and operational failures. It
cannot prove future profitability or predict live-order fills.

## Decide

Write a short safety policy:

1. Which instruments may the paper system simulate?
2. Which sessions may it operate in?
3. What is the maximum simulated risk per trade and per day?
4. What conditions immediately stop new simulated trades?
5. Which actions always require human approval?

## Knowledge Check

**A valid setup appears after the daily loss limit is reached. What happens?**

- The strategy overrides the limit because the setup is valid.
- The risk engine rejects the trade.
- The system doubles the next trade to recover losses.

Correct response: **The risk engine rejects the trade.**

## Verify

Confirm the policy includes:

- Paper-only execution
- No live broker credentials
- Independent risk rejection
- A daily stop condition
- A human-required action

## Completion Evidence

Save the policy in the student's workspace without account numbers or credentials.
