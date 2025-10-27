# Test Guidelines
* No `browser-use`, request user to manually execute.
* Coverage needs to be >90%.

## `app/` Tests
* Tests are located at `tests/`.
* Tests are `pytest`.
* Test are executed using `pytest -v`.
* Test runtime files are stored to `.cache/` so multithreaded tests have a common access pattern.
* Imports from `app` always are inside test function's scope.

### Fixtures
* Never are `autouse`.
* Ensure test isolation (cleanup after test function).
* Do not overlap but inherit and complement each other.

## `vsix/` Tests
* Tests are located at `vsix/tests/`.
* Tests are executed using `npm run test --prefix vsix/`.