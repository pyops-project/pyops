# Note Guidelines
Notes are Markdown (.md) files providing contextual information on project aspects.

## Workflow
**Trigger:** At the start of every task (planning, execution, queries). Harvesting (analysis, loading, and generation of notes) occurs in PLAN MODE without interruption unless ambiguities (e.g., unclear task context requiring clarification) trigger a pause via ask_followup_question. Once generation is complete in PLAN MODE, switch to ACT MODE to write the .md files.

1. List `doc/notes/` and subdirectories to discover .md file names.
2. Rough analysis: Match file names to task keywords/context (e.g., "job" → job_lifecycle.md, "git" → git_integration.md). For tests/, prioritize app/ validation patterns. Identify relevant notes, including cross-complex (e.g., load full app/ for tests/ to illustrate dependency-based relevance).
3. Load all identified relevant .md files from related complexes using read_file iteratively.
4. Load contents into context.
5. Generate new/updated .md content based on analysis, documenting in plan_mode_respond for user review/approval.
6. Document loaded/generated notes and rationale in task_progress checklist. If none required (e.g., empty directory or meta-tasks), note explicitly. Proceed without pauses in PLAN MODE if no ambiguities; confirm completeness before suggesting toggle to ACT MODE for writing.

## Format
- **Size:** Maximum 512 tokens of core content per file; split if needed.
- **Organization:** Global in `doc/notes/`; complex-specific in subfolders (e.g., `architectural/`, `functional/`).
- **Naming:** Descriptive, .md extension, orderless (no numbering like 01_).

**Markdown Example:**
```
# Job Lifecycle Patterns

## Overview
Describes job creation, execution, and monitoring in app/routers/scriptlet/job.py.

## Key Aspects
- **Creation:** Via POST /scriptlet/job with params: script_id, schedule_id.
- **Execution:** Handled by app/lib/runner.py; logs to persistence layer.
- **Interactions:** Depends on app/orm/job.py for state; cross-complex with vsix/src/state/jobManager.ts.
```
Focus on current state facts.

## Enforcement
- **Checklist:** Include "Perform analysis, load ALL, and generate ALL required notes from doc/notes/, including cross-complex (e.g., app/ for tests/), in PLAN MODE without interruption unless ambiguities arise" as first item in task_progress (skip detailed analysis if empty).
- **Failure:** If not loaded, pause and request user confirmation to proceed without.

## Improvement
**Objective:** Improve `doc/notes/` via user interactions/feedback.

**Trigger:** End of tasks with feedback or multiple non-trivial steps (e.g., file edits, logic generation). For PLAN MODE, offer reflection during planning for new/updated notes, before finalizing.

**Process:**
1. Offer: "Before I complete the task, would you like me to reflect on our interaction and suggest potential improvements to the `doc/notes/`?"
2. If confirmed: Synthesize feedback, identify used files, propose content changes (prioritize feedback; use diff blocks), await approval to apply (replace_in_file/write_to_file).
3. Proceed if declined.

**Constraints:**
- Skip for simple tasks without feedback.
- Pure current state only (no future/past/crashes/progress).
- Orderless descriptive names, .md extension.
- Max 512 tokens core content; split if needed.
