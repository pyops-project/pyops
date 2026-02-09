# Global Rules
## Insights
- are "Condensations of technically relevant insights into project" as markdown.
- contain project detail given by user & derrived from explicitly approved code.
- contain relations in Meermaid diagrams.
- are ordered by scope.
- only contain information required to understand current state of project.
- never contain historical information.
- don't exceed 32768 tokens.
- are persisted/updated to `.roo/rules/03_insights.md`.
- are persisted before using `replace_in_file` or `write_to_file` on any other file.

## Architecture
- only decided by user.

## Agent
- avoids Context pollution.
- reduces Context usage.
- presents plans in response (no file).
- uses Mermaid diagrams to visualize.
- never touches Git.
- updates and maintains insights.