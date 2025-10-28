# Insight Guidelines
Insights are Turtle (.ttl) files of interconnected facts in Subject-Predicate-Object (S-P-O) triples, capturing the essential structure of project complexes and their interactions.

## Workflow
**Trigger:** At the start of every task (planning, execution, queries). Harvesting (analysis, loading, and generation of insights) occurs in PLAN MODE without interruption unless ambiguities (e.g., unclear task context requiring clarification) trigger a pause via ask_followup_question. Once generation is complete in PLAN MODE, switch to ACT MODE to write the .ttl files.

1. List `doc/insights/` and subdirectories to discover .ttl file names.
2. Rough analysis: Match file names to task keywords/context (e.g., "job" → job_lifecycle.ttl, "git" → git_integration.ttl). For tests/, prioritize app/ validation patterns. Identify relevant insights, including cross-complex (e.g., load full app/ for tests/ to illustrate dependency-based relevance).
3. Load all identified relevant .ttl files from related complexes using read_file iteratively.
4. Parse and load contents into context (line-by-line as S-P-O triples).
5. Generate new/updated .ttl content based on analysis, documenting in plan_mode_respond for user review/approval.
6. Document loaded/generated insights and rationale in task_progress checklist. If none required (e.g., empty directory), note explicitly. Proceed without pauses in PLAN MODE if no ambiguities; confirm completeness before suggesting toggle to ACT MODE for writing.

## Format
- **Turtle Structure:** RDF Turtle (.ttl files). Each line: one triple ending in '.'. Subjects/predicates as strings or <URI>; objects as quoted literals.
- **Size:** Maximum 512 tokens of core content per file (excluding syntax like '.' or quotes); clean for efficiency. Split if needed.
- **Content Order:** Most important triple first.
- **Organization:** Global in `doc/insights/`; complex-specific in subfolders (e.g., `src/`, `test/`, `vsix/`). Descriptive names, .ttl extension, no numbering.
- **S-P-O Format:** subject predicate "object" . Quote literals for multi-word or complex descriptions (e.g., parameters, details). Double-check for format violations.
- **Content Scope:** Triples represent current project state (e.g., dependencies, types, descriptions); avoid future plans, narratives, or dynamic values.

**RDF Examples:**
```
# Valid: Current module dependency
<app/lib/pyops> rdf:type "Module" .
<app/lib/pyops> dependsOn "app/orm/database" .
<app/lib/pyops> description "Handles PyOps operations with isolation and logging parameters." .

# Valid: Function interaction
<job.create> calls "app/routers/scriptlet/job.py:create_job" .
<job.create> requires "script_id, schedule_id" .
<job.create> returns "job_id" .

# Valid: State fact
<schedule> hasType "cron" .
<schedule> associatedWith "app/orm/schedule.py" .
```

# Do: Capture static structure/interactions
# Don't: <job> willSupport "new feature" . (future-oriented)
# Don't: <pyops> overview "This module does X and Y." . (narrative, use description predicate)

These triples interconnect facts about current project structure, dependencies, and behaviors.

## Enforcement
- **Checklist:** Include "Perform analysis, load ALL, and generate ALL required insights from doc/insights/, including cross-complex (e.g., app/ for tests/), in PLAN MODE without interruption unless ambiguities arise" as first item in task_progress (skip detailed analysis if empty).
- **Parsing:** Parse .ttl line-by-line as S-P-O triples ending in '.'.
- **Failure:** If not loaded, pause and request user confirmation to proceed without.

## Improvement
**Objective:** Improve `doc/insights/` via user interactions/feedback.

**Trigger:** End of tasks with feedback or multiple non-trivial steps (e.g., file edits, logic generation). For PLAN MODE, offer reflection during planning for new/updated insights, before finalizing.

**Process:**
1. Offer: "Before completing, would you like reflection on interaction and `doc/insights/` improvements?"
2. If confirmed: Synthesize feedback, identify used files, propose content changes (prioritize feedback; use diff blocks), await approval to apply (replace_in_file/write_to_file).
3. Proceed if declined.

**Constraints:**
- Skip for simple tasks without feedback.
- Pure current state only (no future/past/crashes/progress).
- No platform structure unless project-relevant.
