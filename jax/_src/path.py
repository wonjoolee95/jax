# Copyright 2022 The JAX Authors.
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

import functools
import logging
import pathlib

logger = logging.getLogger(__name__)

Path = pathlib.Path

@functools.cache
def get_path_implementation():
  # We import etils lazily, because it can be slow to import.
  try:
    import etils.epath as epath
    logger.debug("etils.epath found. Using etils.epath for file I/O.")
    return epath.Path
  except ImportError:
    logger.debug("etils.epath was not found. Using pathlib for file I/O.")
    return pathlib.Path

# If etils.epath (aka etils[epath] to pip) is present, we prefer it because it
# can read and write to, e.g., GCS buckets. Otherwise we use the builtin
# pathlib and can only read/write to the local filesystem.
def get_path(*args) -> Path:
  return get_path_implementation()(*args)
