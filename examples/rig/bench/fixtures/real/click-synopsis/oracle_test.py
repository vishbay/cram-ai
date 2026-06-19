"""Clean-room oracle for the real-repo `click-synopsis-optional` task.

Authored for cram-bench (not copied from click's own suite), so it carries no
upstream code. Red at the pinned base SHA, green once the synopsis marks an
optional subcommand correctly.
"""
import click
from click.testing import CliRunner


def test_group_synopsis_marks_optional_command():
    # A group that can run without a subcommand must mark the subcommand
    # optional in its help synopsis: "[COMMAND] [ARGS]..." not bare "COMMAND".
    @click.group(invoke_without_command=True)
    def cli():
        pass

    @cli.command()
    def sub():
        pass

    out = CliRunner().invoke(cli, ["--help"]).output
    assert out.splitlines()[0] == "Usage: cli [OPTIONS] [COMMAND] [ARGS]..."
