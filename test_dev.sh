#!/bin/bash

LOG_LEVEL="${1:-WARNING}"  # First arg = log level, default WARNING

pytest tests/ \
  -W error \
  -v \
  --cov=app \
  --cov-report=term-missing \
  --cov-append \
  --cov-fail-under=80 \
  --log-cli-level="$LOG_LEVEL"