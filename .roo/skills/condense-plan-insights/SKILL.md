---
name: condense-plan-insights
description: Condenses technically relevant insights from a created plan into proposed updates for project's .roo/rules/insights.md or .roo/rules/insights_*.md, compliant with insights rules. Use after generating a detailed plan for a project task.
---

# When NOT to use this skill
- If not executing an explicit insights condensation step of a mode workflow
- No new insights (redundant with existing)
- Purely conversational or non-technical context
- Insights already condensed in recent steps

# Inputs required
- The created plan content (paste or reference).

# Workflow
1. Analyze plan for new technically relevant insights: project details, relations (mermaid), scoped by breadth.
2. Derive condensations: current state only, from plan + approved code; no history.
3. Format as markdown: ordered by scope, include mermaid diagrams for relations.
4. Ensure total <32768 tokens; propose as diff or new section.
5. **Explicitly get user approval** before writing: propose content, ask "Approve update to Insights?" via `ask_followup_question`.
6. On approval, append/update file(s) via `write_to_file` or `search_replace`.

# Examples
**Input:** Plan: "Add auth: new User model, JWT flow"  
**Output:** Proposed: ## Auth Scope\n- Current: No auth.\n```mermaid\ngraph LR\nUser --JWT--> API\n```

# Troubleshooting
- Overlap: Merge with existing (read first via `read-insights`).
- No new insights: Skip; inform user.
- Token exceed: Condense to meet constraints.
