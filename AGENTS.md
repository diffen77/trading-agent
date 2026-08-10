<!-- cortex:auto:start -->
## Cortex Auto Workflow
- Use the `using-cortex` skill if available; otherwise follow the commands below.
- Search before answering code questions: `cortex search "<query>" --json`; never answer from memory.
- Check `cortex rules --json` before suggesting changes and `cortex impact "<query>" --json` before refactors.
- Review changed files with `cortex pattern-evidence <file> --json` before finalizing.
- Run `cortex update` before completing substantial code changes.
- If background sync is enabled, check with `cortex watch status`.
<!-- cortex:auto:end -->
