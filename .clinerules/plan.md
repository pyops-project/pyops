# Plan Guidelines
* Current plan is always persisted to `.context/current.md`.

## Persitance
**Trigger:** Before using `replace_in_file` or `write_to_file` on `.context/current.md`.

**Process:** Use ask_followup_question to present the proposed changes (e.g., via diff blocks) and await user confirmation before applying. If declined, do not save.