---
name: condense-context-insights
description: Condenses technically relevant insights from new context window content (environment_details, tool results, file reads) into proposed updates for project's .roo/rules/insights.md or .roo/rules/insights_*.md, compliant with insights rules. Use for context insights, new valuable insights, mandatory invocation on context updates.
---

# When to use this skill
**Mandatory:** Invoke every time new valuable technical insights enter the context window.

**Triggers:**
- Updated/changed `environment_details`
- Fresh tool outputs (list_files, read_file results)
- Newly read/discovered file contents
- User messages with project-specific details

# When NOT to use this skill
- If executing a mode workflow which has an explicit insight condensation step
- No new insights (redundant with existing)
- Purely conversational or non-technical context
- Insights already condensed in recent steps

# Inputs required
- New context window content (e.g., environment_details, tool results).

# Workflow
1. Request user approval that the provided context contains new valuable insights.
2. Review the new context: identify technically relevant details, relations from new info.
3. Derive condensations: markdown lists, mermaid for relations, scoped and ordered by relevance.
4. Exclude historical info; focus on current implications.
5. Ensure concise (<32768 tokens); propose as diff or section addition.
6. **Explicitly get user approval** before writing: propose content via `ask_followup_question`.
7. On approval, append/update file(s) via `write_to_file` or `search_replace`.

# Examples
**Input:** New environment_details showing skills directory structure.  
**Output:** Proposed: ## Skills Directory\n- Architect skills: analyze-*, check-*, condense-*, etc.\n```mermaid\ngraph TD\nskills --> condense-changeset-insights\nskills --> condense-plan-insights\n```

# Troubleshooting
- Overlap: Merge with existing (read insights first).
- No new insights: Skip; inform user.
- Token exceed: Condense to meet constraints.
