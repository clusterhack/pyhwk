# (c) 2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import re
from functools import update_wrapper, wraps
import inspect
from io import StringIO

from snoop import Config as SnoopConfig

from types import FunctionType
from typing import overload, Callable, Optional, Sequence, Type


__all__ = ['strip_ansi_escapes', 'TraceLog']


_ANSI_CSISEQ_RE = re.compile(r'\033\[[0-9:;<=>?]*[ !"#$%&\'()*+,-./]*[@A-Z\[\]^_`a-z{|}~]')

def strip_ansi_escapes(txt: str) -> str:
  return _ANSI_CSISEQ_RE.sub('', txt)

# Define these here (just need basic colors, no need for additional dependencies)
_ANSI_RESET = '\033[0m'
_ANSI_CYAN = '\033[0;36m'


class TraceLog:
  def __init__(self, *, prefix: str = f'{_ANSI_CYAN}┃{_ANSI_RESET}', default_depth: int = 1):
    self._out = StringIO()
    self._config = SnoopConfig(out=self._out, prefix=prefix, columns='', color=True, replace_watch_extras=())
    self.default_depth = default_depth

  def reset(self):
    self._out.seek(0)
    self._out.truncate()

  def get_output(self, *, color: bool = True, header: bool = True) -> str:
    out = self._out.getvalue()
    if header:
      out = (
        f'{_ANSI_CYAN}┎──────────────────────────── EXECUTION TRACE ────────────────────────────{_ANSI_RESET}\n' +
        out +
        f'{_ANSI_CYAN}┖─────────────────────────────────────────────────────────────────────────{_ANSI_RESET}\n'
      )
    if not color:
      out = strip_ansi_escapes(out)
    return out
  
  @overload
  def trace[T: FunctionType | Type](self, *, depth: Optional[int] = None, watch: Sequence[str] = ()) -> Callable[[T], T]: ...
  @overload
  def trace[T: FunctionType | Type](self, cls_or_func: T) -> T: ...
  def trace(self, cls_or_func = None, depth = None, watch = ()):
    if depth is None:
      depth = self.default_depth
    snoop = self._config.snoop(watch=watch, depth=depth)

    def wrap(cls_or_func):
      if inspect.isfunction(cls_or_func):
        return snoop(cls_or_func)
      elif inspect.isclass(cls_or_func):
        for name, attr in cls_or_func.__dict__.items():  # Do *not* trace base class methods
          if inspect.isfunction(attr):
            setattr(cls_or_func, name, snoop(attr))
        return cls_or_func
      else:
        raise ValueError('Can only trace functions or classes')

    if cls_or_func is None:
      return wrap
    else:
      return wrap(cls_or_func)

  __call__ = trace
