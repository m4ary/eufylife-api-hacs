#!/bin/bash

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/set_env.sh"

animation="${ANIMATION:-Guy Fawkes Night}"
iterations="${ITERATIONS:-3}"
delay="${DELAY_SECONDS:-3}"
output_dir="${OUTPUT_DIR:-${TMPDIR:-/tmp}/eufylife-animation-validation}"

usage() {
    printf 'Usage: %s [animation] [iterations] [delay_seconds]\n' "$(basename "$0")"
    printf '\nEnvironment overrides: ANIMATION, ITERATIONS, DELAY_SECONDS, OUTPUT_DIR\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

animation="${1:-$animation}"
iterations="${2:-$iterations}"
delay="${3:-$delay}"

if ! [[ "$iterations" =~ ^[1-9][0-9]*$ && "$delay" =~ ^[0-9]+$ ]]; then
    printf 'Iterations must be positive and delay must be a non-negative integer.\n' >&2
    exit 2
fi

mkdir -p "$output_dir"
run_id="$(date +%Y%m%d-%H%M%S)"
log_file="$output_dir/animation-loop-$run_id.log"

exec > >(tee -a "$log_file") 2>&1

printf 'Animation validation loop\n'
printf '  animation: %s\n  iterations: %s\n  delay: %ss\n  serial: %s\n  log: %s\n' \
    "$animation" "$iterations" "$delay" "${YOUR_SERIAL:-unset}" "$log_file"

for ((attempt = 1; attempt <= iterations; attempt++)); do
    printf '\n=== Attempt %d/%d: %s ===\n' "$attempt" "$iterations" "$animation"

    if python3 "$SCRIPT_DIR/validate_lights.py" \
        --country nl \
        --test-serial "$YOUR_SERIAL" \
        --animation "$animation"; then
        printf 'Animation command completed without a client error.\n'
    else
        command_status=$?
        printf 'Animation command failed (exit %d); collecting state anyway.\n' "$command_status"
    fi

    if python3 "$SCRIPT_DIR/check_lamp_status.py"; then
        printf 'State read completed.\n'
    else
        status_code=$?
        printf 'State read failed (exit %d).\n' "$status_code"
    fi

    if (( attempt < iterations )); then
        sleep "$delay"
    fi
done

printf '\nLoop complete. Full output: %s\n' "$log_file"