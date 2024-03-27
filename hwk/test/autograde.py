# (c) 2023- Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

__all__ = ['score', 'max_score', 'sortby', 'visibility', 'tags']

from typing import Callable, List, Literal, Type, TypeVar

import inspect

_Func = TypeVar("_Func", bound=Callable)
_Class = TypeVar("_Class", bound=Type)

def score(points: float):
  def decorator(func: _Func) -> _Func:
    if not inspect.isroutine(func):
      raise TypeError('@score only applies to methods')
    func.__score__ = points
    return func
  return decorator

def max_score(points: float):
  def decorator(cls: _Class) -> _Class:
    if not inspect.isclass(cls):
      raise TypeError('@max_score only applies to classes')
    # Validate score annotations: all must be nonpositive
    for name, obj in inspect.getmembers(cls, predicate=inspect.isroutine):
      if getattr(obj, '__score__', 0) > 0:
        raise ValueError(f'Test method {name} with positive score in class {cls.__name__} with max_score not allowed')
    # Actual class annotation
    cls.__max_score__ = points
    return cls
  return decorator

def sortby(key):
  def decorator(func: _Func) -> _Func:
    func.__sortby__ = str(key)
    return func
  return decorator

def visibility(vis: Literal['hidden', 'visible']):
  def decorator(func: _Func) -> _Func:
    func.__visibility__ = vis
    return func
  return decorator

def tags(*args: List[str]):
  def decorator(func: _Func) -> _Func:
    func.__tags__ = args
    return func
  return decorator
