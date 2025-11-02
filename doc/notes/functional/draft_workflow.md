# Draft Workflow

## Overview
VSIX handles drafts via unstaged git changes in remote repos.

## Key Aspects
- **Write Draft:** pyops.writeLocalToRemoteDraft always POSTs local code to /draft (unstaged write).
- **Save Draft:** pyops.saveRemoteDraft checks /status has_unstaged=true, then commits via /item.
- **Status:** GET /status uses git diff for unstaged detection.
- **Interactions:** Tree items show sync/draft status; errors prevented by pre-checks.
