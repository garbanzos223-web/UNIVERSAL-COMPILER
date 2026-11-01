# Contributing

Thanks for your interest. This project values honesty and safety over breadth.

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE), the same license that covers the project.

## Ground rules

- **Never fake a build or decompilation result.** If a tool or backend is
  missing, report it. Do not add a backend that claims to recover source it does
  not have, and do not mark generated outlines as recompilable.
- **Runtime code uses the standard library only.** Optional decompiler extras
  (`pefile`, `capstone`) must stay optional and degrade honestly.
- **Everything in English** — code, comments, docs, commit messages.
- **No shell strings.** Commands are argv lists.
- **Project-controlled scripts require consent.**
- **Imported files are never executed** by the decompiler.

## Workflow

1. Fork and branch from the default branch.
2. Make focused changes with tests.
3. Run the suite: `python run_tests.py`.
4. Update docs when behaviour or support changes (see
   [docs/SUPPORTED_PROJECTS.md](docs/SUPPORTED_PROJECTS.md),
   [docs/SUPPORTED_FORMATS.md](docs/SUPPORTED_FORMATS.md) and
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

The project is licensed under the [MIT License](LICENSE). By contributing you
agree that your contributions are licensed under the same terms.
