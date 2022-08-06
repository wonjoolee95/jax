# Copyright 2019 Google LLC
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

# Array type functions.
#
# JAX dtypes differ from NumPy in both:
# a) their type promotion rules, and
# b) the set of supported types (e.g., bfloat16),
# so we need our own implementation that deviates from NumPy in places.


import functools
from typing import Any, Dict, List

import numpy as np

import jax
from jax._src.config import flags, config
from jax._src.lib import xla_client

from jax._src import traceback_util
traceback_util.register_exclusion(__file__)

FLAGS = flags.FLAGS

# bfloat16 support
bfloat16: type = xla_client.bfloat16
_bfloat16_dtype: np.dtype = np.dtype(bfloat16)

# Default types.
bool_: type = np.bool_
int_: type = np.int32 if config.jax_default_dtype_bits == '32' else np.int64
uint: type = np.uint32 if config.jax_default_dtype_bits == '32' else np.uint64
float_: type = np.float32 if config.jax_default_dtype_bits == '32' else np.float64
complex_: type = np.complex64 if config.jax_default_dtype_bits == '32' else np.complex128
_default_types = {'b': bool_, 'i': int_, 'u': uint, 'f': float_, 'c': complex_}

# Trivial vectorspace datatype needed for tangent values of int/bool primals
float0: np.dtype = np.dtype([('float0', np.void, 0)])

_dtype_to_32bit_dtype = {
    np.dtype('int64'): np.dtype('int32'),
    np.dtype('uint64'): np.dtype('uint32'),
    np.dtype('float64'): np.dtype('float32'),
    np.dtype('complex128'): np.dtype('complex64'),
}

# Note: we promote narrow types to float32 here for backward compatibility
# with earlier approaches. We might consider revisiting this, or perhaps
# tying the logic more closely to the type promotion lattice.
_dtype_to_inexact = {
    np.dtype(k): np.dtype(v) for k, v in [
        ('bool', 'float32'),
        ('uint8', 'float32'), ('int8', 'float32'),
        ('uint16', 'float32'), ('int16', 'float32'),
        ('uint32', 'float32'), ('int32', 'float32'),
        ('uint64', 'float64'), ('int64', 'float64')
    ]
}


def _to_inexact_dtype(dtype):
  """Promotes a dtype into an inexact dtype, if it is not already one."""
  dtype = np.dtype(dtype)
  return _dtype_to_inexact.get(dtype, dtype)


def _to_complex_dtype(dtype):
  ftype = _to_inexact_dtype(dtype)
  if ftype in [np.dtype('float64'), np.dtype('complex128')]:
    return np.dtype('complex128')
  return np.dtype('complex64')


@functools.lru_cache(maxsize=None)
def _canonicalize_dtype(x64_enabled, dtype):
  """Convert from a dtype to a canonical dtype based on config.x64_enabled."""
  if type(dtype) in jax.core.custom_eltypes:
    return dtype
  try:
    dtype = np.dtype(dtype)
  except TypeError as e:
    raise TypeError(f'dtype {dtype!r} not understood') from e

  if x64_enabled:
    return dtype
  else:
    return _dtype_to_32bit_dtype.get(dtype, dtype)

def canonicalize_dtype(dtype):
  return _canonicalize_dtype(config.x64_enabled, dtype)

# Default dtypes corresponding to Python scalars.
python_scalar_dtypes : dict = {
  bool: np.dtype('bool'),
  int: np.dtype('int64'),
  float: np.dtype('float64'),
  complex: np.dtype('complex128'),
}

def scalar_type_of(x):
  typ = dtype(x)
  if typ == bfloat16:
    return float
  elif np.issubdtype(typ, np.bool_):
    return bool
  elif np.issubdtype(typ, np.integer):
    return int
  elif np.issubdtype(typ, np.floating):
    return float
  elif np.issubdtype(typ, np.complexfloating):
    return complex
  else:
    raise TypeError(f"Invalid scalar value {x}")


def _scalar_type_to_dtype(typ: type, value: Any = None):
  """Return the numpy dtype for the given scalar type.

  Raises
  ------
  OverflowError: if `typ` is `int` and the value is too large for int64.

  Examples
  --------
  >>> _scalar_type_to_dtype(int)
  dtype('int32')
  >>> _scalar_type_to_dtype(float)
  dtype('float32')
  >>> _scalar_type_to_dtype(complex)
  dtype('complex64')
  >>> _scalar_type_to_dtype(int)
  dtype('int32')
  >>> _scalar_type_to_dtype(int, 0)
  dtype('int32')
  >>> _scalar_type_to_dtype(int, 1 << 63)  # doctest: +IGNORE_EXCEPTION_DETAIL
  Traceback (most recent call last):
  OverflowError: Python int 9223372036854775808 too large to convert to int32
  """
  dtype = canonicalize_dtype(python_scalar_dtypes[typ])
  if typ is int and value is not None:
    if value < np.iinfo(dtype).min or value > np.iinfo(dtype).max:
      raise OverflowError(f"Python int {value} too large to convert to {dtype}")
  return dtype


def coerce_to_array(x, dtype=None):
  """Coerces a scalar or NumPy array to an np.array.

  Handles Python scalar type promotion according to JAX's rules, not NumPy's
  rules.
  """
  if dtype is None and type(x) in python_scalar_dtypes:
    dtype = _scalar_type_to_dtype(type(x), x)
  return np.asarray(x, dtype)

iinfo = np.iinfo

class _Bfloat16MachArLike:
  def __init__(self):
    smallest_normal = float.fromhex("0x1p-126")
    self.smallest_normal = bfloat16(smallest_normal)


class finfo(np.finfo):
  __doc__ = np.finfo.__doc__
  _finfo_cache: Dict[np.dtype, np.finfo] = {}
  @staticmethod
  def _bfloat16_finfo():
    def float_to_str(f):
      return "%12.4e" % float(f)

    bfloat16 = _bfloat16_dtype.type
    tiny = float.fromhex("0x1p-126")
    resolution = 0.01
    eps = float.fromhex("0x1p-7")
    epsneg = float.fromhex("0x1p-8")
    max = float.fromhex("0x1.FEp127")

    obj = object.__new__(np.finfo)
    obj.dtype = _bfloat16_dtype
    obj.bits = 16
    obj.eps = bfloat16(eps)
    obj.epsneg = bfloat16(epsneg)
    obj.machep = -7
    obj.negep = -8
    obj.max = bfloat16(max)
    obj.min = bfloat16(-max)
    obj.nexp = 8
    obj.nmant = 7
    obj.iexp = obj.nexp
    obj.precision = 2
    obj.resolution = bfloat16(resolution)
    obj._machar = _Bfloat16MachArLike()
    if not hasattr(obj, "tiny"):
      obj.tiny = bfloat16(tiny)

    obj._str_tiny = float_to_str(tiny)
    obj._str_smallest_normal = float_to_str(tiny)
    obj._str_max = float_to_str(max)
    obj._str_epsneg = float_to_str(epsneg)
    obj._str_eps = float_to_str(eps)
    obj._str_resolution = float_to_str(resolution)
    return obj

  def __new__(cls, dtype):
    if isinstance(dtype, str) and dtype == 'bfloat16' or dtype == _bfloat16_dtype:
      if _bfloat16_dtype not in cls._finfo_cache:
        cls._finfo_cache[_bfloat16_dtype] = cls._bfloat16_finfo()
      return cls._finfo_cache[_bfloat16_dtype]
    return super().__new__(cls, dtype)

def _issubclass(a, b):
  """Determines if ``a`` is a subclass of ``b``.

  Similar to issubclass, but returns False instead of an exception if `a` is not
  a class.
  """
  try:
    return issubclass(a, b)
  except TypeError:
    return False

def issubdtype(a, b):
  if a == "bfloat16":
    a = bfloat16
  if a == bfloat16:
    if isinstance(b, np.dtype):
      return b == _bfloat16_dtype
    else:
      return b in [bfloat16, np.floating, np.inexact, np.number]
  if not _issubclass(b, np.generic):
    # Workaround for JAX scalar types. NumPy's issubdtype has a backward
    # compatibility behavior for the second argument of issubdtype that
    # interacts badly with JAX's custom scalar types. As a workaround,
    # explicitly cast the second argument to a NumPy type object.
    b = np.dtype(b).type
  return np.issubdtype(a, b)

can_cast = np.can_cast
issubsctype = np.issubsctype

# Enumeration of all valid JAX types in order.
_weak_types = [int, float, complex]
_bool_types: List[np.dtype] = [np.dtype(bool)]
_int_types: List[np.dtype] = [
    np.dtype('uint8'),
    np.dtype('uint16'),
    np.dtype('uint32'),
    np.dtype('uint64'),
    np.dtype('int8'),
    np.dtype('int16'),
    np.dtype('int32'),
    np.dtype('int64'),
]
_float_types: List[np.dtype] = [
    np.dtype(bfloat16),
    np.dtype('float16'),
    np.dtype('float32'),
    np.dtype('float64'),
]
_complex_types: List[np.dtype] = [
    np.dtype('complex64'),
    np.dtype('complex128'),
]
_jax_types = _bool_types + _int_types + _float_types + _complex_types
_jax_dtype_set = {float0, *_bool_types, *_int_types, *_float_types, *_complex_types}

def _jax_type(dtype, weak_type):
  """Return the jax type for a dtype and weak type."""
  if weak_type:
    if dtype == bool:
      return dtype
    if dtype == _bfloat16_dtype:
      return float
    return type(dtype.type(0).item())
  return dtype

def _dtype_and_weaktype(value):
  """Return a (dtype, weak_type) tuple for the given input."""
  return dtype(value), any(value is typ for typ in _weak_types) or is_weakly_typed(value)

def _type_promotion_lattice(jax_numpy_dtype_promotion):
  """
  Return the type promotion lattice in the form of a DAG.
  This DAG maps each type to its immediately higher type on the lattice.
  """
  b1, = _bool_types
  u1, u2, u4, u8, i1, i2, i4, i8 = _int_types
  bf, f2, f4, f8 = _float_types
  c4, c8 = _complex_types
  i_, f_, c_ = _weak_types
  if jax_numpy_dtype_promotion == 'standard':
    return {
      b1: [i_],
      u1: [i2, u2], u2: [i4, u4], u4: [i8, u8], u8: [f_],
      i_: [u1, i1], i1: [i2], i2: [i4], i4: [i8], i8: [f_],
      f_: [bf, f2, c_], bf: [f4], f2: [f4], f4: [f8, c4], f8: [c8],
      c_: [c4], c4: [c8], c8: [],
    }
  elif jax_numpy_dtype_promotion == 'strict':
    return {
      i_: [f_] + _int_types,
      f_: [c_] + _float_types,
      c_: _complex_types,
      **{t: [] for t in _jax_types}
    }
  else:
    raise ValueError(
      f"Unexpected value of jax_numpy_dtype_promotion={jax_numpy_dtype_promotion!r}")

def _make_lattice_upper_bounds(jax_numpy_dtype_promotion):
  lattice = _type_promotion_lattice(jax_numpy_dtype_promotion)
  upper_bounds = {node: {node} for node in lattice}
  for n in lattice:
    while True:
      new_upper_bounds = set().union(*(lattice[b] for b in upper_bounds[n]))
      if n in new_upper_bounds:
        raise ValueError(f"cycle detected in type promotion lattice for node {n}")
      if new_upper_bounds.issubset(upper_bounds[n]):
        break
      upper_bounds[n] |= new_upper_bounds
  return upper_bounds

_lattice_upper_bounds = {
  'standard': _make_lattice_upper_bounds('standard'),
  'strict': _make_lattice_upper_bounds('strict'),
}

class TypePromotionError(ValueError):
  pass

@functools.lru_cache(512)  # don't use util.memoize because there is no X64 dependence.
def _least_upper_bound(jax_numpy_dtype_promotion, *nodes):
  """Compute the least upper bound of a set of nodes.

  Args:
    nodes: sequence of entries from _jax_types + _weak_types
  Returns:
    the _jax_type representing the least upper bound of the input nodes
      on the promotion lattice.
  """
  # This function computes the least upper bound of a set of nodes N within a partially
  # ordered set defined by the lattice generated above.
  # Given a partially ordered set S, let the set of upper bounds of n ∈ S be
  #   UB(n) ≡ {m ∈ S | n ≤ m}
  # Further, for a set of nodes N ⊆ S, let the set of common upper bounds be given by
  #   CUB(N) ≡ {a ∈ S | ∀ b ∈ N: a ∈ UB(b)}
  # Then the least upper bound of N is defined as
  #   LUB(N) ≡ {c ∈ CUB(N) | ∀ d ∈ CUB(N), c ≤ d}
  # The definition of an upper bound implies that c ≤ d if and only if d ∈ UB(c),
  # so the LUB can be expressed:
  #   LUB(N) = {c ∈ CUB(N) | ∀ d ∈ CUB(N): d ∈ UB(c)}
  # or, equivalently:
  #   LUB(N) = {c ∈ CUB(N) | CUB(N) ⊆ UB(c)}
  # By definition, LUB(N) has a cardinality of 1 for a partially ordered set.
  # Note a potential algorithmic shortcut: from the definition of CUB(N), we have
  #   ∀ c ∈ N: CUB(N) ⊆ UB(c)
  # So if N ∩ CUB(N) is nonempty, if follows that LUB(N) = N ∩ CUB(N).
  N = set(nodes)
  UB = _lattice_upper_bounds[jax_numpy_dtype_promotion]
  CUB = set.intersection(*(UB[n] for n in N))
  LUB = (CUB & N) or {c for c in CUB if CUB.issubset(UB[c])}
  if len(LUB) == 1:
    return LUB.pop()
  elif len(LUB) == 0:
    if config.jax_numpy_dtype_promotion == 'strict':
      msg = (
        f"Input dtypes {tuple(str(n) for n in nodes)} have no available implicit dtype "
        "promotion path when jax_numpy_dtype_promotion=strict. Try explicitly casting "
        "inputs to the desired output type, or set jax_numpy_dtype_promotion=standard.")
    else:
      msg = (
        f"Input dtypes {tuple(str(n) for n in nodes)} have no available implicit dtype "
        "promotion path. Try explicitly casting inputs to the desired output type.")
    raise TypePromotionError(msg)
  else:
    # If we get here, it means the lattice is ill-formed.
    raise TypePromotionError(
      f"Internal Type Promotion error: {nodes} do not have a unique least upper bound "
      f"on the specified lattice; options are {LUB}. This is an unexpected error in "
      "JAX's internal logic; please report it to the JAX maintainers."
    )

def promote_types(a, b):
  """Returns the type to which a binary operation should cast its arguments.

  For details of JAX's type promotion semantics, see :ref:`type-promotion`.

  Args:
    a: a :class:`numpy.dtype` or a dtype specifier.
    b: a :class:`numpy.dtype` or a dtype specifier.

  Returns:
    A :class:`numpy.dtype` object.
  """
  # Note: we deliberately avoid `if a in _weak_types` here because we want to check
  # object identity, not object equality, due to the behavior of np.dtype.__eq__
  a = a if any(a is t for t in _weak_types) else np.dtype(a)
  b = b if any(b is t for t in _weak_types) else np.dtype(b)
  return np.dtype(_least_upper_bound(config.jax_numpy_dtype_promotion, a, b))

def is_weakly_typed(x):
  try:
    return x.aval.weak_type
  except AttributeError:
    return type(x) in _weak_types

def is_python_scalar(x):
  try:
    return x.aval.weak_type and np.ndim(x) == 0
  except AttributeError:
    return type(x) in python_scalar_dtypes

def dtype(x, *, canonicalize=False):
  """Return the dtype object for a value or type, optionally canonicalized based on X64 mode."""
  if x is None:
    raise ValueError(f"Invalid argument to dtype: {x}.")
  elif isinstance(x, type) and x in python_scalar_dtypes:
    dt = python_scalar_dtypes[x]
  elif type(x) in python_scalar_dtypes:
    dt = python_scalar_dtypes[type(x)]
  else:
    dt = np.result_type(x)
  if dt not in _jax_dtype_set:
    raise TypeError(f"Value '{x}' with dtype {dt} is not a valid JAX array "
                    "type. Only arrays of numeric types are supported by JAX.")
  return canonicalize_dtype(dt) if canonicalize else dt

def _lattice_result_type(*args):
  dtypes, weak_types = zip(*(_dtype_and_weaktype(arg) for arg in args))
  if len(dtypes) == 1:
    out_dtype = dtypes[0]
    out_weak_type = weak_types[0]
  elif all(weak_types) and config.jax_numpy_dtype_promotion != 'strict':
    # If all inputs are weakly typed, we compute the bound of the strongly-typed
    # counterparts and apply the weak type at the end. This avoids returning the
    # incorrect result with non-canonical weak types (e.g. weak int16).
    # TODO(jakevdp): explore removing this special case.
    result_type = _least_upper_bound(config.jax_numpy_dtype_promotion,
                                     *{_jax_type(dtype, False) for dtype in dtypes})
    out_dtype = dtype(result_type)
    out_weak_type = True
  else:
    result_type = _least_upper_bound(config.jax_numpy_dtype_promotion,
                                     *{_jax_type(d, w) for d, w in zip(dtypes, weak_types)})
    out_dtype = dtype(result_type)
    out_weak_type = any(result_type is t for t in _weak_types)
  return out_dtype, (out_dtype != bool_) and out_weak_type

def result_type(*args, return_weak_type_flag=False):
  """Convenience function to apply JAX argument dtype promotion.

  Args:
    return_weak_type_flag : if True, then return a ``(dtype, weak_type)`` tuple.
      If False, just return `dtype`

  Returns:
    dtype or (dtype, weak_type) depending on the value of the ``return_weak_type`` argument.
  """
  if len(args) == 0:
    raise ValueError("at least one array or dtype is required")
  dtype, weak_type = _lattice_result_type(*(float_ if arg is None else arg for arg in args))
  if weak_type:
    dtype = _default_types['f' if dtype == _bfloat16_dtype else dtype.kind]
  dtype = canonicalize_dtype(dtype)
  return (dtype, weak_type) if return_weak_type_flag else dtype
