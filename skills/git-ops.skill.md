---
name: git-ops
description: Git repository management with safe defaults and conventional commit guidance
tier: pro
activation: trigger
keywords: git, commit, branch, merge, rebase, push, pull, repository, repo, diff, stash
flash_forbidden: true
tools:
  - name: git_status
    description: Show the working tree status of a git repository
    handler: shell
    template: "cd {repo_path} && git status"
    parameters:
      repo_path: {type: string, description: Absolute path to the git repository, required: true}
  - name: git_log
    description: Show recent commit history for a git repository
    handler: shell
    template: "cd {repo_path} && git log --oneline -20"
    parameters:
      repo_path: {type: string, description: Absolute path to the git repository, required: true}
  - name: git_diff
    description: Show unstaged changes in a git repository
    handler: shell
    template: "cd {repo_path} && git diff"
    parameters:
      repo_path: {type: string, description: Absolute path to the git repository, required: true}
---

## Git Operations Guide

When working with git repositories, follow these practices:

1. **Before any destructive operation**, run `git status` and confirm the working tree state.
2. **Branching**: For non-trivial changes, create a feature branch: `git checkout -b feat/description`.
3. **Commit messages**: Use conventional commits format — `type(scope): description`. Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`.
4. **Never force-push to main/master** without explicit human confirmation via `notify_human`.
5. **Check for uncommitted changes** before running scripts that modify files.

Repositories are typically found in `~/projects/` or wherever the task context specifies.
