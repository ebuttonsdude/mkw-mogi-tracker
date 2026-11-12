#!/bin/sh
# Run from the checkout so relative settings/data paths work from any directory.
cd -- "$(dirname -- "$0")" || {
    printf '%s\n' 'Could not open the tracker folder.' >&2
    exit 1
}

if [ ! -f .venv/bin/python ] || [ ! -x .venv/bin/python ]; then
    printf '%s\n' 'The tracker needs its Python environment first.' \
        'Run sh setupLinux.sh from the tracker folder, then try again.' >&2
    exit 1
fi

# Keep Ctrl+C and the exit status attached to the tracker, not a background job.
exec .venv/bin/python -m mkw_tracker "$@"
