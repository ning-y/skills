---
name: suggestcommit
description: Analyzes the current git repository's changes and suggests five high-quality commit messages using Conventional Commits format. Scopes are compulsory and drawn from docs/AGENTS/COMMIT-SCOPES.md if available. Use when you need commit message suggestions.
---

# suggestcommit

## Data Gathering

First, determine the repository state and gather changes:

1. Check if the repo has any commits:

```bash
git rev-parse --verify HEAD 2>/dev/null
```

2. If the repo has commits, capture changes to tracked files:

```bash
git diff HEAD 2>/dev/null
```

If `git diff HEAD` fails or shows no output, fall back to the combined output:

```bash
git diff --cached && echo "---UNSTAGED---" && git diff
```

3. Also check for untracked files via `git status --short`:

```bash
git status --short
```

Lines starting with `??` are untracked files. Use your judgment to identify which are project-relevant (source code, config files, documentation — not generated artifacts, build outputs, scratch notes, or personal droppings). For each relevant untracked file, read its contents and consider it alongside the diff. Note the path of each relevant untracked file — you will need to explicitly stage it during commit.

4. If the repo has no commits yet (bare/empty repo), list all tracked and untracked files, then read them:

```bash
git ls-files --others --exclude-standard && echo "---TRACKED---" && git ls-files
```

Use your judgment to filter out irrelevant scratch files.

## Analysis & Formatting

Analyze the gathered diffs to understand the core logic of the changes. Generate 5 distinct commit message options using the Conventional Commits standard. Scopes are compulsory — every commit MUST have a scope.

### Format: `<type>(<scope>): <subject>`

**Types:** Use standard types, plus the project-specific `plot` type. Apply these heuristics to distinguish similar types:

Type-selection heuristics:
- `feat` — Diff introduces a new capability, replaces a whole approach, or adds significant logic (e.g., pagination, validation pipeline, new endpoint).
- `fix` — Diff corrects a wrong value, adjusts a boundary condition, or patches a crash. Usually a small, targeted change.
- `refactor` — Code is restructured with no observable behavior change.
- `plot` — *For data analysis pipelines only,* diff creates or modifies a graphical plot

Other types:
- `chore` — Maintenance, tooling, dependencies
- `docs` — Documentation only
- `style` — Formatting, whitespace (no code change)
- `test` — Adding or fixing tests

**Scope resolution:** Scopes are compulsory. Before generating options, discover available scopes:

1. Find the repo root: `git rev-parse --show-toplevel`
2. Check for `docs/AGENTS/COMMIT-SCOPES.md` at the repo root
3. If it exists, parse each line matching `- **<scope>** — <description>` to build the predefined scope list.
4. If it doesn't exist, predefined scopes are empty.

When assigning scopes to commits:
- Try to match each commit's changes against a predefined scope (match via the scope description against file names, paths, and content).
- Of the 5 commits, first try to use predefined scopes. Reserve commits that don't fit any predefined scope for brand-new scope suggestions.
- If a predefined scope fits multiple commits, reuse it with different angles.

**Subject line:**
- Imperative mood (e.g., "Fix", not "Fixed" or "Fixes")
- 100 characters or less
- No trailing period
- Capitalize the first word after the type/scope

**Body (optional):**
- Only include if the diff shows a complex change that benefits from explanation
- Explain the *why* or *what* of the change based on context
- Do not explain *how* the code was written
- Wrap at 72 characters

## Output Delivery

Do **not** print the options as code blocks. Do **not** run `git commit` without going through the interactive dialog.

You **MUST** invoke the `ask` tool. This is a blocking interactive step — it shows a TUI dialog and waits for the user to respond. Nothing happens until the user acts.

### Procedure

1. Generate 5 commit message options (subject line + optional body, per the rules above).

2. Keep a private mapping of `{ subject: string, fullMessage: string, newScope: string | null }` for each option. Set `newScope` to the scope name for options using a brand-new scope (not in `docs/AGENTS/COMMIT-SCOPES.md`), or `null` for options using a predefined scope or a scope that already exists in `docs/AGENTS/COMMIT-SCOPES.md`.

3. **Invoke the `ask` tool.** This is the interactive dialog — you must call it as a real tool invocation, not simulate it. It blocks until the user selects, types a custom entry, or cancels.

   - `questions`: an array containing a single question object with:
     - `id`: `"commit-message"`
     - `question`: `"Select a commit message"`
     - `options`: the 5 subject lines, each as `{ "label": "<subject line>" }`
     - `recommended`: `0`

   The `ask` dialog automatically offers a free-form "Other (type your own)" entry, so custom messages need no separate flag.

4. **Only after** receiving the tool's return value, act on it.

   Before committing, ensure a git identity is available. Use the first
   existing source — repo-local config, global config, or the most recent
   commit in the repo:

   ```bash
GIT_EMAIL=$(git config user.email 2>/dev/null || git log -1 --format='%ae' 2>/dev/null || echo "")
   GIT_NAME=$(git config user.name 2>/dev/null || git log -1 --format='%an' 2>/dev/null || echo "")
   ```

   If either `GIT_NAME` or `GIT_EMAIL` is empty (no identity configured and
   no prior commit to inherit from), do not commit. Ask the user for their
   name and email via the `ask` tool, or fall back to `whoami` /
   `whoami@$(hostname)`.

   Then pass the identity inline with `-c` flags so no config files are
   modified:

   - **Chat redirect** — the result has `details.chatRedirect` set to `true`
     (the user chose "Chat about this"). Commit nothing; instead answer the
     user's chat message directly.

   - **Picked an option** — the chosen subject is `details.selectedOptions[0]`. Look up the `fullMessage` from step 2.

     If the chosen option used a **new scope** (its `newScope` is non-null), first define it in `docs/AGENTS/COMMIT-SCOPES.md`:

     - Read the current `docs/AGENTS/COMMIT-SCOPES.md` content (create the file and its `docs/AGENTS/` parent directories if they don't exist).
     - Append the new scope line (`- **<scope>** — <auto-generated description>`) to the end of the file.
     - Auto-generate the description from the diff context (the files changed, the nature of the change). Keep it concise (one line).
     - Use lowercase for the scope name. Preserve the existing bullet format (`- **<scope>** — <description>`).
     - Write the updated content back to `docs/AGENTS/COMMIT-SCOPES.md`.
     - Stage it: `git add docs/AGENTS/COMMIT-SCOPES.md`

     Then stage all changed files:
     ```bash
     git add --update
     ```

     If you identified relevant new untracked files during the data gathering phase, stage each explicitly:
     ```bash
     git add path/to/relevant/file
     ```

     Then commit:
     ```bash
     git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "<subject>" -m "<body>"
     ```
     Omit the second `-m` if there is no body.

   - **Custom entry** — the typed value is `details.customInput`. Use it verbatim.

     Parse the custom message for a scope using the pattern `<type>(<scope>):`. If a scope is found and is not in the predefined list, first define it in `docs/AGENTS/COMMIT-SCOPES.md` using the same rules as above (create, append, stage).

     Then stage all changed files:
     ```bash
     git add --update
     ```

     If you identified relevant new untracked files during the data gathering phase, stage each explicitly:
     ```bash
     git add path/to/relevant/file
     ```

     Then commit:
     ```bash
     git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "<customInput>"
     ```

   - **Cancelled** — the `ask` call throws a cancellation error instead of returning a result. Treat that as the user cancelling: do nothing.

### Show the Commit

After a successful commit (picked or custom), show the resulting commit with color:

```bash
git --no-pager log --color=always --oneline --stat --max-count=1 HEAD
```

Use `git --no-pager` or `GIT_PAGER=cat` to avoid interactive pager issues, and `--color=always` to preserve ANSI color codes in the output.

If the user cancelled, skip this step.

### Warnings

- Do not auto-commit without showing the dialog. The user must pick or type their message.
- Do not print the options in your response text — the dialog is the only delivery mechanism.

Keep conversational filler to an absolute minimum; just invoke the tool and act on the result.
