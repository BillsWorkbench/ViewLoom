# Contributing to Viewloom

Viewloom is an early working prototype. Small, focused improvements and reports
from real photographic sequences are useful at this stage.

## Get started

1. Follow the source setup in [README](README.md), using an isolated environment.
2. Read the [architecture](docs/ARCHITECTURE.md) and [development notes](docs/DEVELOPMENT.md).
3. Install in editable mode for development: `python -m pip install -e .`.
4. Run `python -m unittest discover -s tests -v` and
   `python -m tests.check_alignment_ui` with that environment's interpreter.

## Changes

- Discuss larger workflow changes in an issue before implementing them.
- Preserve non-destructive projects and never write to source photographs.
- Keep expensive image processing off the interface thread.
- Keep preview and export behavior aligned. Explain approximation differences.
- Test meaningful behavior, including cancellation and saved-project compatibility.
- Include reproduction steps and validation results in pull requests.
- Do not bundle RAW files, personal project files, local environments, or generated reports.

## Reporting problems

Include the Viewloom version, operating system, Python version, camera model,
RAW format/compression setting, reproduction steps, and expected/actual behavior.
Use synthetic images or a minimal description when possible. Only share photos
you have permission to distribute, and remove personal paths from logs.

Windows reports are welcome, but Windows support is still being validated.
See the issue templates for a starting point.

Original project contributions are accepted under the repository's MIT license.
Third-party assets and dependencies retain their own terms.
