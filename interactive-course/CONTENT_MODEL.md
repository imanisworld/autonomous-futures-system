# Content Model

The interactive course should keep curriculum content separate from the eventual app.

## Module

```json
{
  "id": "module-00",
  "title": "Orientation and Safety",
  "estimated_hours": "1-2",
  "required": true,
  "lessons": ["lesson-01-paper-first"],
  "completion_gate": "Submit a paper-only system safety policy"
}
```

## Lesson Front Matter

```yaml
id: lesson-01-paper-first
module: module-00
title: Paper First
estimated_minutes: 20
interaction: worksheet
required: true
deliverable: paper-only-safety-policy
```

## Completion Record

```json
{
  "lesson_id": "lesson-01-paper-first",
  "status": "completed",
  "completed_at": "ISO-8601 timestamp",
  "evidence": {
    "type": "worksheet",
    "reference": "learner-owned location"
  }
}
```

Never store secrets, broker credentials, webhook secrets, or live-trading access in a
completion record.
