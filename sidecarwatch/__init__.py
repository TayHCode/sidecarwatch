APP_NAME = "sidecarwatch"
__version__ = "0.1.0"
PREFIX = APP_NAME.upper() + "_"
LABEL = "[AUTOMATED " + APP_NAME + " reply "

def entry():
    import os
    import sys
    if sys.version_info < (3, 11):
        print('sidecarwatch requires Python 3.11 or newer. Use uv with --python 3.12.', file=sys.stderr)
        sys.exit(2)
    if os.name != 'posix':
        print('Native Windows is unsupported; macOS and Linux only. WSL is untested.', file=sys.stderr)
        sys.exit(2)
    from .cli import entry as cli_entry
    cli_entry()
