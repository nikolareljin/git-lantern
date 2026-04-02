# Agent Notes

- Never add hard-coded paths to directories in any project.
- Repo: git-lantern (Python CLI).
- Use `python3` for all scripts; respect `PYTHON_BIN` overrides and keep scripts POSIX-friendly (bash).
- Prefer `rg` for search and `apply_patch` for small edits.
- CLI entrypoint lives in `src/lantern/cli.py`; table formatting in `src/lantern/table.py`.
- Docs live in `docs/` and `README.md` is the top-level entry point; keep examples current.
- Tests: `make test` or `./scripts/test.sh` (avoid network).
- Lint: `make lint` or `./scripts/lint.sh`.
- Build: `make build` or `./scripts/build.sh`.
- Avoid committing generated artifacts (man pages, build outputs); update `.gitignore` if needed.

## PR Implementation Workflow

1. **Fix Issues**: Address all review feedback in the code.
2. **Resolve Conversations**: Use the GitHub GraphQL API to resolve all review threads.
   ```bash
   # Fetch thread IDs
   gh api graphql -F owner='OWNER' -F repo='REPO' -F pull_number=PR_NUMBER -f query='
   query($owner: String!, $repo: String!, $pull_number: Int!) {
     repository(owner: $owner, name: $repo) {
       pullRequest(number: $pull_number) {
         reviewThreads(first: 100) {
           nodes { id isResolved }
         }
       }
     }
   }'
   # Resolve each thread
   gh api graphql -f query='mutation($threadId: ID!) { resolveReviewThread(input: {threadId: $threadId}) { thread { id isResolved } } }' -F threadId=THREAD_ID
   ```
3. **Re-request Review**: Follow the workspace-level reviewer request policy.
