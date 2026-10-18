# Contributing

Thanks for your interest. This project values honesty and safety over breadth.

## Ground rules

- **Never fake a build result.** If a tool is missing, report it. Do not add an
  adapter that claims success without running a real command.
- **Runtime code uses the standard library only.** Discuss before adding a
  dependency.
- **Everything in English** — code, comments, docs, commit messages.
- **No shell strings.** Commands are argv lists.
- **Project-controlled scripts require consent.**

## Workflow

1. Fork and branch from the default branch.
2. Make focused changes with tests.
3. Run the suite: `python run_tests.py`.
4. Update docs when behaviour or support changes (see
   [docs/SUPPORTED_PROJECTS.md](docs/SUPPORTED_PROJECTS.md) and
   [docs/ECE_FORMAT.md](docs/ECE_FORMAT.md)).
5. Open a pull request describing *what* changed and *how you verified it*.

## Commit messages

Use short imperative subjects, for example:

```
Add Go adapter detection and tool-gated build
Fix unity detection for projects at the scan root
```

## Pull requests

A good PR:

- has a clear description and the commands you ran to verify it,
- keeps unrelated changes out,
- does not introduce new dependencies,
- does not claim capabilities the code does not have.

## Reporting bugs

Include the command you ran, the container or project type, the full error
output, and `python -m ece_autocompiler tools`. For security issues, follow
[SECURITY.md](SECURITY.md) instead.

## License

The license has not been chosen yet. By contributing, you agree that your
contribution may be distributed under whatever license the project owner later
selects.
