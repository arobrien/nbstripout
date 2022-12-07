from os import devnull, environ, makedirs, path
from pathlib import PureWindowsPath
import re
from subprocess import call, check_call, check_output, CalledProcessError, STDOUT
import sys

__all__ = ["install", "uninstall", "status", "INSTALL_LOCATION_LOCAL", "INSTALL_LOCATION_GLOBAL", "INSTALL_LOCATION_SYSTEM"]


INSTALL_LOCATION_LOCAL = 'local'
INSTALL_LOCATION_GLOBAL = 'global'
INSTALL_LOCATION_SYSTEM = 'system'


def _get_system_gitconfig_folder():
    try:
        git_config_output = check_output(['git', 'config', '--system', '--list', '--show-origin'], universal_newlines=True, stderr=STDOUT).strip()

        # If the output is empty, it means the file exists but is empty, so we cannot get the path.
        # To still get it, we're setting a temporary config parameter.
        if git_config_output == '':
            check_call(['git', 'config', '--system', 'filter.nbstripoutput.test', 'test'])
            git_config_output = check_output(['git', 'config', '--system', '--list', '--show-origin'], universal_newlines=True).strip()
            check_call(['git', 'config', '--system', '--unset', 'filter.nbstripoutput.test'])

        output_lines = git_config_output.split('\n')

        system_gitconfig_file_path = re.sub(r'^file:', '', output_lines[0].split('\t')[0])
    except CalledProcessError as e:
        git_config_output = e.output

        system_gitconfig_file_path = re.match(r"fatal:.*file '([^']+)'.*", git_config_output).group(1)

    return path.abspath(path.dirname(system_gitconfig_file_path))


def _get_attrfile(git_config, install_location=INSTALL_LOCATION_LOCAL, attrfile=None):
    if not attrfile:
        if install_location == INSTALL_LOCATION_SYSTEM:
            try:
                attrfile = check_output(git_config + ['core.attributesFile'], universal_newlines=True).strip()
            except CalledProcessError:
                config_dir = _get_system_gitconfig_folder()
                attrfile = path.join(config_dir, 'gitattributes')
        elif install_location == INSTALL_LOCATION_GLOBAL:
            try:
                attrfile = check_output(git_config + ['core.attributesFile'], universal_newlines=True).strip()
            except CalledProcessError:
                config_dir = environ.get('XDG_CONFIG_DIR', path.expanduser('~/.config'))
                attrfile = path.join(config_dir, 'git', 'attributes')
        else:
            git_dir = check_output(['git', 'rev-parse', '--git-dir'], universal_newlines=True).strip()
            attrfile = path.join(git_dir, 'info', 'attributes')

    attrfile = path.expanduser(attrfile)
    if path.dirname(attrfile):
        makedirs(path.dirname(attrfile), exist_ok=True)

    return attrfile


def install(git_config, install_location=INSTALL_LOCATION_LOCAL, attrfile=None):
    """Install the git filter and set the git attributes."""
    try:
        filepath = f'"{PureWindowsPath(sys.executable).as_posix()}" -m nbstripout'
        check_call(git_config + ['filter.nbstripout.clean', filepath])
        check_call(git_config + ['filter.nbstripout.smudge', 'cat'])
        check_call(git_config + ['diff.ipynb.textconv', filepath + ' -t'])
        attrfile = _get_attrfile(git_config, install_location, attrfile)
    except FileNotFoundError:
        print('Installation failed: git is not on path!', file=sys.stderr)
        return 1
    except CalledProcessError:
        print('Installation failed: not a git repository!', file=sys.stderr)
        return 1

    # Check if there is already a filter for ipynb files
    filt_exists = False
    zeppelin_filt_exists = False
    diff_exists = False

    if path.exists(attrfile):
        with open(attrfile, 'r') as f:
            attrs = f.read()

        filt_exists = '*.ipynb filter' in attrs
        zeppelin_filt_exists = '*.zpln filter' in attrs
        diff_exists = '*.ipynb diff' in attrs

        if filt_exists and diff_exists:
            return

    try:
        with open(attrfile, 'a', newline='') as f:
            # If the file already exists, ensure it ends with a new line
            if f.tell():
                f.write('\n')
            if not filt_exists:
                print('*.ipynb filter=nbstripout', file=f)
            if not zeppelin_filt_exists:
                print('*.zpln filter=nbstripout', file=f)
            if not diff_exists:
                print('*.ipynb diff=ipynb', file=f)
    except PermissionError:
        print(f'Installation failed: could not write to {attrfile}', file=sys.stderr)

        if install_location == INSTALL_LOCATION_GLOBAL:
            print('Did you forget to sudo?', file=sys.stderr)

        return 1


def uninstall(git_config, install_location=INSTALL_LOCATION_LOCAL, attrfile=None):
    """Uninstall the git filter and unset the git attributes."""
    try:
        call(git_config + ['--unset', 'filter.nbstripout.clean'], stdout=open(devnull, 'w'), stderr=STDOUT)
        call(git_config + ['--unset', 'filter.nbstripout.smudge'], stdout=open(devnull, 'w'), stderr=STDOUT)
        call(git_config + ['--remove-section', 'diff.ipynb'], stdout=open(devnull, 'w'), stderr=STDOUT)
        attrfile = _get_attrfile(git_config, install_location, attrfile)
    except FileNotFoundError:
        print('Uninstall failed: git is not on path!', file=sys.stderr)
        return 1
    except CalledProcessError:
        print('Uninstall failed: not a git repository!', file=sys.stderr)
        return 1

    # Check if there is a filter for ipynb files
    if path.exists(attrfile):
        with open(attrfile, 'r+') as f:
            patterns = ('*.ipynb filter', '*.zpln filter', '*.ipynb diff')
            lines = [line for line in f if not any(line.startswith(p) for p in patterns)]
            f.seek(0)
            f.write(''.join(lines))
            f.truncate()


def status(git_config, install_location=INSTALL_LOCATION_LOCAL, verbose=False):
    """Return 0 if nbstripout is installed in the current repo, 1 otherwise"""
    try:
        if install_location == INSTALL_LOCATION_SYSTEM:
            location = 'system-wide'
        elif install_location == INSTALL_LOCATION_GLOBAL:
            location = 'globally'
        else:
            git_dir = path.dirname(path.abspath(check_output(['git', 'rev-parse', '--git-dir'], universal_newlines=True).strip()))
            location = f"in repository '{git_dir}'"

        clean = check_output(git_config + ['filter.nbstripout.clean'], universal_newlines=True).strip()
        smudge = check_output(git_config + ['filter.nbstripout.smudge'], universal_newlines=True).strip()
        diff = check_output(git_config + ['diff.ipynb.textconv'], universal_newlines=True).strip()

        if install_location in {INSTALL_LOCATION_SYSTEM, INSTALL_LOCATION_GLOBAL}:
            attrfile = _get_attrfile(git_config, install_location)
            attributes = ''
            diff_attributes = ''

            if path.exists(attrfile):
                with open(attrfile, 'r') as f:
                    attrs = f.readlines()
                attributes = ''.join(line for line in attrs if 'filter' in line).strip()
                diff_attributes = ''.join(line for line in attrs if 'diff' in line).strip()
        else:
            attributes = check_output(['git', 'check-attr', 'filter', '--', '*.ipynb'], universal_newlines=True).strip()
            diff_attributes = check_output(['git', 'check-attr', 'diff', '--', '*.ipynb'], universal_newlines=True).strip()

        try:
            extra_keys = check_output(git_config + ['filter.nbstripout.extrakeys'], universal_newlines=True).strip()
        except CalledProcessError:
            extra_keys = ''

        if attributes.endswith('unspecified'):
            if verbose:
                print('nbstripout is not installed', location)

            return 1

        if verbose:
            print('nbstripout is installed', location)
            print('\nFilter:')
            print('  clean =', clean)
            print('  smudge =', smudge)
            print('  diff=', diff)
            print('  extrakeys=', extra_keys)
            print('\nAttributes:\n ', attributes)
            print('\nDiff Attributes:\n ', diff_attributes)

        return 0
    except FileNotFoundError:
        print('Cannot determine status: git is not on path!', file=sys.stderr)

        return 1
    except CalledProcessError:
        if verbose and 'location' in locals():
            print('nbstripout is not installed', location)

        return 1
