# Interactive Course

This folder holds the structured content for the paper-trading automation course. It
does not depend on a specific web framework or course platform.

## Start Here

- [Course experience](COURSE_EXPERIENCE.md)
- [Content model](CONTENT_MODEL.md)
- [Module manifest](content/modules.json)
- [Lesson template](templates/LESSON_TEMPLATE.md)
- [First lesson](content/module-00/lesson-01-paper-first.md)

## Product Boundary

Course exercises use simulated execution only. Do not collect live broker credentials
or present simulated results as proof of profitability.

## Later App

An eventual course app should:

- Load the module manifest and Markdown lesson content
- Store progress locally before adding accounts or a database
- Support checks, worksheets, code tasks, and verification commands
- Require the safety module before technical build modules
- Export completed worksheets and the capstone checklist
