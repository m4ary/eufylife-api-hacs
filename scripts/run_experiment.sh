#!/bin/bash
source "$(dirname "$0")/set_env.sh"
python3 "$(dirname "$0")/test_direct_experiment.py" "$1"
