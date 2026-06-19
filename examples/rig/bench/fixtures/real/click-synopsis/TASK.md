# Task: mark an optional subcommand as optional in the help synopsis

This is a clone of the real `click` library (`pallets/click`) at a pinned commit.

When a group is declared so it can run *without* a subcommand
(`@click.group(invoke_without_command=True)`), its `--help` synopsis still prints
the subcommand as **required**:

```
Usage: cli [OPTIONS] COMMAND [ARGS]...
```

Because the command is optional here, the subcommand token should be bracketed:

```
Usage: cli [OPTIONS] [COMMAND] [ARGS]...
```

Find where the command synopsis / metavar is built and fix it so an optional
subcommand is shown bracketed (and required subcommands stay unbracketed).

Verify with:

```
PYTHONPATH=src python -m pytest oracle_test.py -q
```

Do not edit `oracle_test.py`.
