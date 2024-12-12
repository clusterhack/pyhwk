# (c) 2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import ast
from collections import deque
import symtable
import weakref

from typing import Iterable, Iterator, Literal, Optional

type CompileMode = Literal['exec', 'single', 'eval']
type ASTRoot = ast.Module | ast.Expression | ast.Interactive

# Recursive isn't the most efficient; probably doesn't matter, but..
# def walk_symtable(tbl: symtable.SymbolTable) -> Iterator[symtable.SymbolTable]:
  # """
  # Generator that traverses all SymbolTable child nodes, and yields them in depth-first order.
  # """
#   yield tbl
#   for child in tbl.get_children():
#     yield from walk_symtable(child)

def walk_symtable(root_tbl: symtable.SymbolTable) -> Iterator[symtable.SymbolTable]:
  # Non-recursive depth-first seems ~25% faster (based on very quick-n-dirty trials); FWIW
  """
  Generator that traverses all SymbolTable child nodes, and yields them in depth-first order.
  """
  q = deque([root_tbl])
  while q:
    tbl = q.popleft()
    q.extend(tbl.get_children())
    yield tbl

def get_global_names(source: str, filename: str, compile_mode: CompileMode) -> set[str]:
  """
  Return a set of all identifiers within global scope in given program source code.
  First builds symbol table using the symtable module, then walks the table to collect
  the names of all symbols for which is_global() is True.
  """
  names = set()
  root_tbl = symtable.symtable(source, filename, compile_mode)
  for tbl in walk_symtable(root_tbl):
    names.update(sym.get_name() for sym in tbl.get_symbols() if sym.is_global())
  return names

def get_ast_names(ast_root: ast.AST) -> set[str]:
  """
  Return set of identifiers from nodes in the AST tree, regardless of name scope.
  The set *may* not be complete: currently, Name, alias, FunctionDef, and ClassDef nodes are considered.
  """
  names = set()
  for node in ast.walk(ast_root):
    if isinstance(node, ast.Name):
      names.add(node.id)
    elif isinstance(node, ast.alias):
      names.add(node.asname or node.name)
    elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
      names.add(node.name)
  return names

def fill_ast_parents(ast_root: ast.AST) -> None:
  """
  Traverse ast and fill in parent links, in node.parent attribute.
  Root nodes have parent == None. All other nodes hold a weakref.proxy to the parent node.
  """
  # We essentially reimplement ast.walk here...
  ast_root.parent = None
  q = deque([ast_root])
  while q:
    node = q.popleft()
    for child in ast.iter_child_nodes(node):
      child.parent = weakref.proxy(node)
      q.append(child)

def copy_ast_locations(
  dst_node: ast.AST,
  src_node: ast.AST,
  *, 
  mode: Literal['span', 'start', 'end'] = 'span',
):
  """
  Copy source code location attributes from `src_node` to `dst_node`.

  If `mode == 'span'`, then all attributes are copied as-is.
  If `mode == 'start'' then both `dst.lineno` and `dst.end_lineno` are set to `src.lineno`
  and both `dst.col_offset` and `dst.end_col_offset` are set to `src.col_offset`.
  If `mode == 'end'`, then `src.end_lineno` and `src.end_col_offset` are used instead.

  If `src` is missing any of these attributes, then the original `dst` attributes values
  will also be deleted.
  """
  def copy_attr(dst_name: str, src_name: str):
    try:
      setattr(dst_node, dst_name, getattr(src_node, src_name))
    except AttributeError:
      delattr(dst_node, dst_name)

  if mode not in ('span', 'start', 'end'):
    raise ValueError("mode must be 'span', 'start', or 'end'")
  copy_attr('lineno', 'lineno' if mode != 'end' else 'end_lineno')
  copy_attr('col_offset', 'col_offset' if mode != 'end' else 'end_col_offset')
  copy_attr('end_lineno', 'end_lineno' if mode != 'start' else 'lineno')
  copy_attr('end_col_offset', 'end_col_offset' if mode != 'start' else 'col_offset')

def fill_ast_missing_locations(
  ast_root: ast.AST,
  lineno: int = 1, col_offset: int = 0,
  *,
  end_lineno: Optional[int] = None, end_col_offset: Optional[int] = None,
) -> None:
  """
  Modify all AST nodes with missing locations, setting them all to the given values.
  If end_lineno and end_col_offset are not explicitly specified,
  they are set to lineno and col_offset, respectively
  """
  if end_lineno is None:
    end_lineno = lineno
  if end_col_offset is None:
    end_col_offset = col_offset
  for node in ast.walk(ast_root):
    if 'lineno' in node._attributes and getattr(node, 'lineno', None) is None:
      node.lineno = lineno
    if 'end_lineno' in node._attributes and getattr(node, 'end_lineno', None) is None:
      node.end_lineno = end_lineno
    if 'col_offset' in node._attributes and getattr(node, 'col_offset', None) is None:
      node.col_offset = col_offset
    if 'end_col_offset' in node._attributes and getattr(node, 'end_col_offset', None) is None:
      node.end_col_offset = end_col_offset

def ast_module_to_function(
  fn_name: str,
  mod: ast.Module, 
  scope: Literal['global', 'nonlocal'] = 'global',
  *,
  mod_names: Optional[Iterable[str]] = None,
) -> tuple[ast.FunctionDef, ast.Call]:
  """
  Wrap a module into a function. Returns the function definition and a corresponding function call.

  Calling the function should have the same effect in terms of global-level assignments to
  all names in mod_names. If mod_names isn't specified, then all identifiers in the mod ast
  (regardless of scope) will be used by default.

  Returned ast nodes have source code location attributes filled-in as reasonaby as possible.

  The original ast is not modified.
  """
  if scope == 'global':
    AstScope = ast.Global
  elif scope == 'nonlocal':
    AstScope = ast.Nonlocal
  else:
    raise ValueError("scope must be either 'global' or 'nonlocal'")

  fn_body = mod.body.copy() # Do not modify original ast

  start_lineno, start_col_offset = 1, 0  # Fallback defaults
  if len(mod.body) > 0:
    first_stmt = mod.body[0]
    start_lineno = getattr(first_stmt, 'lineno', start_lineno)
    start_col_offset = getattr(first_stmt, 'col_offset', start_col_offset)

  if mod_names is None:
    # get_ast_names returns *all* identifiers, regardless of scope.
    # However, since these are only used in an injected global/nonlocal statement, that should be fine
    mod_names = get_ast_names(mod)
  mod_names = sorted(mod_names)  # Collect into list, sorted
  if len(mod_names) > 0:
    scope_stmt = AstScope(names=mod_names)
    fill_ast_missing_locations(scope_stmt, start_lineno, start_col_offset)
    fn_body.insert(0, scope_stmt)

  last_lineno, last_col_offset = start_lineno, start_col_offset  # Fallback defaults
  last_end_lineno, last_end_col_offset = last_lineno, last_col_offset  # Ditto
  if len(mod.body) > 0 and isinstance(mod.body[-1], ast.Expr):
    last_stmt = mod.body[-1]
    last_lineno = getattr(last_stmt, 'lineno', last_lineno)
    last_col_offset = getattr(last_stmt, 'col_offset', last_col_offset)
    last_end_lineno = getattr(last_stmt, 'end_lineno', last_end_lineno)
    last_end_col_offset = getattr(last_stmt, 'end_col_offset', last_end_col_offset)
    ret_stmt = ast.Return(value=last_stmt.value)
    fill_ast_missing_locations(
      ret_stmt,
      last_lineno, last_col_offset,
      end_lineno=last_end_lineno, end_col_offset=last_end_col_offset,
    )
    fn_body[-1] = ret_stmt

  fn_def_stmt = ast.FunctionDef(
    name=fn_name,
    args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
    body=fn_body,
  )
  fill_ast_missing_locations(
    fn_def_stmt,
    start_lineno, start_col_offset,
    end_lineno=last_end_lineno, end_col_offset=last_end_col_offset,
  )

  call_stmt = ast.Call(
    func=ast.Name(id=fn_name, ctx=ast.Load()),
    args=[],
    keywords=[],
  )
  fill_ast_missing_locations(
    call_stmt,
    last_lineno, last_col_offset,
    end_lineno=last_end_lineno, end_col_offset=last_end_col_offset,
  )

  return fn_def_stmt, call_stmt

