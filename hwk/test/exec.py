# (c) 2016-7 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import builtins
import sys
import ast
import imp
import os.path
from typing import Any, Iterable, NamedTuple

from ..util.common import StringReadIO, StringWriteIO

__all__ = [
  'ScriptResult', 'runScript', 'runScriptFromString',
  'parseScript',
  'expand_path', 'expand_path_ext',
]


def expand_path(filename: str) -> str:
  # type: (str) -> str
  """Search in PYTHONPATH for the filename, in order.
    Returns full pathname if found, or the filename itself otherwise.

    :param filename: The filename to search for.
    :type filename: str
    :returns: str -- The full pathname, if the filename is found in
      the PYTHONPATH, or the filename value itself otherwise."""
  for dirname in sys.path:
    pathname = os.path.join(dirname, filename)
    if os.path.isfile(pathname):
      return pathname
  else:
    return filename


def expand_path_ext(filename: str, extensions: list[str]) -> str:
  # type (str, list) -> str
  """Same as expand_path, but also tries with all extensions in list appended.
  Will first try appending each extension (in the order given) and finally
  fall back to the bare filename.

  :param filename: The filename to search for; if it already has an extension,
    that will be kept (in addition to those in the extensions list).
  :type filename: str
  :param extensions: The list of filename extensions to try appending;
    must include extension separator, if necessary.
  :type extensions: iter(str)
  :returns: str -- The full pathname, otherwise the filename itself."""
  # First, try to expand path with each extension appended
  for ext in extensions:
    filename_ext = "%s%s" % (filename, ext)
    pathname_ext = expand_path(filename_ext)
    if pathname_ext != filename_ext:
      return pathname_ext
  # If none works, try bare filename
  return expand_path(filename)


class ScriptResult(NamedTuple):
  stdout: str
  stderr: str | None = None
  exit_code: int = 0
  ns: dict[str, Any] | None = None
  input_prompts: tuple[str] = ()

  def __str__(self):
    return self.stdout


# http://stackoverflow.com/questions/5136611  (capture stdout)
# http://stackoverflow.com/questions/11170949 (clone and patch module)
def runScriptFromString(script: str, args: Iterable = (), **kwargs) -> ScriptResult:  # noqa: N802
  # type: (str, object, object) -> str
  """
  Runs a Python script, capturing standard output and, optionally,
  setting a random seed for the random library.

  :param script: The script program text
  :type script: str
  :keyword seed: Random seed for built-in random library
  :type seed: int
  :keyword pathname:
  :keyword mock_random:
  :keyword ns_extra:
  :keyword stdin:
  :keyword return_ns: Default False.
  :keyword capture_stderr: Default True
  """
  # TODO? Add options to (a) not capture stderr?
  pathname = kwargs.get('pathname', '<input>')
  save_stdin, save_stdout, save_stderr = sys.stdin, sys.stdout, sys.stderr
  save_argv = sys.argv
  save_input = builtins.input
  # As of PyCharm 2017.1, the new test runners also capture sys.stdout
  # (to a StringIO instance), so
  #   sysmodule = imp.load_module('sys', *imp.find_module('sys'))
  # has the effect of reloading sys and messing things up.
  sysmodule = sys  # imp.load_module('sys', *imp.find_module('sys')) ##sys
  # TODO - See if we can turn off stdout capture in the test runner instead
  try:
    exit_code = 0  # default
    # Capture standard streams
    sysmodule.stdin = StringReadIO(kwargs.get('stdin', ''))
    sysmodule.stdout = StringWriteIO()
    capture_stderr = kwargs.get('capture_stderr', True)
    if capture_stderr:
      sysmodule.stderr = StringWriteIO()
    # Set sys.argv
    sysmodule.argv = [pathname] + list(args)
    # Capture input() builtin prompts
    input_prompts = []
    def _input_thunk(prompt: str = '', /) -> str:
      # We can safely assume we're not running on a tty (since we capture stdout...)
      nonlocal input_prompts
      if prompt:
        input_prompts.append(prompt)
      return save_input()
    builtins.input = _input_thunk
    ns = {'__name__': '__main__', 'sys': sysmodule, }
    if 'seed' in kwargs:
      rndmodule = imp.load_module('random', *imp.find_module('random'))
      rndmodule.seed(kwargs['seed'])
      ns['random'] = rndmodule
    if 'mock_random' in kwargs:
      assert 'seed' not in kwargs, "Cannot specify both seed and fakerandom"
      from . import mock_random
      rndmodule = imp.load_module('random', *imp.find_module('random'))
      mock_random.patch_random(
        rndmodule,
        mock_random.MockCircularRandom(
          kwargs['mock_random'],
          kwargs.get('mock_normalize', None))
        )
      ns['random'] = rndmodule
    if 'ns_extra' in kwargs:
      ns.update(kwargs['ns_extra'])
    code = compile(script, pathname, 'exec')
    try:
      exec(code, ns, ns)
    except SystemExit as ex:
      # Don't let sys.exit() terminate entire program (e.g., unit test)
      exit_code = ex.code
    return ScriptResult(
      stdout=sysmodule.stdout.getvalue(),
      stderr=sysmodule.stderr.getvalue() if capture_stderr else None,
      exit_code=exit_code,
      ns=ns if kwargs.get('return_ns', False) else None,
      input_prompts=tuple(input_prompts),
    )
  finally:
    # For some reason, these need to be restored, even if sys
    # is not imported globally (unlike, e.g., the 'random' module seed)
    sys.stdin, sys.stdout, sys.stderr = save_stdin, save_stdout, save_stderr
    sys.argv = save_argv
    builtins.input = save_input
    del sysmodule, ns
    if 'seed' in kwargs:
      del rndmodule


def runScript(filename: str, *args, **kwargs) -> ScriptResult:  # noqa: N802
  """Runs a Python script, capturing standard output and, optionally,
  setting a random seed for the random library.

  :param filename: The script filename; must be in PYTHONPATH
  :type filename: str
  :keyword seed: Random seed for built-in random library
  :type seed: int"""
  pathname = expand_path(filename)
  with open(pathname) as fp:
    kwargs['pathname'] = pathname
    return runScriptFromString(fp.read(), args, **kwargs)


def parseScript(filename: str) -> ast.Module:  # noqa: N802
  """Loads the AST of the given script file."""
  pathname = expand_path(filename)
  with open(pathname) as fp:
    return ast.parse(fp.read(), filename=pathname)
