"""Tests use synthetic local data and cannot access the network."""
import sys
from pathlib import Path
import shutil

BLOCK_PROCESSES = False

def guard(event, args):
    if event in ('socket.connect', 'socket.connect_ex', 'socket.bind'):
        raise AssertionError('network blocked in tests')
    if BLOCK_PROCESSES and event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork'):
        raise AssertionError('process blocked in historical characterization')
    if event == 'subprocess.Popen':
        executable = str(args[0])
        name = Path(executable).name
        if name in ('sneako'+'scope','send','lanes','thread','start','watch','keepalive'):
            raise AssertionError('historical command blocked')
        if name in ('claude','codex'):
            resolved=shutil.which(executable)
            if resolved:
                fixture=Path(__file__).parent/'fixtures'/'bin'/name
                if Path(resolved).read_bytes()!=fixture.read_bytes():
                    raise AssertionError('real CLI blocked')

sys.addaudithook(guard)
