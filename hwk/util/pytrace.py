# (c) 2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import ast
import linecache
import sys
import os
import os.path
import traceback

from collections import deque
from dataclasses import dataclass, field
from importlib.machinery import FileFinder, ModuleSpec, SourceFileLoader
from importlib.util import find_spec
from os import PathLike

from collections.abc import Buffer
from types import CodeType, ModuleType
from typing import Any, Callable, NoReturn, Optional, override

from snoop import Config as SnoopConfig
from snoop.tracer import Tracer as SnoopTracer

from .code import ASTRoot, ast_module_to_function
from .common import die, printerr, setattrdefault


# Names used in injected code; these do not have to be valid identifiers (and it's best if they're not)
NAME_TRACE_DECORATOR = '__trace-wrap'  # Snoop tracer decorator variable injected into each module
NAME_TRACE_MODULE_FN = '__trace-module-main'  # Name of function that wraps entire module


# Simple utility function (for shorter debug prints)
def typename(obj: Any) -> str:
  return type(obj).__name__

if os.environ.get('PYTRACE_DEBUG', None) == '1':
  def _DEBUG(text: str) -> None:
    import textwrap
    print(textwrap.indent(text, 'DBG: '), file=sys.stderr)
else:
  def _DEBUG(text: str) -> None:
    pass 

############################################################################
# Code transformations

type ASTNodeFactory = Callable[[], ast.AST]

def ast_inject_decorator(
  mod: ast.Module,
  decorator_factory: ASTNodeFactory,
  *,
  exclude_nested: bool = False,
  exclude_methods: bool = False, # will also exclude nested within methods, regardless of exclude_nested
):
  def transform(node: ast.AST, parent: Optional[ast.AST], level: int) -> bool:
    """Helper function to transform node in-place. Should return False to prune
    traversal (i.e., skip all child subtrees)"""
    if isinstance(node, ast.FunctionDef):
      # Is it a method?
      if isinstance(parent, ast.ClassDef) and exclude_methods:
        return False  # Skip method (and it's children)
      # Decorate function
      setattrdefault(node, 'decorator_list', []).insert(0, decorator_factory())
      return not exclude_nested  # Visit children only 
    return True  # No transformation, but visit children

  if not isinstance(mod, ast.Module):
    raise ValueError('root_node must be Module')
  q = deque[tuple[ast.AST, Optional[ast.AST], int]]([(mod, None, 0)])
  while q:
    node, parent, level = q.popleft()
    if transform(node, parent, level):
      q.extend((child, node, level+1) for child in ast.iter_child_nodes(node))
  

############################################################################
# Import hooks

@dataclass
class TraceConfig:
  "Simple container so config parameters can be shared (and modified, if needed)"
  tracer: SnoopTracer
  trace_methods: bool = True
  trace_modules: bool = False
  trace_nested: bool = True
  ignore_module_paths: list[str] = field(default_factory=list)

  def ignore_origin(self, origin: str) -> bool:
    # Currently, ignore only installed modules
    for root in [sys.base_prefix, sys.prefix] + self.ignore_module_paths:
      if origin.startswith(root):
        return True
    return False

def _trace_transform_module(mod: ast.Module, trace_config: TraceConfig) -> None:
  decorator_factory = (lambda : ast.Name(id=NAME_TRACE_DECORATOR, ctx=ast.Load()))
  ast_inject_decorator(
    mod=mod,
    decorator_factory=decorator_factory,
    exclude_methods=not trace_config.trace_methods,
    exclude_nested=not trace_config.trace_nested,
  )
  if trace_config.trace_modules:
    # Wrap entire module in a function (preserving global names...), so it's execution can be traced
    fndef_node, call_node = ast_module_to_function(NAME_TRACE_MODULE_FN, mod, 'global')
    setattrdefault(fndef_node, 'decorator_list', []).insert(0, decorator_factory())  # Decorate function
    mod.body = [fndef_node, ast.Expr(value=call_node)]  # In-place mutation (consistent function behavior)
    # linecache hasn't been populated at this point, so nothing more we can do here

  ast.fix_missing_locations(mod)

type SourceData = str | Buffer | ASTRoot
type SourcePath = str | Buffer | PathLike

class TracingSourceFileLoader(SourceFileLoader):
  def __init__(self, loader: SourceFileLoader, trace_config: TraceConfig):
    super().__init__(loader.name, loader.path)
    self.loader = loader
    self.trace_config = trace_config
  
  def path_mtime(self, _path: str) -> int:
    # Not really necessary to override this, as SourceFileLoader.path_stats doesn't use it,
    # but doesn't hurt either
    _DEBUG(f'{typename(self)}.path_mtime({_path!r})')
    raise OSError()  # Bypass bytecode cache
  
  def path_stats(self, _path: str) -> dict[str, float|Any]:
    _DEBUG(f'{typename(self)}.path_stats({_path!r})')
    raise OSError()  # Bypass bytecode cache

  def _cache_bytecode(self, source_path, bytecode_path, data):
    # Also not really necessary to override, since SourceLoader.get_code won't try to
    # cache bytecode if the source mtime is unknown (which our path_stats ensures),
    # but doesn't hurt either
    _DEBUG(f'{typename(self)}._cache_bytecode({source_path!r}, {bytecode_path!r}, ...)')
    raise NotImplementedError()  # Do not cache bytecode
  
  def source_to_code(self, data: SourceData, path: SourcePath, *, _optimize: int = -1) -> CodeType:
    _DEBUG(f'{typename(self)}.source_to_code(..., {path!r})')
    # Transform module by injecting decorators to functions/methods
    if not isinstance(data, ast.AST):
      data = ast.parse(data, path, 'exec')
    _trace_transform_module(data, trace_config=self.trace_config)
    # TODO? Should we also use importlib._bootstrap._call_with_frames_removed for compile?
    return compile(data, path, 'exec', dont_inherit=True, optimize=_optimize)

  def exec_module(self, module: ModuleType) -> None:
    _DEBUG(f'{typename(self)}.exec_module({module!r})')
    # TODO? Should next line be in create_module() method?
    module.__dict__[NAME_TRACE_DECORATOR] = self.trace_config.tracer  # Inject tracer object
    super().exec_module(module)
    # Sanity check
    if module.__dict__[NAME_TRACE_DECORATOR] is not self.trace_config.tracer:
      raise ImportError('Injected decorator variable has been modified?!')

_dbg_specs = []  # XXX
class TracingFileFinder(FileFinder):
  def __init__(self, finder: FileFinder, trace_config: TraceConfig):
    super().__init__(finder.path)
    # Since FileFinder.__init__ doesn't really use _loaders attribute, it's easier to just copy over
    # rather than reconstruct *loader_details argument (either way, we depend on non-public property...)
    self._loaders = finder._loaders
    self.finder = finder
    self.trace_config = trace_config

  def find_spec(self, fullname: str, target: Optional[ModuleType]=None) -> ModuleSpec:
    _DEBUG(f'{type(self).__name__}.find_spec({fullname!r}, {target!r})')
    spec = super().find_spec(fullname, target)
    _DEBUG(f'  spec={spec!r}')
    global _dbg_specs  # XXX
    _dbg_specs.append(spec)  # XXX
    if isinstance(spec.loader, SourceFileLoader) and not self.trace_config.ignore_origin(spec.origin):
      spec.loader = TracingSourceFileLoader(spec.loader, self.trace_config)
    return spec


type PathHook = Callable[[str], FileFinder]

def TracingWrappedPathHook(hook: PathHook, trace_config: TraceConfig) -> PathHook:
  def path_hook(path: str) -> FileFinder:
    _DEBUG(f'path_hook({path!r})')
    finder = hook(path)
    _DEBUG(f'  finder={finder!r}')
    return TracingFileFinder(finder, trace_config)
  path_hook.hook = hook
  return path_hook


def patch_path_hooks(trace_config: TraceConfig):
  for i in range(len(sys.path_hooks)):
    if getattr(sys.path_hooks[i], '__name__', None) == 'path_hook_for_FileFinder':
      sys.path_hooks[i] = TracingWrappedPathHook(sys.path_hooks[i], trace_config)


##############################################################################

def usage(progname: str) -> NoReturn:
  printerr(
    f'usage: {progname} [-h] [-d DEPTH] [-p PREFIX] [-P MODULEPATH]* [+/-M] [+/-C] (-m MODULENAME | SCRIPTFILE) SCRIPTARGS...\n'
    '\n'
    '+/-M  Trace module-global statements. Wraps entire module in a function and calls it.  [Default: off]\n'
    '+/-C  Trace class methods.  [Default: on]\n'
    '+/-I  Trace inner (nested) functions.  [Default: on]\n'
    '\n'
    '-d DEPTH   Maximum call depth.  [Default: 1]\n'
    '-p PREFIX  Trace output line prefix.  [Default: \'DBG:\']\n'
    '-P MODULEPATH Add module path to list of paths to ignore when module-global tracing\n'
    '           is enabled (+M). The sys.prefix and sys.base_prefix paths are always ignored.'
  )
  sys.exit(0)

def _uncapitalize(s: str) -> str:
  "Simple utility (for consisten error message formatting)"
  return s[:1].lower() + s[1:]

def _pop_arg(args: list[str], flag: str) -> str:
  assert args.pop(0) == flag
  if not args:
    die(f'error: {flag} requires an argument')
  return args.pop(0)

def main():
  # Spent over 2hrs trying to use argparse for this, but... ugh!!  Just for the record:
  # Getting it to stop parsing args after a certain option is near-impossible. I did manage
  # to get 90% there with a custom argparse.Action, but (a) I had to rely on undocumented 
  # features (e.g., _UNRECOGNIZED_ARGS_ATTR), and (b) certain args are completely impossible
  # to preserve (e.g., -opt=val will always be broken up at the '=').
  # As for mutually exclusive option or positional arg (-m MODULE vs SCRIPTFILE) on top of that..
  # forget about it!
  # Hand-crafted parser FTW, would have saved me several hours if I'd started with that!
  prog_name = os.path.basename(sys.argv[0])
  args = sys.argv[1:]  # Keep original sys.argv intact
  trace_methods = True
  trace_modules = False
  trace_nested = True
  trace_depth = 1
  trace_prefix = 'DBG:'
  ignore_module_paths = []
  modulename = None
  while args:
    match args[0]:
      case '-h':
        usage(prog_name)
      case '-d':
        try:
          trace_depth = int(_pop_arg(args, '-d'))
        except ValueError:
          die('error: -d argument must be an integer')
        if trace_depth < 1:
          die('error: -d argument must be at least 1')
      case '-p':
        trace_prefix = _pop_arg(args, '-p')
      case '-P':
        ignore_module_paths.append(_pop_arg('-P'))
      case '+M' | '-M':
        trace_modules = args.pop(0)[0] == '+'
      case '+C' | '-C':
        trace_methods = args.pop(0)[0] == '+'
      case '+I' | '-I':
        trace_nested = args.pop(0)[0] == '+'
      case '-m':
        args.pop(0)
        if not args:
          die('error: -m must be followed by a module name', status=2)
        modulename = args.pop(0)
        break
      case _:
        break

  # Set up snoop and path hooks
  snoop_config = SnoopConfig(prefix=trace_prefix, columns=(), replace_watch_extras=())
  tracer = snoop_config.snoop(depth=trace_depth)
  trace_config = TraceConfig(
    tracer=tracer,
    trace_methods=trace_methods,
    trace_modules=trace_modules,
    trace_nested=trace_nested,
    ignore_module_paths=ignore_module_paths,
  )
  patch_path_hooks(trace_config)

  # Load script source code
  if modulename is not None:
    if modulename.startswith('.'):
      die('error: relative module names are not supported')
    try:
      spec = find_spec(modulename)
    except ImportError as err:
      die(f'error: {_uncapitalize(err.msg)}')
    filename = spec.origin
    loader = spec.loader
    source = loader.get_source(spec.name)
  else:
    if not args:
      die('error: must specify either scriptfile or module (-m)')
    spec = None
    filename = args.pop(0)
    loader = None
    try:
      with open(filename, 'r') as fp:
        source = fp.read()
    except OSError as err:
      die(f'error: {_uncapitalize(err.strerror)}: {err.filename}')

  # Parse and transform script
  try:
    source_ast = ast.parse(source, filename, 'exec')
  except Exception as err:
    traceback.print_exception(err)
    sys.exit(1)
  _DEBUG(f'AST (pre):\n{ast.dump(source_ast, indent=2, include_attributes=True)}')
  _trace_transform_module(source_ast, trace_config=trace_config)
  _DEBUG(f'{'*'*72}')
  _DEBUG(f'AST (post):\n{ast.dump(source_ast, indent=2, include_attributes=True)}')
  #_DEBUG(f'Source (post):\n{ast.unparse(source_ast)}')

  # Execute script as __main__module
  try:
    saved_main = sys.modules['__main__']
    saved_argv = sys.argv
    sys.modules['__main__'] = main = ModuleType('__main__')
    main.__dict__.update(
      __file__ = filename,
      __cached__ = None,  # We load source directly, thus bypass cache  # XXX Won't match spec?
      __loader__ = loader,
      __spec__ = spec,
    )
    main.__dict__[NAME_TRACE_DECORATOR] = trace_config.tracer
    code = compile(source_ast, filename, 'exec', dont_inherit=True)
    sys.argv = [filename] + args
    try:
      exec(code, main.__dict__, main.__dict__)
    except Exception as err:
      traceback.print_exception(err)
      sys.exit(1)
  finally:
    sys.modules['__main__'] = saved_main
    sys.argv = saved_argv

if __name__ == '__main__':
  main()
