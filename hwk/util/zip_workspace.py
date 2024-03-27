# (c) 2016-2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

"""
Command-line utility to zip workspace folder, for submission.

Assumes that the cwd is the workspace root. 
Will create a zipfile in parent folder, with name "{folderName}_{userName}_r{date}_{time}.zip"
"""

import sys
from pathlib import Path
import datetime

from .common import msg, hr, filename_escape, zip_tree, reveal_file


_ZIP_EXCLUDE = [
  '__pycache__',
  '.DS_Store',
  'util/*.py',
  'tests/*.py'
]

def main():
  # if not any(fmt == 'zip' for fmt, _ in shutil.get_archive_formats()):
  #   msg("FATAL: Your system is missing zlib; cannot create zipfile!")

  cwd = Path.cwd()
  parentpath = cwd.parent
  foldername = cwd.relative_to(parentpath)

  if not Path(cwd, '.vscode').is_dir():
    msg("ERROR: Could not find '.vscode' folder; this script should be executed within workspace root folder!")
    sys.exit(1)
  if not foldername.match('hw[0-9]'):
    msg("WARNING: Workspace folder name does not match 'hwN'; proceeding anyway...")

  if len(sys.argv) < 2:
    now = datetime.datetime.now()
    suffix = now.strftime('r%Y%m%d_%H%M%S')
  else:
    if len(sys.argv) != 2:
      msg("WARNING: Unexpected command line arguments; ignoring...")
    suffix = sys.argv[1]
  zipname = f'{filename_escape(foldername)}_{suffix}.zip'
  zippath = Path(parentpath, zipname)

  if zippath.exists():
    msg("ERROR: Zipfile already exists (please delete first); aborting...")
    sys.exit(1)

  msg(hr())
  zip_tree(
    zippath,
    root_dir=parentpath,
    base_dir=foldername,
    exclude_patterns=_ZIP_EXCLUDE,
    verbose=True,
  )

  msg(hr())
  msg( "Zipfile successfully created!")
  msg(f"Zipfile name: {zipname}")
  msg(f"Zipfile location: {parentpath}")
  msg(hr())
  msg("Please upload this zipfile as your submission; if Finder/Explorer")
  msg("did not open automatically to reveal the zipfile, please manually")
  msg("navigate to folder location noted above.")
  msg(hr())

  try:
    reveal_file(zippath)
  except Exception:
    msg("(Could not open file manager; please locate file manually)")



if __name__ == '__main__':
  main()
