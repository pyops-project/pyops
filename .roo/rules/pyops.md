# PyOps Project Rules
## Python Virtual Environment
- located at `.venv/`.
- needs to be sourced before executing any command.
- activates running `. ./.venv/bin/activate`.

## Roo Code Agent
- never touches Git.

## App Code
- located at `app/`.
- fixes & workarounds are forbidden.

## VSIX Code
- located at `vsix/`.
- fixes & workarounds are forbidden.

## App Tests
- located at `tests/`.
- common fixtures in `conftest.py`.
- coverage goal is >90%.
- store runtime files in `.cache/`.
- only import from `app` in scope of test method.

# VSIX Tests
- are located at `vsix/tests/`.
- have coverage >80%.
- run `npm run test --prefix vsix/` for automated tests.
- run `npm run test:integration --prefix vsix/` for attended integration tests.
- at development and debug assumes a running authenticationless PyOps instance on localhost:8000.

### Test Fixtures
- never are `autouse`.
- ensure test isolation and cleanup.
- do not overlap.
- complement each other.
- inherit each other if meaningful.