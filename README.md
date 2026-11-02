# mathnix

A native Nix evaluator plugin exposing **`builtins.math`**: 69 curried functions
for real-valued arithmetic, transcendental and special functions, exact integer
math, and list/vector reductions. No runtime dependencies beyond Nix and its C++
runtime. All operations work in pure evaluation; nothing executes external
programs, reads files, or uses randomness.

## Quick start

```sh
nix build
./result/bin/nix-math eval --json --expr 'builtins.math.sqrt 2'
nix run . -- eval --json --expr 'builtins.math.comb 52 5'
./result/bin/nix-math eval --json --file examples/demo.nix
```

`nix-math` forwards arguments to a **matching, packaged Nix** with the plugin
loaded. It supports normal Nix commands, including `eval`, `repl`, and `build`.
The flake supports `x86_64-linux` and `aarch64-linux`.

**ABI compatibility:** the default package builds against pinned Nix 2.34.6.
Nix's C++ plugin API is unstable, even between patch releases. Do not load this
library into an arbitrary system Nix. Use `nix-math`, or build against your exact
Nix components:

```nix
pkgs.callPackage ./package.nix {
  nixComponents = pkgs.nixVersions.nixComponents_2_34;
}
```

For an evaluator that exactly matches the build dependencies, direct loading is:

```sh
nix --option plugin-files "$PWD/result/lib/nix/plugins/mathnix.so" \
  eval --json --expr 'builtins.math.sin (builtins.math.pi / 2)'
```

Loading native plugins executes trusted native code in the evaluator. Only load
libraries you trust. No daemon configuration or global `nix.conf` changes are
needed. The launcher supplies its own `plugin-files` setting; to combine plugins,
explicitly supply a list of mutually ABI-compatible libraries.

## Using the API

```nix
let m = builtins.math; in {
  area = m.pi * m.pow 3 2;
  sine45 = m.sin (m.radians 45);
  roots = builtins.map m.sqrt [ 1 4 9 ];
  powerOfTwo = (m.pow 2) 10;
  exactPower = m.powInt 2 60;
  length = m.norm [ 3 4 12 ];
  accurate = m.sum [ 1.0e16 1 (-1.0e16) ]; # 1.0
  nearZero = m.isCloseWith { absTol = 1.0e-15; } (m.sin m.pi) 0;
}
```

Parenthesize negative arguments: `m.cbrt (-8)`, not `m.cbrt -8` (subtraction).

### Numeric contract

- Real functions accept Nix integers or floats and return IEEE-754 binary64
  floats. Integer inputs may lose precision above 2^53 when converted to floats.
  `div 5 2` is **2.5**, unlike Nix's integer `/`.
- `abs` preserves the input's integer/float type. `sign`, rounding operations,
  and the integer helpers return signed 64-bit integers. Rounding an integer
  preserves it exactly, including values above 2^53.
- Integer helpers require actual integers, not integral floats. Overflow is an
  error, never wrapping or silently converting to float.
- Inputs and results must be finite. Invalid domains, poles, division by zero,
  and floating-point overflow raise Nix evaluation errors. Underflow may produce
  subnormal numbers or signed zero. There are intentionally no infinity/NaN
  constants. Errors follow native builtin behavior; `builtins.tryEval` does not
  catch every evaluator error.
- Use `ldexp 1 (-1074)` to construct the smallest positive subnormal; Nix's
  parser can reject subnormal float literals even though the evaluator supports them.
- Transcendental functions use the platform C/C++ math library. Last-bit results
  can vary by architecture/libc; these are not symbolic or arbitrary-precision
  operations. `sum`, `mean`, and `dot` use widened compensated accumulation,
  not a guarantee of correctly rounded arbitrary-precision results.
- All arguments needed by an operation are forced when its result is demanded;
  ordinary Nix laziness and partial application still apply.

### Constants

| Attribute | Value/meaning |
| --- | --- |
| `pi`, `tau`, `e`, `phi` | π, 2π, Euler's number, golden ratio |
| `sqrt2`, `ln2`, `ln10` | √2, ln(2), ln(10) |
| `epsilon` | 2^-52, spacing above 1.0 |
| `minFloat`, `maxFloat` | Smallest positive **normal** float; largest finite float |
| `minInt`, `maxInt` | -2^63; 2^63 - 1 |
| `version` | Plugin version string (`"0.1.0"`) |

### Arithmetic and powers

| Function | Meaning |
| --- | --- |
| `add a b`, `sub a b`, `mul a b`, `div a b` | Floating-point arithmetic |
| `abs x`, `sign x` | Absolute value; integer sign -1/0/1 |
| `min a b`, `max a b` | Smaller/larger input, returned as a float |
| `clamp lower upper x` | Restrict x to inclusive bounds; lower ≤ upper |
| `lerp a b t` | Linear interpolation; t outside [0,1] extrapolates |
| `fma a b c` | a × b + c with one rounding |
| `sqrt x`, `cbrt x` | Square root (x ≥ 0); real cube root |
| `pow base exponent` | Real power; `pow 0 0 = 1.0` |
| `hypot a b` | √(a² + b²), avoiding unnecessary intermediate overflow |
| `exp x`, `exp2 x`, `expm1 x` | e^x, 2^x, accurate e^x - 1 |
| `log x`, `log2 x`, `log10 x` | Natural/base-2/base-10 logarithm; x > 0 |
| `log1p x` | Accurate ln(1 + x); x > -1 |
| `logBase base x` | Logarithm to base; base > 0, base ≠ 1, x > 0 |

### Trigonometry and special functions

Angles are **radians**, including inverse-function results.

| Function | Meaning |
| --- | --- |
| `sin x`, `cos x`, `tan x` | Trigonometric functions |
| `asin x`, `acos x`, `atan x` | Inverse functions; asin/acos require \|x\| ≤ 1 |
| `atan2 y x` | Quadrant-aware inverse tangent (note argument order) |
| `sinh x`, `cosh x`, `tanh x` | Hyperbolic functions |
| `asinh x`, `acosh x`, `atanh x` | Inverses; acosh requires x ≥ 1, atanh \|x\| < 1 |
| `radians x`, `degrees x` | Degrees → radians; radians → degrees |
| `erf x`, `erfc x` | Error function; accurate complementary error function |
| `gamma x`, `lgamma x` | Γ(x); ln(\|Γ(x)\|), **not** ln(Γ(x)) for negative Γ |

### Rounding and float utilities

| Function | Meaning |
| --- | --- |
| `ceil x`, `floor x`, `trunc x` | Toward +∞, -∞, zero; checked integer result |
| `round x` | Nearest integer; ties **away from zero**, not ties-to-even |
| `fmod a b` | a - trunc(a/b) × b; sign follows a |
| `remainder a b` | IEEE remainder, nearest-integer quotient, ties-to-even |
| `copysign a b` | Magnitude of a with b's sign, including signed zero |
| `nextAfter a b` | Next representable float from a toward b; b returned if equal |
| `fdim a b` | Positive difference max(a - b, 0) |
| `frexp x` | `{ fraction; exponent; }`, x = fraction × 2^exponent |
| `ldexp x exponent` | x × 2^exponent; exponent is an integer in native int range |
| `modf x` | `{ integral; fraction; }`, both floats with x's sign |
| `isClose a b` | Approximate equality; relTol = 1e-9, absTol = 0 |
| `isCloseWith options a b` | Options `{ relTol ? 1e-9; absTol ? 0; }` |

Approximate equality means `|a-b| <= max(absTol, relTol * max(|a|, |b|))`.
Tolerances must be finite and nonnegative; unknown options are errors. Supply an
absolute tolerance for comparisons to zero.

### Exact integer helpers

| Function | Meaning |
| --- | --- |
| `gcd a b`, `lcm a b` | Nonnegative GCD/LCM; negative inputs allowed |
| `factorial n` | n!; 0 ≤ n ≤ 20 (21! exceeds signed 64-bit range) |
| `comb n k` | Binomial coefficient; nonnegative n/k, zero when k > n |
| `perm n k` | Ordered selections n!/(n-k)!; same domain as comb |
| `isqrt n` | Exact floor(√n); n ≥ 0 |
| `powInt base exponent` | Exact integer power; exponent ≥ 0; 0^0 = 1 |
| `mod x modulus` | Euclidean remainder in [0, modulus); modulus > 0 |

`gcd 0 0 = 0`; LCM is zero when either argument is zero. A nonnegative result
equal to 2^63 is still overflow, even though -2^63 is a valid input.

### Lists and vectors

| Function | Meaning |
| --- | --- |
| `sum xs` | Compensated sum; empty → 0.0 |
| `product xs` | Left-to-right floating product; empty → 1.0; intermediate overflow errors |
| `mean xs` | Arithmetic mean; empty is an error; avoids intermediate sum overflow |
| `norm xs` | Euclidean norm with scaled hypot accumulation; empty → 0.0 |
| `dot xs ys` | Widened compensated dot product; equal lengths required; empty → 0.0 |

## Development and tests

```sh
nix flake check
nix develop
cmake -S . -B build
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

The derivation tests the compiled and installed libraries and the installed
launcher. Tests cover every exported function, wrong types, invalid domains,
64-bit boundaries, signed zero/subnormals, cancellation, overflow, pure eval,
currying, allocation stress, and randomized comparisons to Python's math library.
