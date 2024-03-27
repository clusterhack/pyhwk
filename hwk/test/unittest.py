# (c) 2016- Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import os
import sys
import unittest

from .exec import runScript, runScriptFromString, parseScript, expand_path, expand_path_ext
from contextlib import contextmanager
from typing import Any, Optional, Union, Iterable, Sequence, ClassVar, List, Tuple, Dict
import pickle

import inspect
import importlib

__all__ = ["HomeworkTestCase", "try_import", "HomeworkModuleTestCase"]


# Function that tries to import either a module (similar to "import ...") or
# individual names from a module (similar to "from ... import ...").
#
# The difference from an import statement is that if the module or names
# do not exist, then the corresponding names in the caller's namespace will
# still be created, by will be set to None (rather than raising an exception).
#
# This function is meant for homework unit tests in VSCode; the test explorer
# will show any tests which do not import properly, and that is confusing to
# most students.  Therefore, we want the import of solutions to silently fail,
# so VSCode test explorer still displays the unit test in it's list.
#
# The name of a variable to save exceptions that occured during the import 
# may be optionally provided.
#
# Finally, a dict of the namespace in which names will be set may be given
# explicitly.  If not given explicitly, then the call *must* occur at the global
# level and the globals of the caller will be used.
def try_import(
  moduleName: str,
  names: Optional[Union[str, List[str]]] = None,
  *,
  excname: Optional[str] = None,
  nsdict: Optional[Dict[str,Any]] = None,
) -> None:
  caller_frameinfo = None
  try:
    if nsdict is None:
      caller_frameinfo = inspect.stack()[1]
      if caller_frameinfo.frame.f_locals is not caller_frameinfo.frame.f_globals:
        raise ImportError("try_import() can only be used at module scope")
      nsdict = caller_frameinfo.frame.f_globals
    try:
      mod = importlib.import_module(moduleName)
      if excname is not None:
        nsdict[excname] = None  # Create attr, to simplify checks
    except Exception as exc:
      mod = None
      if excname is not None:
        nsdict[excname] = exc
    if names is None:
      # Import entire module
      nsdict[moduleName] = mod
    else:
      if type(names) is str:
        names = [names]
      if type(names) is not list:
        raise TypeError("names must be either a string or a list (of strings)")
      # Import individual names from within module
      for n in names:
        nsdict[n] = getattr(mod, n, None)  # if mod is None, this still works
  finally:
    if caller_frameinfo is not None:
      del caller_frameinfo  # Ensure no reference cycles


class HomeworkTestCase(unittest.TestCase):
  __scriptname__: ClassVar[Optional[str]] = None   # Filename of solution script (.py)

  def assertAlmostIn(self, number: float, container: Iterable[float], places: int = 7, msg: Optional[str] = None):  # noqa: N802
    """
    Similar to assertAlmostEquals, but checks for equality to one of several possible values.

      :param number: The number to search for
      :type number: float
      :param container: A container of candidate float values
      :type container: any type that supports iteration"""
    for expected in container:
      if round(abs(expected-number), places) == 0:
        return
    else:
      raise self.failureException(
        (msg or '%r != any of %r within %r places' % (number, container, places)))

  def assertLengthEqual(self, length: int, seq: Sequence, msg: str = None):
    if not hasattr(seq, '__len__'):
      raise self.failureException('%r does not have length' % seq)
    if len(seq) != length:
      raise self.failureException('Expected length %d, got length %d' % (length, len(seq)))

  def assertListAlmostEqual(self, first, second, places=7, msg=None):  # noqa: N802
    if not isinstance(first, list):
      raise self.failureException('%r is not a list' % first)
    if not isinstance(second, list):
      raise self.failureException('%r is not a list' % second)
    if len(first) != len(second):
      raise self.failureException('list lengths differ (%r vs %r)' %
                                  (len(first), len(second)))
    not_almost_equal = [round(abs(x - y), places) != 0
                        for x, y in zip(first, second)]
    if any(not_almost_equal):
      pos = not_almost_equal.index(True)
      raise self.failureException(
        (msg or 'values at index %r are not equal (%r vs %r)' %
        (pos, first[pos], second[pos]))
      )

  def assertArrayEquals(self, first, second, places=7, msg=None):  # noqa: N802
    import numpy as np  # Only where necessary; avoid barfing if not installed
    if not isinstance(first, np.ndarray):
      raise self.failureException('%r is not a NumPy array' % first)
    if not isinstance(second, np.ndarray):
      raise self.failureException('%r is not a NumPy array' % second)
    if first.shape != second.shape:
      raise self.failureException(
        'shapes %r and %r cannot be compared' % (first.shape, second.shape))
    if (np.issubdtype(first.dtype, np.float_) or
      np.issubdtype(second.dtype, np.float_)
    ):
      fail_cond = np.round(np.abs(first-second), places) != 0
    else:
      fail_cond = first != second
    if np.any(fail_cond):
      pos = tuple(a[0] for a in fail_cond.nonzero())
      raise self.failureException(
        (msg or 'values at %r differ: %r != %r' %
        (list(pos), first[pos], second[pos]))
      )

  def assertArrayElementType(self, dtype, array, msg=None):  # noqa: N802
    import numpy as np  # Only where necessary; avoid barfing if not installed
    if not np.issubdtype(array.dtype, dtype):
      raise self.failureException(
        (msg or 'array type %r is not of expected type %r' % (array.dtype, dtype,)))

  def assertArrayWithin(self, first, second, max_deviation, msg=None):  # noqa: N802
    import numpy as np  # Only where necessary; avoid barfing if not installed
    # fail_cond is written so it works even when first and/or second are unsigned int types
    fail_cond = ((first > second) & (first - second > max_deviation)) | (second - first > max_deviation)
    if np.any(fail_cond):
      pos = tuple(a[0] for a in fail_cond.nonzero())
      raise self.failureException(
        (msg or 'values at %r differ by more than %r: %r vs %r' %
        (list(pos), max_deviation, first[pos], second[pos]))
      )

  def assertArrayNormWithin(self, first, second=None, limit=0.0, order=2.0, msg=None):  # noqa: N802
    import numpy as np  # Only where necessary; avoid barfing if not installed
    if second is None:
      diff = np.linalg.norm(first, ord=order)
    else:
      diff = np.linalg.norm(first - second, ord=order)
    if diff > limit:
      raise self.failureException(
        (msg or 'arrays more than %r away (actual: %r)' % (limit, diff)))

  def assertListIdenticalElements(self, first, second, msg=None):  # noqa: N802
    if not isinstance(first, list):
      raise self.failureException('%r is not a list' % first)
    if not isinstance(second, list):
      raise self.failureException('%r is not a list' % second)
    if len(first) != len(second):
      raise self.failureException('list lengths differ (%r vs %r)' % (len(first), len(second)))
    not_identical = [x is not y for x, y in zip(first, second)]
    if any(not_identical):
      pos = not_identical.index(True)
      raise self.failureException(
        msg or 'values at index %r are not identical (though they may be equal...)' % pos
      )

  def loadTestData(self, name: str):  # Returns a dictionary or list  # noqa: N802
    from gzip import GzipFile
    from bz2 import BZ2File
    # Pickle file is expected to follow the following simple format:
    # 1. A boolean, has_names, indicating if key-value pairs follow, or just values
    # 2. Depending on has_names, either string-object pairs, or just objects

    def do_load(fp):
      has_names = pickle.load(fp)
      if not isinstance(has_names, bool):
        # Even though this should not be happening, gracefully handle this
        # as a value-sequence array, and include the first object as a value
        data = [has_names]
        has_names = False
      elif not has_names:
        data = []
      else:
        data = {}
      # Loop through pickles
      while True:
        try:
          obj = pickle.load(fp)
        except EOFError:
          break
        if has_names:
          assert isinstance(obj, str)
          assert obj not in data
          val = pickle.load(fp)  # EOF here is an error
          data[obj] = val
        else:
          data.append(obj)
      return data
    # Function body
    filename = expand_path_ext(
      os.path.join('data', 'test', name), ('.p.gz', '.p.bz2', '.p')
    )
    if filename.endswith('.gz'):
      with GzipFile(filename, 'rb') as zfp:
        return do_load(zfp)
    elif filename.endswith('.bz2'):
      with BZ2File(filename, 'rb') as zfp:
        return do_load(zfp)
    else:
      with open(filename, 'rb') as fp:
        return do_load(fp)

  def saveTestData(self, filename, data):  # noqa: N802
      if not filename.lower().endswith('.p'):
        filename += '.p'
      with open(filename, 'wb') as fp:
        if isinstance(data, dict):
          pickle.dump(True, fp, protocol=pickle.HIGHEST_PROTOCOL)
          for k, v in data.items():
            pickle.dump(k, fp, protocol=pickle.HIGHEST_PROTOCOL)
            pickle.dump(v, fp, protocol=pickle.HIGHEST_PROTOCOL)
        elif isinstance(data, list):
          pickle.dump(False, fp, protocol=pickle.HIGHEST_PROTOCOL)
          for v in data:
            pickle.dump(v, fp, protocol=pickle.HIGHEST_PROTOCOL)
        else:
          raise ValueError("Unsupported data type: must be dict or list")

  @contextmanager
  def randomSeed(self, seed):  # noqa: N802
    import random
    state = random.getstate()
    random.seed(seed)
    try:
      yield
    finally:
      random.setstate(state)

  @contextmanager
  def mockRandom(self, values, normalize=None):  # noqa: N802
    from . import mock_random
    with mock_random.mock_random(values, normalize):
      yield

  @contextmanager
  def assertUnmodified(self, *args, deep=False):  # noqa: N802
    if not args:
      raise TypeError("assertUnmodified() requires at least one argument")
    import copy
    copy_fn = copy.deepcopy if deep else copy.copy
    arg_copies = [copy_fn(a) for a in args]
    if len(args) == 1:
      yield args[0]  # "Unwrap" singleton, for convenience
    else:
      yield args
    for a, a_copy in zip(args, arg_copies):
      self.assertEqual(
        a, a_copy,
        msg=f"Function argument(s) improperly modified, from {a_copy!r} to {a!r}"
      )

  @contextmanager
  def assertAccesses(self, ref: Any, module_names: Optional[List] = None, msg: Optional[str] = None):  # noqa: N802
    """Deletes all module-level names that reference given object, and
    expects a failure with either NameError or AttributeError"""
    # Delete all module-level references
    saved_names = {}  # keyed by module name, list of deleted names
    # XXX We need to make a copy of sys.modules.items(), since accessing certain module attrs
    #   triggers imports (notably, accessing ProcessPoolExecutor or ThreadPoolExecutor in
    #   concurrent.futures, via custom __getattr__ override in module---yes, that's possible,
    #   who knew..!)  However, homework modules should have already been imported by this point...
    if module_names is None:
      module_items = tuple(sys.modules.items())
    else:
      module_items = tuple((modname, mod) for (modname, mod) in sys.modules.items() if modname in module_names)
    for modname, mod in module_items:
      for varname in dir(mod):
        print(f"DEBUG: About to getattr {modname}.{varname}")
        if getattr(mod, varname) is ref:
          if modname not in saved_names:
            saved_names[modname] = []
          saved_names[modname].append(varname)
          delattr(mod, varname)
    # new_module_names = set(sys.modules.keys()).difference(name for name, _ in module_items)
    # print(
    #   "DEBUG: Introspecting all module attrs triggered " +
    #   f"{len(new_module_names)} new imports: {' '.join(new_module_names)}",
    #   file=sys.stderr
    # )
    # print("DEBUG: Deleted names", saved_names, file=sys.stdout)

    try:
      with self.assertRaises((NameError, AttributeError)):
        yield
    finally:
      # Restore all deleted references
      for modname in saved_names:
        for varname in saved_names[modname]:
          print(f"DEBUG: About to setattr varname {varname}")
          setattr(sys.modules[modname], varname, ref)

  @contextmanager
  def assertWithoutBuiltin(self, fn_name: str):
    # XXX Be *very* careful to limit context as much as possible; also
    #   we intentionally don't allow altering more than one builtin at once...
    import builtins
    saved_fn = getattr(builtins, fn_name)
    # TODO? Verify saved_fn is actually a function
    def fail(*args, **kwargs):
      raise self.failureException(f"Called built-in {fn_name}() function")
    try:
      setattr(builtins, fn_name, fail)
      yield
    finally:
      setattr(builtins, fn_name, saved_fn)

  def runScript(self, filename: Optional[str] = None, *args, **kwargs):  # noqa: N802
    filename = filename or self.__scriptname__
    if filename:
      return runScript(filename, *args, **kwargs)
    else:
      raise RuntimeError("Must specify a script filename")

  def runScriptFromString(self, script, args=(), **kwargs):  # noqa: N802
    return runScriptFromString(script, args, **kwargs)

  def parseScript(self, script):  # noqa: N802
    return parseScript(script)

  # TODO Argument to optionally allow top-level assignments (e.g., for constants?)
  def assertLibraryModule(self, script, msg=None):
    import ast
    script_ast = self.parseScript(script)
    # Trivially succeed on empty files
    if len(script_ast.body) == 0:
      return
    # Kludge to turn off test: first statement is FORCE_LIBRARY_MODULE = True
    first_stmt = script_ast.body[0]
    if (isinstance(first_stmt, ast.Assign) and 
        len(first_stmt.targets) == 1 and isinstance(first_stmt.targets[0], ast.Name) and first_stmt.targets[0].id == "FORCE_LIBRARY_MODULE" and
        isinstance(first_stmt.value, ast.Constant) and first_stmt.value.value is True):
      return  # Bypass this assertion test; used for some reference solutions (e.g., with alternatives)
    # Check that top-level statements are limited to imports and defs
    if all(
      isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom) or isinstance(node, ast.FunctionDef)
      for node in ast.iter_child_nodes(script_ast)
    ):
      return
    # Allow one if statement... 
    if 1 != sum(isinstance(node, ast.If) for node in ast.iter_child_nodes(script_ast)):
      raise self.failureException(msg or f"Top-level statements in {script} aren't limited to import and def")
    # ... as long as it is if __name__ == .. and it appears at the very end
    if not isinstance(script_ast.body[-1], ast.If):
      raise self.failureException(msg or f"A top-level if statement is allowed only at the end")
    if_stmt = script_ast.body[-1]
    if not (isinstance(if_stmt.test, ast.Compare) and 
            len(if_stmt.test.ops) == 1 and isinstance(if_stmt.test.ops[0], ast.Eq) and
            isinstance(if_stmt.test.left, ast.Name) and if_stmt.test.left.id == "__name__"):
      raise self.failureException(msg or f"Only an if __name__ == ... top-level statement is allowed at the end")


class HomeworkModuleTestCase(HomeworkTestCase):
  """
  TestCase subclass to help partially automate testing that a solution's module
  can be successfully imported and does not do things it shouldn't,
  while providing some useful error messages
  """
  __modulename__: ClassVar[Optional[str]] = None   # Modulename of solution script
  __attrnames__: ClassVar[Optional[List[str]]] = None  # Attributes of solution module to import directly (instead of module)

  imports: ClassVar[Dict[str,Any]]

  @classmethod
  def setUpClass(cls):
    if cls is HomeworkModuleTestCase:
      raise unittest.SkipTest("Skip HomeworkModuleTestCase tests, it's a base class")
    if hasattr(cls, "imports"):
      raise RuntimeError(f"{cls.__name__}.imports unexpectedly exists; please report bug")
    if cls.__modulename__:  
      cls.imports = {}
      try_import(
        cls.__modulename__, 
        cls.__attrnames__, 
        excname=f"{cls.__modulename__}_exc", 
        nsdict=cls.imports)
      # Also add imported names to the module defining the testcase class
      # so they can be called more conveniently by the test methods
      sys.modules[cls.__module__].__dict__.update(cls.imports)
    # TODO Sanity check if both __modulename__ and __scriptname__ are set...

  @property
  def import_exc(self):
    return self.imports[f"{self.__modulename__}_exc"]

  def __check_import(self):
    # Check that import succeeded
    if self.__modulename__:
      if self.import_exc is not None:
        raise self.failureException(f"Importing your solution file failed with:\n{self.import_exc!r}")
    # Check that all required attribute names (if any) were found
    if self.__attrnames__:
      for attr in self.__attrnames__:
        #print("CHECK", attr, self.imports)
        if self.imports.get(attr) is None:
          raise self.failureException(f"Your solution file does not define {attr}")

  def __check_is_library(self):
    if self.__scriptname__:
      self.assertLibraryModule(self.__scriptname__)

  def setUp(self):
    self.__check_import()
    self.__check_is_library()
