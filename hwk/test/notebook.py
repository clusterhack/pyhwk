# (c) 2023 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

from typing import ClassVar, Optional, Any, Sequence, List, Dict
import unittest

from types import SimpleNamespace

from .exec import runScriptFromString, expand_path
from .unittest import HomeworkTestCase

__all__ = ['NotebookNamespace', 'runNotebook', 'HomeworkNotebookTestCase']


class NotebookNamespace(SimpleNamespace):
  """
  Class that adds some minor convenience methods/operations to SimpleNamespace.
  """

  def __contains__(self, attr):
    return attr in self.__dict__

  def __iter__(self):
    return self.__dict__.keys()


class __FakeIPython:
  def run_line_magic(self, *args, **kwargs):
    pass

__fake_ipython = __FakeIPython()

def __fake_get_ipython():
  global __fake_ipython
  return __fake_ipython

def __fake_display(*args, **kwargs):
  pass


def _skip_cell(cell: "nbformat.NotebookNode") -> bool:
  return 'test:skip' in getattr(cell.metadata, 'tags', ())

def runNotebook(
  filename: str,
  export_varnames: Sequence[str] = (),
  *,
  ignore_missing: bool = False,
  include_stdout: bool = False,
) -> NotebookNamespace:
  # Import locally to minimize chance of test discovery errors
  import nbformat
  from nbconvert.exporters import PythonExporter, export

  # Read notebook
  pathname = expand_path(filename)
  with open(pathname) as fp:
    nb = nbformat.read(fp, 4)
  # Filter cells that are markdown or explicitly marked for skipping
  nb.cells = [c for c in nb.cells if c.cell_type != 'markdown' and not _skip_cell(c)]
  # Convert notebook to Python script
  pyexp = PythonExporter()
  pyscript, _ = export(pyexp, nb)
  # print("DBG pyscript:\n" + pyscript + "\n**********************")
  # Execute converted notebook
  ns_extra = {
    'get_ipython': __fake_get_ipython,  # So converted magics don't fail...
    'display': __fake_display,  # So display() doesn't fail...
  }
  output, ns = runScriptFromString(pyscript, return_ns=True, ns_extra=ns_extra)

  # Extract requested variable names from script namespace
  nbvars = NotebookNamespace()
  for varname in export_varnames:
    if (not ignore_missing) and (varname not in ns):
      raise RuntimeError('Notebook did not create variable with name %s' % varname)
    setattr(nbvars, varname, ns[varname])
  if include_stdout:
    setattr(nbvars, '__output__', output)

  return nbvars


class HomeworkNotebookTestCase(HomeworkTestCase):
  """
  TestCase subclass to help partially automate testing of Jupyter notebooks.
  """
  __notebookname__: ClassVar[Optional[str]] = None
  __attrnames__: ClassVar[Optional[List[str]]] = None

  nb: ClassVar[Dict[str,Any]]

  @classmethod
  def setUpClass(cls):
    if cls is HomeworkNotebookTestCase:
      raise unittest.SkipTest("Skip HomeworkNotebookTestCase tests, it's a base class")
    if not getattr(cls, '__attrnames__', None):
      raise RuntimeError(f"{cls.__name__}.__attrnames__ is not set; please report bug")
    if hasattr(cls, "nb"):
      raise RuntimeError(f"{cls.__name__}.nb unexpectedly exists; please report bug")
    if getattr(cls, '__scriptname__', None) is not None:
      raise RuntimeError(f"{cls.__name__}.__scriptname__ is set; please report bug")
    # Execute notebook
    try:
      if hasattr(cls, "setUpNotebook"): cls.setUpNotebook()
      cls.nb = runNotebook(cls.__notebookname__, cls.__attrnames__, include_stdout = True)
      cls.nb_exc = None
    except Exception as exc:
      cls.nb_exc = exc

  @classmethod
  def tearDownClass(cls):
    if cls.nb_exc is None and hasattr(cls, "tearDownNotebook"):
      cls.tearDownNotebook()

  def __check_exec(self):
    # Check that execution of notebook succeeded
    if self.nb_exc is not None:
      raise self.failureException(f"Executing your notebook failed with:\n{self.nb_exc!r}")
    # Check that all required attribute names were found
    for attr in self.__attrnames__:
      if attr not in self.nb:
        raise self.failureException(f"Your notebook does not define {attr}")

  def setUp(self):
    self.__check_exec()
