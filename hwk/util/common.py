# (c) 2016-2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

"""
Common auxiliary functions for all utility scripts.
"""

from typing import Optional, Sequence, TextIO, Union, Literal

import sys
import os
from pathlib import Path
import re
import contextlib
import zipfile
import subprocess
import json
from urllib.parse import quote as urlescape  # Just for Linux (dbus-send arg)

__all__ = [
  'msg', 'hr', 'c', 'Color',
  'vscode_settings_dir', 'vscode_load_settings',
  'filename_escape', 'reveal_file', 'cwd',
  'zip_tree'
]


def msg(s: str = '', file: TextIO = sys.stderr) -> None:
  "Simple alias for print() builtin, to allow future customization."
  print(s, file=file)

def hr(n: int = 76, file: Optional[TextIO] = None) -> str:
  "Return a simple horizontal rule; optionally output to file stream."
  rv = "*" * n
  if file is not None:
    msg(rv, file=file)
  return rv

_COLORMAP = {
  'black': 0,
  'red': 1,
  'green': 2,
  'yellow': 3,
  'blue': 4,
  'magenta': 5,
  'cyan': 6,
  'white': 7,
}

Color = Union[
  Literal['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'],
  Literal[0, 1, 2, 3, 4, 5, 6, 7]
]
if sys.platform != "win32":
  def c(fg: Optional[Color] = None, bg: Optional[Color] = None) -> str:
    "Return ANSI color escape sequence for *nix terminals"
    if fg is None and bg is None:
      return '\033[0m'  # reset
    if isinstance(fg, str):
      fg = _COLORMAP[fg]
    if isinstance(bg, str):
      bg = _COLORMAP[bg]
    codes = []
    if fg is not None:
      codes.append(f'38;5;{fg}')
    if bg is not None:
      codes.append(f'48;5;{bg}')
    return '\033[' + ';'.join(codes) + 'm'
else:
  # Play it safe on Windows
  # TODO Test on a Windows VM in the future
  def c(fg: Optional[Color] = None, bg: Optional[Color] = None) -> str:
    return ''  # no-op    


def vscode_settings_dir(workspace: Optional[os.PathLike] = None) -> str:
  """Find .vscode folder of current workspace"""
  if workspace is None:
    workspace = os.getcwd()  # TODO Search parent folders as well?
  return os.path.join(workspace, '.vscode')

def vscode_load_settings(
  filename: str = 'settings.json', workspace: Optional[os.PathLike] = None
) -> Optional[Union[dict,list]]:
  """Load a settings JSON file from the .vscode folder"""
  pathname = os.path.join(vscode_settings_dir(workspace), filename)
  if not os.path.exists(pathname):
    return None
  with open(pathname, 'r') as fp:
    return json.load(fp)


def filename_escape(name: os.PathLike) -> str:
  """
  Replace all non-alphanumeric characters with underscores; this should
  produce a valid (and safe) filename on all O/Ses.
  """
  return re.sub(r'[^\w\d]', '_', os.fspath(name))

# Heavily modified from https://stackoverflow.com/a/50965628 (Windows-only)
def reveal_file(filename: Union[str, os.PathLike]) -> None:
  absfilename = os.path.abspath(filename)
  isdir = os.path.isdir(absfilename)
  if sys.platform == 'darwin':
    if isdir:
      subprocess.run(['/usr/bin/open', absfilename], check=True)
    else:
      subprocess.run(['/usr/bin/open', '-R', absfilename], check=True)
  elif sys.platform == 'linux':
    # Original method (see https://askubuntu.com/q/1109908), but not guaranteed to work
    #   (e.g., RawTherapee decided to make itself the default handler for inode/directory)
    # subprocess.run(
    #   f'gtk-launch "`xdg-mime query default inode/directory`" {shlex.quote(absfilename)}', 
    #   shell=True, check=True
    # )
    # Method used by e.g., web-browsers (see, e.g., https://unix.stackexchange.com/q/487054 or
    #   https://www.freedesktop.org/wiki/Specifications/file-manager-interface/), based on DBus
    if isdir:
      dbus_method = 'org.freedesktop.FileManager1.ShowFolders'
    else:
      dbus_method = 'org.freedesktop.FileManager1.ShowItems'
    subprocess.run([
      '/usr/bin/dbus-send',
      '--session', '--type=method_call', '--dest=org.freedesktop.FileManager1',
      '/org/freedesktop/FileManager1', dbus_method,
      f'array:string:file://{urlescape(absfilename)}', 'string:',
    ], check=True)
    #  dbus-send --session --dest=org.freedesktop.FileManager1 /org/freedesktop/FileManager1 org.freedesktop.FileManager1.ShowItems array:string:"file://<abspath>" string:""
  elif sys.platform == 'win32':
    explorer_path = os.path.abspath(Path(os.getenv('WINDIR', ''), 'explorer.exe'))
    if os.path.isdir(filename):
        subprocess.run([explorer_path, absfilename])
    else:
        subprocess.run([explorer_path, '/select,', absfilename])
  else:
    raise RuntimeError("Unknown system platform; don't know how to open file manager")

@contextlib.contextmanager
def cwd(path: Path):
  save_cwd = Path.cwd()
  os.chdir(path)
  try:
    yield
  finally:
    os.chdir(save_cwd)


def _match_any(path: Path, patterns: Sequence[str]) -> bool:
  return any(path.match(pat) for pat in patterns)

def zip_tree(
  filename: Path, 
  root_dir: Path, 
  base_dir: Path = Path(), 
  exclude_patterns: Sequence[str] = (),
  verbose: bool = True,
) -> None:
  """
  Create a zipfile with all files within a directory (recursively).
  Mostly mirrors the shutil.make_archive() function, but adds options
  to skip/exclude files based on glob patterns.
  """
  included = []
  excluded = []
  with zipfile.ZipFile(filename, 'w', compression=zipfile.ZIP_DEFLATED) as zip:
    with cwd(root_dir):
      for dirpath, dirnames, filenames in os.walk(base_dir):
        # Prune directories that match exclude
        reldirpaths = [
          (dir, rp := Path(dirpath, dir), _match_any(rp, exclude_patterns)) 
          for dir in dirnames
        ]
        excluded += [
          Path(rp, '**') 
          for _, rp, excl in reldirpaths 
          if excl
        ]          
        dirnames[:] = [
          dir for dir, _, excl in reldirpaths 
          if not excl
        ]

        # Add files to zipfile (if not excluded)
        for fname in filenames:
          relpath = Path(dirpath, fname)
          if not _match_any(relpath, exclude_patterns):
            zip.write(os.fspath(relpath))
            included.append(relpath)
          else:
            excluded.append(relpath)

  if verbose:
    msg("INCLUDED FILES:")
    for fn in included:
      msg(f" \u2713 {fn}")
    msg("EXCLUDED FILES/FOLDERS:")
    for fn in excluded:
      msg(f" \u2717 {fn}")
  
            


