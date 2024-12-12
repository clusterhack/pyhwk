# (c) 2024 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

from IPython.core.interactiveshell import InteractiveShell  # Just for type annotation

from .trace import TraceMagics


def load_ipython_extension(ip: InteractiveShell) -> None:
  ip.register_magics(TraceMagics)
