"""
Strip output from Jupyter and IPython notebooks
===============================================

Opens a notebook, strips its output, and writes the outputless version to the
original file.

Useful mainly as a git filter or pre-commit hook for users who don't want to
track output in VCS.

This does mostly the same thing as the `Clear All Output` command in the
notebook UI.

Usage
=====

Strip output from IPython / Jupyter / Zeppelin notebook (modifies the file in-place): ::

    nbstripout <file.ipynb>
    nbstripout <file.zpln>

By default, nbstripout will only modify files ending in '.ipynb' or '.zpln', to
process other files us the '-f' flag to force the application.

    nbstripout -f <file.ipynb.bak>

For using Zeppelin mode while processing files with other extensions use:
    nbstripout -m zeppelin -f <file.ext>

Use as part of a shell pipeline: ::

    cat FILE.ipynb | nbstripout > OUT.ipynb
    cat FILE.zpln | nbstripout -m zeppelin > OUT.zpln

Set up the git filter and attributes as described in the manual installation
instructions below: ::

    nbstripout --install

Set up the git filter using ``.gitattributes`` ::

    nbstripout --install --attributes .gitattributes

Set up the git filter in your global ``~/.gitconfig`` ::

    nbstripout --install --global

Set up the git filter in your system-wide ``$(prefix)/etc/gitconfig`` (most installations will require you to ``sudo``) ::

    [sudo] nbstripout --install --system

Remove the git filter and attributes: ::

    nbstripout --uninstall

Remove the git filter from your global ``~/.gitconfig`` and attributes ::

    nbstripout --uninstall --global

Remove the git filter from your system-wide ``$(prefix)/etc/gitconfig`` and attributes ::

    nbstripout --uninstall --system

Remove the git filter and attributes from ``.gitattributes``: ::

    nbstripout --uninstall --attributes .gitattributes

Check if ``nbstripout`` is installed in the current repository
(exits with code 0 if installed, 1 otherwise): ::

    nbstripout --is-installed

Print status of ``nbstripout`` installation in the current repository and
configuration summary of filter and attributes if installed
(exits with code 0 if installed, 1 otherwise): ::

    nbstripout --status

Do a dry run and only list which files would have been stripped: ::

    nbstripout --dry-run FILE.ipynb [FILE2.ipynb ...]

Print the version: ::

    nbstripout --version

Show this help page: ::

    nbstripout --help

Manual filter installation
==========================

Set up a git filter using nbstripout as follows: ::

    git config filter.nbstripout.clean '/path/to/nbstripout'
    git config filter.nbstripout.smudge cat

Create a file ``.gitattributes`` or ``.git/info/attributes`` with: ::

    *.ipynb filter=nbstripout

Apply the filter for git diff of ``*.ipynb`` files: ::

    git config diff.ipynb.textconv '/path/to/nbstripout -t'

In file ``.gitattributes`` or ``.git/info/attributes`` add: ::

    *.ipynb diff=ipynb
"""

from argparse import ArgumentParser, RawDescriptionHelpFormatter
import collections
import io
import json
from subprocess import check_output, CalledProcessError
import sys
from typing import TextIO
import warnings

from nbstripout._utils import strip_output, strip_zeppelin_output, StripArgs
from nbstripout._installer import install, uninstall, status, INSTALL_LOCATION_LOCAL, INSTALL_LOCATION_GLOBAL, INSTALL_LOCATION_SYSTEM

try:
    # Jupyter >= 4
    from nbformat import read, write, NO_CONVERT
    from nbformat.reader import NotJSONError
except ImportError:
    # IPython 3
    try:
        from IPython.nbformat import read, write, NO_CONVERT
        from IPython.nbformat.reader import NotJSONError
    except ImportError:
        # IPython < 3
        from IPython.nbformat import current
        from IPython.nbformat.reader import NotJSONError

        # Dummy value, ignored anyway
        NO_CONVERT = None

        def read(f, as_version):
            return current.read(f, 'json')

        def write(nb, f):
            return current.write(nb, f, 'json')

__all__ = ["main", "process_stream"]
__version__ = '0.6.1'


def _parse_size(num_str):
    num_str = num_str.upper()
    if num_str[-1].isdigit():
        return int(num_str)
    elif num_str[-1] == 'K':
        return int(num_str[:-1]) * (10**3)
    elif num_str[-1] == 'M':
        return int(num_str[:-1]) * (10**6)
    elif num_str[-1] == 'G':
        return int(num_str[:-1]) * (10**9)
    else:
        raise ValueError(f"Unknown size identifier {num_str[-1]}")


def main():
    parser = ArgumentParser(epilog=__doc__, formatter_class=RawDescriptionHelpFormatter)
    task = parser.add_mutually_exclusive_group()
    task.add_argument('--dry-run', action='store_true',
                      help='Print which notebooks would have been stripped')
    task.add_argument('--install', action='store_true',
                      help='Install nbstripout in the current repository (set '
                      'up the git filter and attributes)')
    task.add_argument('--uninstall', action='store_true',
                      help='Uninstall nbstripout from the current repository '
                      '(remove the git filter and attributes)')
    task.add_argument('--is-installed', action='store_true',
                      help='Check if nbstripout is installed in current repository')
    task.add_argument('--status', action='store_true',
                      help='Print status of nbstripout installation in current '
                      'repository and configuration summary if installed')
    task.add_argument('--version', action='store_true',
                      help='Print version')
    parser.add_argument('--keep-count', action='store_true',
                        help='Do not strip the execution count/prompt number')
    parser.add_argument('--keep-output', action='store_true',
                        help='Do not strip output', default=None)
    parser.add_argument('--extra-keys', default='',
                        help='Space separated list of extra keys to strip '
                        'from metadata, e.g. metadata.foo cell.metadata.bar')
    parser.add_argument('--drop-empty-cells', action='store_true',
                        help='Remove cells where `source` is empty or contains only whitepace')
    parser.add_argument('--drop-tagged-cells', default='',
                        help='Space separated list of cell-tags that remove an entire cell')
    parser.add_argument('--strip-init-cells', action='store_true',
                        help='Remove cells with `init_cell: true` metadata (default: False)')
    parser.add_argument('--attributes', metavar='FILEPATH',
                        help='Attributes file to add the filter to (in '
                        'combination with --install/--uninstall), '
                        'defaults to .git/info/attributes')
    location = parser.add_mutually_exclusive_group()
    location.add_argument('--global', dest='_global', action='store_true',
                          help='Use global git config (default is local config)')
    location.add_argument('--system', dest='_system', action='store_true',
                          help='Use system git config (default is local config)')
    parser.add_argument('--force', '-f', action='store_true',
                        help='Strip output also from files with non ipynb extension')
    parser.add_argument('--max-size', metavar='SIZE',
                        help='Keep outputs smaller than SIZE', default='0')
    parser.add_argument('--mode', '-m', default='jupyter', choices=['jupyter', 'zeppelin'],
                        help='Specify mode between [jupyter (default) | zeppelin] (to be used in combination with -f)')

    parser.add_argument('--textconv', '-t', action='store_true',
                        help='Prints stripped files to STDOUT')

    parser.add_argument('files', nargs='*', help='Files to strip output from')
    args = parser.parse_args()

    git_config = ['git', 'config']

    if args._system:
        git_config.append('--system')
        install_location = INSTALL_LOCATION_SYSTEM
    elif args._global:
        git_config.append('--global')
        install_location = INSTALL_LOCATION_GLOBAL
    else:
        git_config.append('--local')
        install_location = INSTALL_LOCATION_LOCAL

    if args.install:
        raise SystemExit(install(git_config, install_location, attrfile=args.attributes))
    if args.uninstall:
        raise SystemExit(uninstall(git_config, install_location, attrfile=args.attributes))
    if args.is_installed:
        raise SystemExit(status(git_config, install_location, verbose=False))
    if args.status:
        raise SystemExit(status(git_config, install_location, verbose=True))
    if args.version:
        print(__version__)
        raise SystemExit(0)

    extra_keys = [
        'metadata.signature',
        'metadata.widgets',
        'cell.metadata.collapsed',
        'cell.metadata.ExecuteTime',
        'cell.metadata.execution',
        'cell.metadata.heading_collapsed',
        'cell.metadata.hidden',
        'cell.metadata.scrolled',
    ]

    try:
        extra_keys.extend(check_output((git_config if args._system or args._global else ['git', 'config']) + ['filter.nbstripout.extrakeys'], universal_newlines=True).strip().split())
    except (CalledProcessError, FileNotFoundError):
        pass

    extra_keys.extend(args.extra_keys.split())

    strip_args = StripArgs(args.keep_output, args.keep_count, extra_keys, args.drop_empty_cells, args.drop_tagged_cells.split(), args.strip_init_cells, _parse_size(args.max_size))

    if args.files:
        for filename in args.files:
            if not (args.force or filename.endswith('.ipynb') or filename.endswith('.zpln')):
                continue
            process_file(filename, args.mode, args.dry_run, args.textconv, strip_args)
    else:
        process_stream(strip_args)


def process_file(filename, mode: str, dry_run: bool, textconv: bool, strip_args: StripArgs):
    try:
        with io.open(filename, 'r', encoding='utf8') as f:
            if mode == 'zeppelin' or filename.endswith('.zpln'):
                if dry_run:
                    sys.stderr.write(f'Dry run: would have stripped {filename}\n')
                    return
                nb = json.load(f, object_pairs_hook=collections.OrderedDict)
                nb_stripped = strip_zeppelin_output(nb)

                with open(filename, 'w') as f:
                    json.dump(nb_stripped, f, indent=2)
                return
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                nb = read(f, as_version=NO_CONVERT)

        nb = strip_output(nb, strip_args)

        if dry_run:
            sys.stderr.write(f'Dry run: would have stripped {filename}\n')

            return

        if textconv:
            output_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', newline='')
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                write(nb, output_stream)
            output_stream.flush()
        else:
            with io.open(filename, 'w', encoding='utf8', newline='') as f:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=UserWarning)
                    write(nb, f)
    except NotJSONError:
        print(f"'{filename}' is not a valid notebook", file=sys.stderr)
        raise SystemExit(1)
    except FileNotFoundError:
        print(f"Could not strip '{filename}': file not found", file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        # Ignore exceptions for non-notebook files.
        print(f"Could not strip '{filename}'", file=sys.stderr)
        raise


def process_stream(force: bool, mode: str, dry_run: bool, textconv: bool, strip_args: StripArgs):
    if not sys.stdin:
        raise ValueError("No files supplied, and could not open STDIN for reading")

    # Wrap input/output stream in UTF-8 encoded text wrapper
    # https://stackoverflow.com/a/16549381
    input_stream = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
    output_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', newline='')

    try:
        if mode == 'zeppelin':
            if dry_run:
                sys.stderr.write('Dry run: would have stripped input from stdin\n')
                raise SystemExit(0)
            nb = json.load(input_stream, object_pairs_hook=collections.OrderedDict)
            nb_stripped = strip_zeppelin_output(nb)
            json.dump(nb_stripped, output_stream, indent=2)
            output_stream.write('\n')
            output_stream.flush()
            raise SystemExit(0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            nb = read(input_stream, as_version=NO_CONVERT)

        nb = strip_output(nb, strip_args)

        if dry_run:
            output_stream.write('Dry run: would have stripped input from '
                                'stdin\n')
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                write(nb, output_stream)

            output_stream.flush()
    except NotJSONError:
        print('No valid notebook detected', file=sys.stderr)
        raise SystemExit(1)


def load_zepellin(input_stream: TextIO):
    return json.load(input_stream, object_pairs_hook=collections.OrderedDict)


def write_zepellin(nb, output_stream: TextIO):
    json.dump(nb, output_stream, indent=2)
    output_stream.flush()


def load_jupyter(input_stream: TextIO):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=UserWarning)
        return read(input_stream, as_version=NO_CONVERT)


def write_jupyter(nb, output_stream: TextIO):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=UserWarning)
        write(nb, output_stream)
    output_stream.flush()
