# Copyright 2023 The JAX Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from functools import partial
import glob
import logging
import math
import os
import tempfile

from absl.testing import absltest
import jax
from jax._src import config
from jax._src import test_util as jtu
from jax._src.pjit import pjit
from jax.experimental import profiler as exp_profiler
import jax.numpy as jnp
from jax.sharding import NamedSharding
from jax.sharding import PartitionSpec as P
import numpy as np

jax.config.parse_flags_with_absl()


@jtu.pytest_mark_if_available('multiaccelerator')
class PgleTest(jtu.JaxTestCase):

  def test_auto_pgle(self):
    mesh = jtu.create_global_mesh((2,), ('x',))

    @partial(
        jax.jit,
        in_shardings=NamedSharding(mesh, P('x')),
        out_shardings=NamedSharding(mesh, P('x')),
    )
    def f(x, y):
      return x @ y

    shape = (16, 16)
    x = jnp.arange(math.prod(shape)).reshape(shape).astype(np.float32)
    y = x + 1

    with config.pgle_data_collecting_retries(2):
      f_lowered = f.lower(x, y)
      compiled = f_lowered.compile()

    for _ in range(10):
      self.assertArraysEqual(compiled(x, y), x @ y)

  def testPassingFDOProfile(self):
    mesh = jtu.create_global_mesh((2,), ('x',))

    @partial(
        jax.jit,
        in_shardings=NamedSharding(mesh, P('x')),
        out_shardings=NamedSharding(mesh, P('x')),
    )
    def f(x, y):
      return x @ y

    shape = (16, 16)
    x = jnp.arange(math.prod(shape)).reshape(shape).astype(np.float32)
    y = x + 1

    with config.pgle_data_collecting_retries(0):
      f_lowered = f.lower(x, y)
      compiled = f_lowered.compile()

    with tempfile.TemporaryDirectory() as tmpdir:
      jax.profiler.start_trace(tmpdir)
      compiled(x, y)
      jax.profiler.stop_trace()
      directories = glob.glob(os.path.join(tmpdir, 'plugins/profile/**/'))
      directories = [d for d in directories if os.path.isdir(d)]
      rundir = directories[-1]
      logging.info('rundir: %s', rundir)
      fdo_profile = exp_profiler.get_profiled_instructions_proto(rundir)

    if jtu.test_device_matches(['gpu']) and jtu.is_device_cuda():
      self.assertIn(b'custom', fdo_profile)

    logging.info('fdo_profile: %s', fdo_profile)
    # Test pass fdo_profile as compiler_options API works.
    f_lowered.compile(compiler_options={'fdo_profile': fdo_profile})


if __name__ == '__main__':
  absltest.main(testLoader=jtu.JaxTestLoader())
