# (c) 2016-2023 Spiros Papadimitriou <spapadim@gmail.com>
#
# This file is released under the MIT License:
#    https://opensource.org/licenses/MIT
# This software is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.

import random
from contextlib import contextmanager


class MockCircularRandom(random.Random):
  FAKE_VERSION = 1000

  # Workaround based on https://stackoverflow.com/q/5148198
  def __new__(cls, *args, **kwargs):
    return super(MockCircularRandom, cls).__new__(cls, None)

  def __init__(self, values, normalize=None):
    assert len(values) > 0
    if normalize is not None:
      values = [float(v)/normalize for v in values]
    # assert max(values) < 1.0  # FIXME - Currently we use string values to cause tests to barf
    self.values = values
    self.pos = 0  # position of next value to be returned

  def random(self):
    val = self.values[self.pos]
    self.pos = (self.pos + 1) % len(self.values)
    return val

  def seed(self, a=None):
    self.pos = 0  # always reset

  def getstate(self):
    return self.FAKE_VERSION, len(self.values), self.pos

  def setstate(self, state):
    version, n, pos = state
    assert version == self.FAKE_VERSION
    assert n == len(self.values)
    self.pos = pos

  def jumpahead(self, n):
    self.pos = (self.pos + n) % len(self.values)


def patch_random(rndmodule, instance):
  """Modifies the module-global instance of the random number generator in the
  given loaded instance of Python's random."""
  rndmodule._inst = instance
  rndmodule.seed = instance.seed
  rndmodule.random = instance.random
  rndmodule.uniform = instance.uniform
  rndmodule.triangular = instance.triangular
  rndmodule.randint = instance.randint
  rndmodule.choice = instance.choice
  rndmodule.randrange = instance.randrange
  rndmodule.sample = instance.sample
  rndmodule.shuffle = instance.shuffle
  rndmodule.normalvariate = instance.normalvariate
  rndmodule.lognormvariate = instance.lognormvariate
  rndmodule.expovariate = instance.expovariate
  rndmodule.vonmisesvariate = instance.vonmisesvariate
  rndmodule.gammavariate = instance.gammavariate
  rndmodule.gauss = instance.gauss
  rndmodule.betavariate = instance.betavariate
  rndmodule.paretovariate = instance.paretovariate
  rndmodule.weibullvariate = instance.weibullvariate
  rndmodule.getstate = instance.getstate
  rndmodule.setstate = instance.setstate
  rndmodule.jumpahead = instance.jumpahead
  rndmodule.getrandbits = instance.getrandbits


@contextmanager
def mock_random(values, normalize=None):
  import random
  saved_inst = random._inst
  mock_inst = MockCircularRandom(values, normalize)
  patch_random(random, mock_inst)
  try:
    yield
  finally:
    patch_random(random, saved_inst)
