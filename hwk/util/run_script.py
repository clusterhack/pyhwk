# (c) 2016-2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

from typing import Optional, List
from types import TracebackType

import sys
import os
import re
import traceback
import shlex
import contextlib
import warnings

from ..test.exec import runScript, expand_path
from .common import msg, vscode_load_settings, c, Color


# Available variables for prompt string specs:
#  scriptfile: Basename of script filename
#  scriptpath: Full pathname of script filename
#  pythonexec: Full pathname of python interpreter executable
#  cwd: current working directory
#  shellps: shell prompt (simulated)
_DEFAULT_PS0 = "Please fill in (or modify) command-line argument values:\n"
_DEFAULT_PS1 = f"{c(2)}{{shellps}}{c()} python {{scriptfile}} "


def print_warning(message: str, *, title: str = 'WARNING', color: Color = 3) -> None:
  msg()
  msg(f'{c(color)}┌──────────────────────────────────────────────────────────────────────┐{c()}')
  msg(f'{c(color)}│                   ⚠   ⚠   ⚠  {title[:9]: ^9s}  ⚠   ⚠   ⚠                    │{c()}')
  msg(f'{c(color)}│ {message[:68]: ^68s} │{c()}')
  msg(f'{c(color)}└──────────────────────────────────────────────────────────────────────┘{c()}')
  msg()

try:
  import readline
  
  _HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".fbp_run_script_history")
  _HISTORY_LENGTH = 400  # global limit

  @contextlib.contextmanager
  def rl_autocomplete(scriptpath: str):
    # Load history for particular script;
    # can't use readline.read_history_file() since we need to filter file entries
    try:
      history_lines = []
      with open(_HISTORY_FILE, 'r') as fp:
        for line in fp:
          line = line.rstrip()
          history_lines.append(line)
          if line.startswith(scriptpath + " "):
            readline.add_history(line[len(scriptpath)+1:])
    except FileNotFoundError:
      pass

    readline.parse_and_bind("tab: complete")
    readline.set_auto_history(True)  # Only CPython guarantees it's already set
    rl_history_len0 = readline.get_current_history_length()

    try:
      yield
    finally:
      # Save updated history
      rl_history_len = readline.get_current_history_length()
      for index in range(rl_history_len0+1, rl_history_len+1):
        item = readline.get_history_item(index)  # XXX index is 1-based !!
        if item is not None:
          history_lines.append(f"{scriptpath} {item}")
      try:
        with open(_HISTORY_FILE, 'w') as fp:
          for line in history_lines[-_HISTORY_LENGTH:]:
            print(line, file=fp)
      except OSError:
        warnings.warn("Failed to save command-line argument history!")

  def rl_init(config: Optional[dict] = None):
    # Initialize rl buffer with either latest history entry or, if configured, default arg value
    rl_history_len = readline.get_current_history_length()
    if rl_history_len > 0:
      initial_args = readline.get_history_item(rl_history_len)
    else:
      initial_args = config and config.get('defaultArgs', None)
    if initial_args is not None:
      readline.set_startup_hook(lambda: readline.insert_text(initial_args))

  def rl_cleanup():
    readline.set_startup_hook()

except ImportError:  # import readline
  @contextlib.contextmanager
  def rl_autocomplete(scriptpath: str):
    yield

  def rl_init(config: Optional[dict] = None):
    pass

  def rl_cleanup():
    pass


def get_arguments(
  filename: str, ps1: str = _DEFAULT_PS1, ps0: Optional[str] = _DEFAULT_PS0,
  config: Optional[dict] = None,
) -> List[str]:
  pathname = expand_path(filename)
  ps_vars = dict(
    scriptfile=os.path.basename(pathname),
    scriptpath=pathname,
    cwd=os.getcwd(),
    pythonexec=sys.executable,
    shellps="$" if sys.platform != "win32" else ">",
  )
  if ps0 is not None:
    ps0 = ps0.format(**ps_vars)
    print(ps0, end='')
  ps1 = ps1.format(**ps_vars)
  try:
    with rl_autocomplete(pathname):
      rl_init(config)
      # Read user input line
      argstr = input(ps1)
    # Split arguments (and parse simple shell escapes) and return list
    return shlex.split(argstr)
  finally:
    rl_cleanup()


def tb_depth(tb: TracebackType) -> int:
  depth = 0
  while tb is not None:
    depth += 1
    tb = tb.tb_next
  return depth


def print_traceback() -> None:
  _, _, tb = ei = sys.exc_info()
  try:
    lines = traceback.format_exception(ei[0], ei[1], tb, limit=-max(tb_depth(tb)-3, 0))
    msg(''.join(lines))
  finally:
    tb = ei = None


def run_script(filename: str, config: Optional[dict] = None) -> None:
  # XXX Consider code.InteractiveInterpreter as an alternative to runScript?
  try:
    args = get_arguments(filename, config=config)
  except KeyboardInterrupt:
    msg('\nAborted!')
    return
  except EOFError:
    return
  try:
    print(runScript(filename, *args), end='')
  except:
    print_traceback()


def main() -> None:
  filename = sys.argv[1]

  if re.match(r'.*[/\\]tests[/\\].+\.py$', filename):
    print_warning('Unit tests cannot be executed via "Run in Terminal"', title='ERROR', color=1)
    sys.exit(1)

  print_warning('Using the "Run Python File in Terminal" button is *not* recommended!')
  config = vscode_load_settings('runscript.json')
  if config is not None:
    config = config.get(os.path.basename(filename), None)
  run_script(filename, config=config)


if __name__ == '__main__':
  main()
