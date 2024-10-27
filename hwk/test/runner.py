# (c) 2023- Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import json
# import re
import sys
import time
from dataclasses import dataclass, field

from typing import Any, Callable, Optional, TextIO, Type, TypeVar
from types import TracebackType

try:
  from typing import override  # Coming in Python 3.12
except ImportError:
  # Define as noop, to hush errors
  _Func = TypeVar('_Func', bound=Callable)
  def override(method: _Func) -> _Func:
    return method

from unittest import (
  SkipTest, TestCase, TestResult, TestSuite, TextTestResult, TextTestRunner,
  defaultTestLoader, registerResult,
)
from unittest.suite import _ErrorHolder

__all__ = [
  'AutogradeTest', 'AutogradeTestGroup', 'AutogradeResults',
  'AutogradeTestSuite', 'AutogradeTestResult', 'AutogradeTestRunner',
  'runAutograding',
]


############################################################################
# Results schema

@dataclass
class AutogradeTest:
  name: str
  score: int = 0 # Actual score (either zero or, typically, max_score)
  max_score: int = 0  # Specified test value (via @score decorator; note: could be negative)
  output: Optional[str] = None

  @property
  def is_subtractive(self) -> bool:
    return self.max_score < 0

  def to_dict(self) -> dict[str, Any]:
    return {
      'name': self.name,
      'score': self.score,
      'max_score': self.max_score,
      'output': self.output,
    }


@dataclass
class AutogradeTestGroup:
  name: str  # Qualified class name
  _max_score: Optional[int] = None  # Explicitly specified (via @max_score decorator)
  # XXX Ideally, constructor arg name for _max_score should be max_score ...

  tests: list[AutogradeTest] = field(default_factory=list, init=False)
  
  _total_score: int = field(default=0, init=False)  # Sum of score over all tests (note: could be negative)
  _total_max_score: int = field(default=0, init=False)  # Sum of max_score over all tests

  @property
  def is_subtractive(self) -> bool:
    return self._max_score is not None

  @property
  def score(self) -> int:
    if self.is_subtractive:
      return max(0, self._max_score + self._total_score)  # _total_score will be negative...
    return self._total_score

  @property
  def max_score(self) -> int:
    if self.is_subtractive:
      return self._max_score
    return self._total_max_score

  @max_score.setter
  def max_score(self, points: int):
    if len(self.tests) > 0:
      raise ValueError("Cannot modify max_score after tests have been added to group")
    self._max_score = points

  def add_test(self, test: AutogradeTest) -> None:
    if self.is_subtractive and test.score > 0:
      raise ValueError(f'Cannot add non-subtractive test {test.name} to subtractive group {self.name}')
    if not self.is_subtractive and test.score < 0:
      raise ValueError(f'Cannot add subtractive test {test.name} to non-subtractive group {self.name}')
    self._total_score += test.score
    self._total_max_score += test.max_score
    self.tests.append(test)

  def to_dict(self) -> dict[str, Any]:
    return {
      'name': self.name,
      'score': self.score,
      'max_score': self.max_score,
      'tests': [t.to_dict() for t in self.tests],
    }
    

@dataclass
class AutogradeResults:
  execution_time: Optional[int] = field(default=None, init=False)  # In seconds
  test_groups: dict[str, AutogradeTestGroup] = field(default_factory=dict, init=False)  # Key should be group.name

  @property
  def score(self) -> int:
    points = 0
    for group in self.test_groups.values():
      points += group.score
    return points

  @property
  def max_score(self) -> int:
    points = 0
    for group in self.test_groups.values():
      points += group.max_score
    return points

  def has_group(self, group_name: str) -> bool:
    return group_name in self.test_groups

  def add_group(self, group_name: str, max_score: Optional[int] = None) -> AutogradeTestGroup:
    if self.has_group(group_name):
      raise KeyError(f'Test group {group_name} already exists')
    group = AutogradeTestGroup(group_name, _max_score=max_score)
    self.test_groups[group_name] = group
    return group

  def ensure_group(self, group_name: str, max_score: Optional[int] = None) -> AutogradeTestGroup:
    if self.has_group(group_name):
      group = self.test_groups[group_name]
      if group._max_score != max_score:
        raise ValueError(f'Test group {group_name} exists, but with max_score of {group._max_score} instead of {max_score}')
      return group
    return self.add_group(group_name, max_score)

  # def add_test(self, group_name: str, test: AutogradeTest) -> None:
  #   if not self.has_group(group_name):
  #     raise KeyError(f'Test group {group_name} does not exist')
  #   self.test_groups[group_name].add_test(test)

  def to_dict(self) -> dict[str, Any]:
    return {
      'score': self.score,
      'max_score': self.max_score,
      'execution_time': self.execution_time,
      'test_groups': [g.to_dict() for g in self.test_groups.values()],
    }


############################################################################
# Unit test runner

# Copied from typeshed definitions for unittest.result
SysExcInfoType = tuple[Type[BaseException], BaseException, TracebackType] | tuple[None, None, None]

# def _parseErrorHolderId(id: str) -> Optional[tuple[str, str]]:
#   "Returns a (className, methodName) tuple, or None if id can't be parsed"
#   m = re.fullmatch(r'(?P<methodName>\w+)?\s+\((?:\w*\.)*(?P<className>\w+)\)', id)
#   if m is None:
#     return
#   return m.group('methodName', 'className')

class AutogradeTestSuite(TestSuite):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # This is a horrible kludge, since _ErrorHolder provides no way to access the underlying test case...
    self._currentTest = None

  @override
  def _handleClassSetUp(self, test: TestCase, result: TestResult) -> None:
    assert self._currentTest is None  # XXX?
    try:
      self._currentTest = test
      super()._handleClassSetUp(test, result)
    finally:
      self._currentTest = None

  @override
  def _handleModuleFixture(self, test: TestCase, result: TestResult) -> None:
    assert self._currentTest is None  # XXX?
    try:
      self._currentTest = test
      super()._handleModuleFixture(test, result)
    finally:
      self._currentTest = None

  @override
  def _addClassOrModuleLevelException(self, result, exception, errorName, info=None):
    # XXX Horrible kludge gets worse, this is *copy-pasted* from stdlib devinition...
    error = _ErrorHolder(errorName)
    error._realTest = self._currentTest  # XXX ...just so we can do this!
    addSkip = getattr(result, 'addSkip', None)
    if addSkip is not None and isinstance(exception, SkipTest):
        addSkip(error, str(exception))
    else:
        if not info:
            result.addError(error, sys.exc_info())
        else:
            result.addError(error, info)

defaultTestLoader.suiteClass = AutogradeTestSuite

def _getTestGroupInfo(test: TestCase) -> tuple[str, Optional[int]]:
  test = getattr(test, '_realTest', test)  # XXX Using kludge here
  assert isinstance(test, TestCase)  # XXX Partial safeguard for kludge
  group_name = test.__class__.__name__  # TODO? Should be unqualified; verify?
  group_max_score = getattr(test, '__max_score__', None)
  return group_name, group_max_score

def _getTestInfo(test: TestCase) -> tuple[str, int]:
  test = getattr(test, '_realTest', test)  # XXX Using kludge here
  assert isinstance(test, TestCase)  # XXX Partial safeguard for kludge
  test_name = getattr(test, '_testMethodName', None)
  test_method = getattr(test, test_name, None)
  test_max_score = getattr(test_method, '__score__', None)
  return test_name, test_max_score


# _fxxx = open('utdebug.log', 'w')

# TODO Refactor addXXX methods, if/when time allows...
class AutogradeTestResult(TextTestResult):
  def __init__(
    self,
    stream: Optional[TextIO],
    descriptions: Optional[bool] = None,
    verbosity: Optional[int] = None
  ):
    super().__init__(stream, descriptions, verbosity)
    self.autograde = AutogradeResults()

  def getOutput(self) -> Optional[str]:
    if not self.buffer:
      return None
    out: str = self._stdout_buffer.getvalue()
    err: str = self._stderr_buffer.getvalue()
    if err:
      if not out.endswith('\n'):
        out += '\n'
      out += err
    return out

  @override
  def startTestRun(self) -> None:
    super().startTestRun()
    # print(f'INFO: startTestRun()', file=_fxxx)

  @override
  def stopTestRun(self) -> None:
    super().stopTestRun()
    # print(f'INFO: stopTestRun()', file=_fxxx)

  @override
  def startTest(self, test: TestCase) -> None:
    super().startTest(test)
    # print(f'INFO: startTest({test})\n  [{_getTestGroupInfo(test)}]', file=_fxxx)

  @override
  def stopTest(self, test: TestCase) -> None:
    super().stopTest(test)
    # print(f'INFO: stopTest({test})\n  [{_getTestGroupInfo(test)}]', file=_fxxx)

  @override
  def addSuccess(self, test: TestCase) -> None:
    super().addSuccess(test)
    # print(f'INFO: addSuccess({test})\n  [{_getTestGroupInfo(test)} | {_getTestInfo(test)}]', file=_fxxx)
    group_name, group_max_score = _getTestGroupInfo(test)
    test_name, test_max_score = _getTestInfo(test)
    if test_max_score is None:
      # Tests not decorated with @score do not participate in grading; ommit
      return
    group = self.autograde.ensure_group(group_name, group_max_score)
    if group.is_subtractive:
      test_score = 0
    else:
      test_score = test_max_score
    group.add_test(AutogradeTest(test_name, score=test_score, max_score=test_max_score, output=self.getOutput()))

  @override
  def addError(self, test: TestCase, err: SysExcInfoType) -> None:
    super().addError(test, err)
    # print(f'INFO: addError({test}, {err})\n  [{_getTestGroupInfo(test)} | {_getTestInfo(test)}]', file=_fxxx)
    group_name, group_max_score = _getTestGroupInfo(test)
    test_name, test_max_score = _getTestInfo(test)
    if test_max_score is None:
      # Tests not decorated with @score do not participate in grading; ommit
      return
    group = self.autograde.ensure_group(group_name, group_max_score)
    if group.is_subtractive:
      test_score = test_max_score
    else:
      test_score = 0
    group.add_test(AutogradeTest(test_name, score=test_score, max_score=test_max_score, output=self.getOutput()))

  @override
  def addFailure(self, test: TestCase, err: SysExcInfoType) -> None:
    super().addFailure(test, err)
    # print(f'INFO: addFailure({test}, {err})\n  [{_getTestGroupInfo(test)} | {_getTestInfo(test)}]', file=_fxxx)
    group_name, group_max_score = _getTestGroupInfo(test)
    test_name, test_max_score = _getTestInfo(test)
    if test_max_score is None:
      # Tests not decorated with @score do not participate in grading; ommit
      return
    group = self.autograde.ensure_group(group_name, group_max_score)
    if group.is_subtractive:
      test_score = test_max_score
    else:
      test_score = 0
    group.add_test(AutogradeTest(test_name, score=test_score, max_score=test_max_score, output=self.getOutput()))


class AutogradeTestRunner(TextTestRunner):
  resultclass = AutogradeTestResult

  def __init__(
    self,
    stream: Optional[TextIO] = None,
    descriptions: bool = True,
    verbosity: int = 1,
    failfast: bool = False,
    buffer: bool = True,
    *,
    autograde_stream: Optional[TextIO] = None,
  ):
    super().__init__(stream=stream, descriptions=descriptions, verbosity=verbosity, failfast=failfast, buffer=buffer)
    self._autograde_stream = autograde_stream

  @override
  def run(self, test: TestCase | TestSuite) -> AutogradeTestResult:
    start_time = time.perf_counter()
    result: AutogradeTestResult = super().run(test)
    result.autograde.execution_time = time.perf_counter() - start_time

    if self._autograde_stream is not None:
      json.dump(result.autograde.to_dict(), self._autograde_stream, indent=2)

    return result


def runAutograding(
  result_file: str,
  start_dir: str = '.',
  pattern: str = 'test_*.py',
  top_level_dir: Optional[str] = None,
) -> AutogradeTestResult:
  suite = defaultTestLoader.discover(start_dir, pattern=pattern, top_level_dir=top_level_dir)
  with open(result_file, 'w') as fp:
    return AutogradeTestRunner(autograde_stream=fp).run(suite)


def main():
  result_file = len(sys.argv) > 1 and sys.argv[1] or 'autograde.json'
  start_dir = len(sys.argv) > 2 and sys.argv[2] or '.'
  pattern = len(sys.argv) > 3 and sys.argv[3] or 'test_*.py'
  result = runAutograding(result_file, start_dir=start_dir, pattern=pattern)
  # print(f'INFO: Autograde result = {result.autograde}', file=_fxxx)
  sys.exit(1 if result.errors or result.failures else 0)

if __name__ == '__main__':
  main()

