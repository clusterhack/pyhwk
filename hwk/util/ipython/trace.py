# (c) 2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import ast
from argparse import ArgumentTypeError

from IPython.core import magic_arguments
from IPython.core.error import UsageError
from IPython.core.magic import (
  Magics, line_cell_magic, line_magic, magics_class,
  needs_local_scope, no_var_expand, output_can_be_silenced,
)
from snoop import Config as SnoopConfig
from typing import Any, Callable, Iterator, Literal, NamedTuple, Optional

from ..code import fill_ast_missing_locations, get_ast_names, ast_module_to_function
from ..common import setattrdefault

# Names for variables injected into shell.user_ns; should be parse-able...
NAME_TRACE_CONFIG = '__trace_config'
NAME_TRACE_FN = '__trace_block'
NAME_TRACE_PP = '__trace_pp'


class _TracedCode(NamedTuple):
  fndef_stmt: ast.Module
  eval_expr: ast.Expression
  var_names: set[str]

def _trace_ast(mod: ast.Module, depth: Optional[int] = None) -> _TracedCode:
  mod_names = get_ast_names(mod)  # All identifiers, regardless of scope

  # Wrap module into function
  fndef_ast, call_ast = ast_module_to_function(NAME_TRACE_FN, mod, mod_names=mod_names)

  # Add snoop decorator to function definition
  decorator = ast.Call(
    func=ast.Name(id=NAME_TRACE_CONFIG, ctx=ast.Load()),
    args=[],
    keywords=[ast.keyword(arg='depth', value=ast.Constant(value=depth))]
  )
  fill_ast_missing_locations(decorator, fndef_ast.lineno, fndef_ast.col_offset)
  setattrdefault(fndef_ast, 'decorator_list', []).insert(0, decorator)

  # ast.Module and ast.Expression don't have source text location attributes, so nothing to backfill
  return _TracedCode(ast.Module(body=[fndef_ast]), ast.Expression(body=call_ast), mod_names)


type _TraceTriggerCond = Callable[[_TracedCode], bool]  # f(traced) -> bool

class TraceTransformer(ast.NodeTransformer):
  trigger_cond: _TraceTriggerCond

  def __init__(self, trigger_cond: Optional[_TraceTriggerCond] = None):
    super().__init__()
    if trigger_cond is None:
      # Always trigger
      trigger_cond = lambda traced: True
    # User-provided arbitrary predicate, that should return True 
    # iff tracing should be triggered
    self.trigger_cond = trigger_cond

  def visit_Expression(self, node: ast.Expression):
    raise NotImplementedError('internal error; unexpected ast from IPython')
  
  def visit_Interactive(self, node: ast.Interactive):
    raise NotImplementedError('internal error; unexpected ast from IPython')

  def visit_Module(self, node: ast.Module) -> ast.Module:
    traced = _trace_ast(node)
    if not self.trigger_cond(traced):
      return node  # skip tracing, just return code unmodified
    code_ast = traced.fndef_stmt
    code_ast.body.append(ast.Expr(value=traced.eval_expr.body))
    return code_ast


def OnOffArgType(argstr: str) -> bool:
  argstr = argstr.strip().lower()
  if argstr in ('on', 'true'):
    return True
  elif argstr in ('off', 'false'):
    return False
  else:
    raise ArgumentTypeError("invalid argument; expected on/off")


class SnoopWrapper:
  depth: int
  _watches: tuple[str, ...]

  _config: SnoopConfig
  _prefix: str
  _color: bool
  # TODO? Add property for config columns

  def __init__(self, depth: int = 2):
    self.depth = depth
    self._watches = ()
    self._prefix = '┃'
    self._color = True
    self._update_config()

  @property
  def watches(self):
    return self._watches
  
  def clear_watches(self):
    self._watches = ()

  def append_watches(self, watches: tuple[str]):
    self._watches += watches

  @property
  def color(self) -> bool:
    return self._color
  
  @color.setter
  def color(self, value: bool):
    self._color = value
    self._update_config()

  @property
  def prefix(self) -> str:
    return self._prefix
  
  @prefix.setter
  def prefix(self, value: str):
    self._prefix = value
    self._update_config()

  def _update_config(self):
    "Create new snoop configuration, using current attribute values"
    self._config = SnoopConfig(
      columns='', replace_watch_extras=(),
      prefix=self._prefix, color=self._color,
    )

  def new_tracer(self, depth: Optional[int] = None):
    if depth is None:
      depth = self.depth
    return self._config.snoop(depth=depth, watch=self.watches)

  @property
  def pp_deep[T](self) -> Callable[[Callable[[], T]], T]:
    # This is a property that returns snoop's pp.deep method itself (rather
    # than merely calling it and returning it's result), since pp.deep
    # needs to receive the lambda literal *directly* as it's argument value 
    # (so it can get it's AST)
    return self._config.pp.deep
  
  def __str__(self):
    return (
      f'      Call depth: {self.depth}\n'
      f'Variable watches: {', '.join(self._watches) if self._watches else '--'}\n'
      f'   Output prefix: {self._prefix!r}\n'
      f'        Colorize: {'on' if self._color else 'off'}'
    )


# TODO? Hook into IPython config for things that can be set via %traceconfig (Magics is a Configurable...)
@magics_class
class TraceMagics(Magics):
  _snoop: SnoopWrapper
  _trace_transformer: TraceTransformer

  def __init__(self, shell=None, **kwargs):
    super().__init__(shell=shell, **kwargs)
    self._snoop = SnoopWrapper()
    self._update_ns()
    # print(f'__init__ user_global_ns={self.shell.user_global_ns!r}')
    self._trace_transformer = TraceTransformer(trigger_cond=self._autotrace_names_cond)  # Stateless, so single instance is fine..

  def _update_ns(self):
    self.shell.push(
      {
        NAME_TRACE_PP: self._snoop.pp_deep,
        NAME_TRACE_CONFIG: self._snoop.new_tracer,  # Doesn't *really* need to be done here, but in future it might...
      },
      interactive=False,
    )
  
  def _autotrace_names_cond(self, traced: _TracedCode) -> bool:
    # Return True iff code contains any non-hidden names in user_ns
    # Note that the trigger condition is very simplistic. Notably, it completely ignores name scope.
    # However, this should be sufficient for most purposes, in practice...
    visible_names = set(self.shell.user_ns)
    visible_names.difference_update(self.shell.user_ns_hidden)
    return not visible_names.isdisjoint(traced.var_names)
  

  @line_cell_magic
  @no_var_expand
  @output_can_be_silenced
  @needs_local_scope  # Only to disallow use in non-global scope...
  def trace(
    self,
    line: str,
    cell: Optional[str] = None,
    *,
    local_ns=None,
  ) -> Any:
    """
    ::

      %trace STATEMENT

      %%trace
      STATEMENTS

    Execute Python code with tracing (via snoop)
    """
    if local_ns is not self.shell.user_ns:
      raise UsageError('magic can only be used in global scope')

    if cell is None:
      # Called as line magic
      source = line
    else:
      # Called as cell magic
      source = cell      

    filename = self.shell.compile.cache(source)
    code_ast = self.shell.compile.ast_parse(source, filename, 'exec')
    #print(f'ORIG-AST:\n{ast.dump(code_ast, include_attributes=True, indent=2)}')
    code_ast, ret_ast, _ = _trace_ast(code_ast)
    #print(f'AST:\n{ast.dump(code_ast, include_attributes=True, indent=2)}')
    #print(f'RET-AST:\n{ast.dump(ret_ast, include_attributes=True, indent=2)}')
    code = self.shell.compile(code_ast, filename, 'exec')
    self.shell.ex(code)
    if ret_ast is not None:
      return self.shell.ev(compile(ret_ast, '<trace-magic>', 'eval'))

  @line_magic
  @no_var_expand
  def etrace(self, line: str) -> Any:
    """
    ::

      %etrace EXPRESSION

    Trace execution of a Python expression, displaying all steps (via snoop's pp.deep).
    """
    # Snoop in internally very messy (looks like it was coded in a hurry, with a lot of cruft,
    # unnecessary/arbitrary dependences, limited separation, etc--kinda like this file :).
    # Ideally, since we *have* the AST (thus don't really need all the executing shennanigans
    # to get it), it should be a matter of calling PPEvent.pp_deep but... it's almost impossible
    # to call it independently (at least without even worse kludges).
    # 
    # Thus, we resort to the following kludges. It took a while to figure out that the source
    # code in the linecache has to match the call-lambda shennanigans for pp.deep (with accurate
    # lineno and col_offset values), for the simple transformation below to actually work...

    # First, ensure that expression compiles by itself
    try:
      ast.parse(line, '<ast>', 'eval')
    except SyntaxError as err:
      # TODO? We could alternatively try to re-parse with mode='exec' and if that causes no error,
      # then raise a UsageError, else re-throw the SyntaxError...
      err.add_note('Please verify that the argument is an expression, not a statement')
      raise

    source = f"{NAME_TRACE_PP}(lambda: {line})"
    filename = self.shell.compile.cache(source)
    expr_ast = self.shell.compile.ast_parse(source, filename, 'eval')
    code = self.shell.compile(expr_ast, filename, 'eval')
    return self.shell.ev(code)

  @magic_arguments.magic_arguments()
  @magic_arguments.argument(
    '--show', action='store_true',
    help='Show current configuration settings.',
  )
  @magic_arguments.argument(
    '-c', '--color', type=OnOffArgType, metavar='on|off',
    help='Whether to colorize trace output',
  )
  @magic_arguments.argument(
    '-p', '--prefix',
    help='Tracing output prefix.',
  )
  @magic_arguments.argument(
    '-W', '--clear-watches', action='store_true',
    help=(
      'Clear all previous watches. Note that this will happen first, so all watches currently '
      'specified will be added, regardless of relative order of options.'
    ),
  )
  @magic_arguments.argument(
    '-w', '--watch', action='append', 
    help=(
      'Additional variable names to watch. Option can be repeated, to specify multiple '
      'variables. Previous watches are *not* cleared (see -W).'
    ),
  )
  @magic_arguments.argument(
    '-d', '--depth', type=int, 
    help='Default maximum call depth to trace.',
  )
  @line_magic
  def traceconfig(self, line: str) -> None:
    "Set tracer defaults"
    args = magic_arguments.parse_argstring(self.traceconfig, line)

    if args.depth is not None:
      if args.depth < 1:
        raise UsageError('depth must be >= 1')
      self._snoop.depth = args.depth
    if args.clear_watches:
      # Should process -W *before* -w
      self._snoop.clear_watches()
    if args.watch is not None:
      self._snoop.append_watches(args.watch)
    if args.prefix is not None:
      self._snoop.prefix = args.prefix
    if args.color is not None:
      self._snoop.color = args.color
    self._update_ns()  # Not necessary for all config options, but doesn't hurt doing anyway...

    if args.show:
      print(self._snoop)

  @magic_arguments.magic_arguments()
  @magic_arguments.argument(
    'state', type=OnOffArgType, nargs='?', metavar='on|off',
    help='New state; if ommitted, print current state.'
  )
  @line_magic
  def autotrace(self, line: str) -> None:
    "Enable automatic tracing of every IPython input"
    args = magic_arguments.parse_argstring(self.autotrace, line)

    is_on = self._trace_transformer in self.shell.ast_transformers
    if args.state is None:
      print(f'Autotrace is {'on' if is_on else 'off'}')
    elif args.state:
      if not is_on:
        self.shell.ast_transformers.append(self._trace_transformer)
    else:  # not args.state
      if is_on:
        self.shell.ast_transformers.remove(self._trace_transformer)

