#!/bin/bash

set -e

for i in {1..5}; do
  echo "Running $i/5"
  pytest tests/ -v --cov=app --cov-report=term-missing --cov-append --cov-fail-under=80
done

coverage html
