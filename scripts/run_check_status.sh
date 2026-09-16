#!/bin/bash
source "$(dirname "$0")/set_env.sh"
python3 "$(dirname "$0")/check_lamp_status.py"
