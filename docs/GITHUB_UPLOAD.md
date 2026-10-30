# Posting this package on GitHub

This folder is prepared repository content. Nothing has been uploaded by the
packaging process. The ZIP is a convenient transfer copy; extract it first.

## Files to post

Post the **contents of the Viewloom folder** as your repository root. Keep the
dotfiles and `.github` folder: they contain ignore rules, issue templates, and
the automated-check workflow.

Do not post the ZIP as a substitute for source files. A repository ZIP may be
attached to a later release separately, but GitHub needs the extracted source
tree to display README and track code changes normally.

The package intentionally omits your Python environment, NEF/DNG photographs,
personal `.viewloom` projects, screenshots, exports, environment reports, and
the unrelated files in the original workspace. `PACKAGE_MANIFEST.json` records
the prepared file list and SHA-256 digests for transfer verification.

## GitHub website

1. Create a repository named `Viewloom` or your preferred name.
2. Choose public or private visibility.
3. Leave GitHub's automatic README, license, and `.gitignore` creation off;
   this package already contains those files.
4. Upload the extracted files, preserving folders. Check that `README.md`,
   `LICENSE`, `pyproject.toml`, `viewloom/`, and `.github/` appear at the root.
5. Review the file list and commit. Check the Actions tab for the first CI run.

If your file picker omits hidden folders, use Git locally so `.github`,
`.gitignore`, and `.gitattributes` are included.

## Git locally

Run these commands in the extracted Viewloom folder:

```sh
git init -b main
git add .
git status
git commit -m "Initial Viewloom 0.5.3 source release"
```

Then follow the new repository's instructions to add its actual remote URL
and push `main`. No remote URL, account credentials, or GitHub identity has
been guessed or embedded in this package.

## Before announcing a release

- Confirm the MIT copyright name is correct: **Bill's Workbench**.
- Review CI results; do not describe Windows as validated until it has been tested.
- Test a real sequence and confirm preview/export results.
- Read the [roadmap](../ROADMAP.md) and [known limits](DEVELOPMENT.md#known-limits).
- Standalone macOS/Windows applications require additional packaging work.
