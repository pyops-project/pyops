#!/bin/bash

set -e

for i in {1..5}; do
  echo "Running test suite $i/5"
  pytest tests/ -W error -v --cov=app --cov-report=term-missing --cov-append
done

coverage html
