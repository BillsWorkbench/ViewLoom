#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
    print 'Set up the project environment using the README instructions first.'
    read '?Press Return to close.'
    exit 1
fi
exec .venv/bin/python -m viewloom "$@"
