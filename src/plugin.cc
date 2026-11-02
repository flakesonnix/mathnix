#include <nix/expr/eval.hh>
#include <nix/expr/primops.hh>

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdint>
#include <limits>
#include <numeric>
#include <numbers>
#include <string>
#include <string_view>

namespace {

using namespace nix;
using Int = int64_t;
constexpr auto maxInt = std::numeric_limits<Int>::max();
constexpr auto context = "while evaluating a mathnix argument";

struct Call {
    EvalState & state;
    PosIdx pos;

    [[noreturn]] void fail(std::string_view message) const
    {
        state.error<EvalError>("mathnix: %s", message).atPos(pos).debugThrow();
    }

    double number(Value & value) const
    {
        double result = state.forceFloat(value, pos, context);
        if (!std::isfinite(result)) fail("arguments must be finite numbers");
        return result;
    }

    Int integer(Value & value) const
    {
        return state.forceInt(value, pos, context).value;
    }

    Int natural(Value & value) const
    {
        auto result = integer(value);
        if (result < 0) fail("expected a nonnegative integer");
        return result;
    }

    unsigned bitIndex(Value & value) const
    {
        Int result = integer(value);
        if (result < 0 || result > 63) fail("bit index or count must be in [0, 63]");
        return static_cast<unsigned>(result);
    }

    void result(double value, Value & out) const
    {
        if (!std::isfinite(value)) fail("non-finite result (domain error or overflow)");
        out.mkFloat(value);
    }

    void rounded(double value, Value & out) const
    {
        // INT64_MAX rounds to 2^63 as a double. Use an exclusive upper bound
        // rather than comparing against its inexact floating-point conversion.
        if (value < -0x1p63 || value >= 0x1p63)
            fail("rounded result is outside the signed 64-bit integer range");
        out.mkInt(static_cast<Int>(value));
    }

    void list(Value & value) const { state.forceList(value, pos, context); }
};

template<double (*Function)(double)>
void unary(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    call.result(Function(call.number(*args[0])), out);
}

template<double (*Function)(double, double)>
void binary(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    double a = call.number(*args[0]);
    double b = call.number(*args[1]);
    call.result(Function(a, b), out);
}

template<double (*Function)(double, double, double)>
void ternary(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    double a = call.number(*args[0]);
    double b = call.number(*args[1]);
    double c = call.number(*args[2]);
    call.result(Function(a, b, c), out);
}

template<double (*Function)(double)>
void rounding(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    state.forceValue(*args[0], pos);
    // Preserve integers above 2^53 exactly, including INT64_MAX.
    if (args[0]->type() == nInt) {
        out.mkInt(args[0]->integer());
        return;
    }
    Call call{state, pos};
    call.rounded(Function(call.number(*args[0])), out);
}

double add(double a, double b) { return a + b; }
double sub(double a, double b) { return a - b; }
double mul(double a, double b) { return a * b; }
double divide(double a, double b) { return a / b; }
double minimum(double a, double b) { return std::fmin(a, b); }
double maximum(double a, double b) { return std::fmax(a, b); }
double radians(double x) { return x * (std::numbers::pi / 180.0); }
double degrees(double x) { return x * (180.0 / std::numbers::pi); }

void absolute(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    state.forceValue(*args[0], pos);
    if (args[0]->type() == nInt) {
        auto value = call.integer(*args[0]);
        if (value == std::numeric_limits<Int>::min()) call.fail("integer absolute value overflow");
        out.mkInt(value < 0 ? -value : value);
    } else {
        call.result(std::fabs(call.number(*args[0])), out);
    }
}

void sign(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    double x = Call{state, pos}.number(*args[0]);
    out.mkInt((x > 0) - (x < 0));
}

void logBase(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    double base = call.number(*args[0]);
    double x = call.number(*args[1]);
    if (base <= 0 || base == 1 || x <= 0)
        call.fail("logBase requires base > 0, base != 1, and x > 0");
    call.result(std::log(x) / std::log(base), out);
}

void clamp(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    double lower = call.number(*args[0]);
    double upper = call.number(*args[1]);
    double x = call.number(*args[2]);
    if (lower > upper) call.fail("clamp requires lower <= upper");
    call.result(std::clamp(x, lower, upper), out);
}

void frexp(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    int exponent;
    double fraction = std::frexp(call.number(*args[0]), &exponent);
    auto attrs = state.buildBindings(2);
    attrs.alloc("fraction").mkFloat(fraction);
    attrs.alloc("exponent").mkInt(exponent);
    out.mkAttrs(attrs);
}

void modf(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    double integral;
    double fraction = std::modf(call.number(*args[0]), &integral);
    auto attrs = state.buildBindings(2);
    attrs.alloc("fraction").mkFloat(fraction);
    attrs.alloc("integral").mkFloat(integral);
    out.mkAttrs(attrs);
}

void ldexp(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    double x = call.number(*args[0]);
    Int exponent = call.integer(*args[1]);
    if (exponent < std::numeric_limits<int>::min() || exponent > std::numeric_limits<int>::max())
        call.fail("ldexp exponent is outside the native int range");
    call.result(std::ldexp(x, static_cast<int>(exponent)), out);
}

bool close(double a, double b, double relative, double absolute)
{
    // Widen before subtraction/multiplication to avoid intermediate overflow.
    long double distance = std::fabs(static_cast<long double>(a) - b);
    long double scale = std::max(std::fabs(a), std::fabs(b));
    return distance <= absolute || distance <= static_cast<long double>(relative) * scale;
}

void isClose(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    double a = call.number(*args[0]);
    double b = call.number(*args[1]);
    out.mkBool(close(a, b, 1e-9, 0));
}

void isCloseWith(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    state.forceAttrs(*args[0], pos, context);
    double relative = 1e-9;
    double absolute = 0;
    for (const auto & attr : *args[0]->attrs()) {
        std::string_view name = state.symbols[attr.name];
        if (name == "relTol") relative = call.number(*attr.value);
        else if (name == "absTol") absolute = call.number(*attr.value);
        else call.fail("isCloseWith only accepts relTol and absTol options");
    }
    if (relative < 0 || absolute < 0) call.fail("tolerances must be nonnegative");
    double a = call.number(*args[1]);
    double b = call.number(*args[2]);
    out.mkBool(close(a, b, relative, absolute));
}

Int checkedMultiply(Call & call, Int a, Int b)
{
    Int result;
    if (__builtin_mul_overflow(a, b, &result)) call.fail("signed 64-bit integer overflow");
    return result;
}

uint64_t magnitude(Int x)
{
    // Negating INT64_MIN as a signed integer would be undefined behavior.
    return x < 0 ? uint64_t{0} - static_cast<uint64_t>(x) : static_cast<uint64_t>(x);
}

void gcd(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    auto a = magnitude(call.integer(*args[0]));
    auto b = magnitude(call.integer(*args[1]));
    auto result = std::gcd(a, b);
    if (result > static_cast<uint64_t>(maxInt)) call.fail("gcd result exceeds signed 64-bit range");
    out.mkInt(static_cast<Int>(result));
}

void lcm(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    auto a = magnitude(call.integer(*args[0]));
    auto b = magnitude(call.integer(*args[1]));
    if (a == 0 || b == 0) {
        out.mkInt(0);
        return;
    }
    auto reduced = a / std::gcd(a, b);
    if (reduced > static_cast<uint64_t>(maxInt) / b) call.fail("lcm result exceeds signed 64-bit range");
    out.mkInt(static_cast<Int>(reduced * b));
}

void factorial(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    Int n = call.natural(*args[0]);
    if (n > 20) call.fail("factorial overflows signed 64-bit integers for n > 20");
    Int result = 1;
    for (Int i = 2; i <= n; ++i) result *= i;
    out.mkInt(result);
}

void comb(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    Int n = call.natural(*args[0]);
    Int k = call.natural(*args[1]);
    if (k > n) {
        out.mkInt(0);
        return;
    }
    k = std::min(k, n - k);
    Int result = 1;
    for (Int i = 1; i <= k; ++i) {
        Int numerator = n - k + i;
        Int denominator = i;
        Int factor = std::gcd(numerator, denominator);
        numerator /= factor;
        denominator /= factor;
        // The remaining denominator divides the previous binomial coefficient.
        result = checkedMultiply(call, result / denominator, numerator);
    }
    out.mkInt(result);
}

void perm(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    Int n = call.natural(*args[0]);
    Int k = call.natural(*args[1]);
    if (k > n) {
        out.mkInt(0);
        return;
    }
    Int result = 1;
    for (Int i = 0; i < k; ++i) result = checkedMultiply(call, result, n - i);
    out.mkInt(result);
}

void isqrt(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    Int n = call.natural(*args[0]);
    auto root = static_cast<Int>(std::sqrt(static_cast<double>(n)));
    while (root > 0 && root > n / root) --root;
    while (root + 1 <= n / (root + 1)) ++root;
    out.mkInt(root);
}

void powInt(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    Int base = call.integer(*args[0]);
    Int exponent = call.natural(*args[1]);
    Int result = 1;
    while (exponent) {
        if (exponent & 1) result = checkedMultiply(call, result, base);
        exponent >>= 1;
        if (exponent) base = checkedMultiply(call, base, base);
    }
    out.mkInt(result);
}

void mod(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    Int x = call.integer(*args[0]);
    Int modulus = call.integer(*args[1]);
    if (modulus <= 0) call.fail("mod requires a positive modulus");
    Int result = x % modulus;
    out.mkInt(result < 0 ? result + modulus : result);
}

// Work on unsigned words, then reinterpret the result as Nix's signed integer.
// In particular, left shifts must not use signed arithmetic or overflow checks.
template<uint64_t (*Function)(uint64_t)>
void wordUnary(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    auto x = static_cast<uint64_t>(call.integer(*args[0]));
    out.mkInt(std::bit_cast<Int>(Function(x)));
}

template<uint64_t (*Function)(uint64_t, uint64_t)>
void wordBinary(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    auto a = static_cast<uint64_t>(call.integer(*args[0]));
    auto b = static_cast<uint64_t>(call.integer(*args[1]));
    out.mkInt(std::bit_cast<Int>(Function(a, b)));
}

template<uint64_t (*Function)(uint64_t, unsigned)>
void wordIndexed(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    auto x = static_cast<uint64_t>(call.integer(*args[0]));
    unsigned index = call.bitIndex(*args[1]);
    out.mkInt(std::bit_cast<Int>(Function(x, index)));
}

uint64_t bitAnd(uint64_t a, uint64_t b) { return a & b; }
uint64_t bitOr(uint64_t a, uint64_t b) { return a | b; }
uint64_t bitXor(uint64_t a, uint64_t b) { return a ^ b; }
uint64_t bitNot(uint64_t x) { return ~x; }
uint64_t shiftLeft(uint64_t x, unsigned count) { return x << count; }
uint64_t shiftRight(uint64_t x, unsigned count) { return x >> count; }
uint64_t shiftRightArithmetic(uint64_t x, unsigned count)
{
    // Since C++20, signed right shift is defined to sign-extend.
    return std::bit_cast<uint64_t>(std::bit_cast<Int>(x) >> count);
}
uint64_t rotateLeft(uint64_t x, unsigned count) { return std::rotl(x, static_cast<int>(count)); }
uint64_t rotateRight(uint64_t x, unsigned count) { return std::rotr(x, static_cast<int>(count)); }
uint64_t popCount(uint64_t x) { return static_cast<uint64_t>(std::popcount(x)); }
uint64_t countLeadingZeros(uint64_t x) { return static_cast<uint64_t>(std::countl_zero(x)); }
uint64_t countTrailingZeros(uint64_t x) { return static_cast<uint64_t>(std::countr_zero(x)); }
uint64_t byteSwap(uint64_t x) { return std::byteswap(x); }
uint64_t setBit(uint64_t x, unsigned index) { return x | (uint64_t{1} << index); }
uint64_t clearBit(uint64_t x, unsigned index) { return x & ~(uint64_t{1} << index); }
uint64_t toggleBit(uint64_t x, unsigned index) { return x ^ (uint64_t{1} << index); }

void testBit(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    auto x = static_cast<uint64_t>(call.integer(*args[0]));
    unsigned index = call.bitIndex(*args[1]);
    out.mkBool((x & (uint64_t{1} << index)) != 0);
}

// Neumaier compensated summation, widened to avoid double intermediate overflow.
struct Accumulator {
    long double total = 0;
    long double correction = 0;

    void add(long double x)
    {
        long double next = total + x;
        correction += std::fabs(total) >= std::fabs(x) ? (total - next) + x : (x - next) + total;
        total = next;
    }

    long double value() const { return total + correction; }
};

template<bool Mean>
void sum(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    call.list(*args[0]);
    auto size = args[0]->listSize();
    if (Mean && size == 0) call.fail("mean requires a nonempty list");
    Accumulator result;
    for (auto value : args[0]->listView()) result.add(call.number(*value));
    call.result(static_cast<double>(Mean ? result.value() / size : result.value()), out);
}

void product(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    call.list(*args[0]);
    double result = 1;
    for (auto value : args[0]->listView()) {
        result *= call.number(*value);
        if (!std::isfinite(result)) call.fail("product overflow");
    }
    call.result(result, out);
}

void norm(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    call.list(*args[0]);
    double result = 0;
    for (auto value : args[0]->listView()) result = std::hypot(result, call.number(*value));
    call.result(result, out);
}

void dot(EvalState & state, PosIdx pos, Value ** args, Value & out)
{
    Call call{state, pos};
    call.list(*args[0]);
    call.list(*args[1]);
    if (args[0]->listSize() != args[1]->listSize()) call.fail("dot requires lists of equal length");
    Accumulator result;
    auto a = args[0]->listView();
    auto b = args[1]->listView();
    for (size_t i = 0; i < a.size(); ++i) {
        double x = call.number(*a[i]);
        double y = call.number(*b[i]);
        result.add(static_cast<long double>(x) * y);
    }
    call.result(static_cast<double>(result.value()), out);
}

// These descriptors have plugin lifetime; values may retain their pointers.
#define UNARY(label, function, docstring) \
    {.name = "math." label, .args = {"x"}, .arity = 1, .doc = docstring, \
     .impl = unary<function>, .experimentalFeature = std::nullopt}
#define BINARY(label, function, docstring) \
    {.name = "math." label, .args = {"a", "b"}, .arity = 2, .doc = docstring, \
     .impl = binary<function>, .experimentalFeature = std::nullopt}
#define TERNARY(label, function, docstring) \
    {.name = "math." label, .args = {"a", "b", "c"}, .arity = 3, .doc = docstring, \
     .impl = ternary<function>, .experimentalFeature = std::nullopt}
#define CUSTOM(label, function, count, docstring) \
    {.name = "math." label, .args = {}, .arity = count, .doc = docstring, \
     .impl = function, .experimentalFeature = std::nullopt}

PrimOp functions[] = {
    BINARY("add", add, "Floating-point addition: add a b."),
    BINARY("sub", sub, "Floating-point subtraction: sub a b."),
    BINARY("mul", mul, "Floating-point multiplication: mul a b."),
    BINARY("div", divide, "Floating-point division, even for integer arguments: div a b."),
    CUSTOM("abs", absolute, 1, "Absolute value; preserve integer inputs and check integer overflow."),
    CUSTOM("sign", sign, 1, "Return -1, 0, or 1 as an integer."),
    BINARY("min", minimum, "Return the smaller argument as a float."),
    BINARY("max", maximum, "Return the larger argument as a float."),
    CUSTOM("clamp", clamp, 3, "clamp lower upper x; require lower <= upper."),
    TERNARY("lerp", std::lerp, "lerp a b t: linear interpolation/extrapolation."),
    TERNARY("fma", std::fma, "Fused multiply-add, a*b+c, with one rounding."),
    UNARY("sqrt", std::sqrt, "Nonnegative square root."),
    UNARY("cbrt", std::cbrt, "Real cube root, including negative arguments."),
    BINARY("pow", std::pow, "pow base exponent; finite real results only."),
    BINARY("hypot", std::hypot, "sqrt(a*a+b*b), avoiding unnecessary intermediate overflow."),
    UNARY("exp", std::exp, "e raised to x."),
    UNARY("exp2", std::exp2, "2 raised to x."),
    UNARY("expm1", std::expm1, "exp(x)-1, accurate near zero."),
    UNARY("log", std::log, "Natural logarithm; x > 0."),
    UNARY("log2", std::log2, "Base-2 logarithm; x > 0."),
    UNARY("log10", std::log10, "Base-10 logarithm; x > 0."),
    UNARY("log1p", std::log1p, "log(1+x), accurate near zero; x > -1."),
    CUSTOM("logBase", logBase, 2, "logBase base x; base > 0, base != 1, x > 0."),
    UNARY("sin", std::sin, "Sine; angle in radians."),
    UNARY("cos", std::cos, "Cosine; angle in radians."),
    UNARY("tan", std::tan, "Tangent; angle in radians."),
    UNARY("asin", std::asin, "Inverse sine in radians; -1 <= x <= 1."),
    UNARY("acos", std::acos, "Inverse cosine in radians; -1 <= x <= 1."),
    UNARY("atan", std::atan, "Inverse tangent in radians."),
    BINARY("atan2", std::atan2, "atan2 y x: quadrant-aware inverse tangent in radians."),
    UNARY("sinh", std::sinh, "Hyperbolic sine."),
    UNARY("cosh", std::cosh, "Hyperbolic cosine."),
    UNARY("tanh", std::tanh, "Hyperbolic tangent."),
    UNARY("asinh", std::asinh, "Inverse hyperbolic sine."),
    UNARY("acosh", std::acosh, "Inverse hyperbolic cosine; x >= 1."),
    UNARY("atanh", std::atanh, "Inverse hyperbolic tangent; -1 < x < 1."),
    UNARY("radians", radians, "Convert degrees to radians."),
    UNARY("degrees", degrees, "Convert radians to degrees."),
    CUSTOM("ceil", rounding<std::ceil>, 1, "Round toward positive infinity; return a checked integer."),
    CUSTOM("floor", rounding<std::floor>, 1, "Round toward negative infinity; return a checked integer."),
    CUSTOM("trunc", rounding<std::trunc>, 1, "Round toward zero; return a checked integer."),
    CUSTOM("round", rounding<std::round>, 1, "Round to nearest, ties away from zero; return a checked integer."),
    BINARY("fmod", std::fmod, "Truncated-quotient remainder; sign follows the dividend."),
    BINARY("remainder", std::remainder, "IEEE remainder using the nearest integer quotient, ties to even."),
    BINARY("copysign", std::copysign, "Magnitude of a with the sign of b, including signed zero."),
    BINARY("nextAfter", std::nextafter, "Next representable float from a toward b."),
    BINARY("fdim", std::fdim, "Positive difference: max(a-b, 0)."),
    CUSTOM("frexp", frexp, 1, "Decompose x as { fraction; exponent; }, x = fraction * 2^exponent."),
    CUSTOM("ldexp", ldexp, 2, "ldexp x exponent: x * 2^exponent; exponent must be an integer."),
    CUSTOM("modf", modf, 1, "Split x into floating-point { integral; fraction; }, both with x's sign."),
    UNARY("erf", std::erf, "Error function."),
    UNARY("erfc", std::erfc, "Complementary error function, accurate in the positive tail."),
    UNARY("gamma", std::tgamma, "Gamma function, with poles and overflow rejected."),
    UNARY("lgamma", std::lgamma, "Natural logarithm of the absolute gamma function."),
    CUSTOM("isClose", isClose, 2, "isClose a b; relative tolerance 1e-9, absolute tolerance 0."),
    CUSTOM("isCloseWith", isCloseWith, 3, "isCloseWith { relTol ? 1e-9; absTol ? 0; } a b."),
    CUSTOM("gcd", gcd, 2, "Nonnegative greatest common divisor of two integers; gcd 0 0 = 0."),
    CUSTOM("lcm", lcm, 2, "Nonnegative least common multiple of two integers; zero if either is zero."),
    CUSTOM("factorial", factorial, 1, "Exact n! for integers 0 <= n <= 20."),
    CUSTOM("comb", comb, 2, "Exact binomial coefficient comb n k; zero for k > n."),
    CUSTOM("perm", perm, 2, "Exact ordered selections perm n k = n!/(n-k)!; zero for k > n."),
    CUSTOM("isqrt", isqrt, 1, "Exact floor of the square root of a nonnegative integer."),
    CUSTOM("powInt", powInt, 2, "Checked exact integer exponentiation; exponent must be nonnegative."),
    CUSTOM("mod", mod, 2, "Euclidean integer remainder mod x modulus; modulus > 0."),
    CUSTOM("bitAnd", wordBinary<bitAnd>, 2, "Bitwise AND of two 64-bit integers."),
    CUSTOM("bitOr", wordBinary<bitOr>, 2, "Bitwise OR of two 64-bit integers."),
    CUSTOM("bitXor", wordBinary<bitXor>, 2, "Bitwise XOR of two 64-bit integers."),
    CUSTOM("bitNot", wordUnary<bitNot>, 1, "Complement all 64 bits of an integer."),
    CUSTOM("shiftLeft", wordIndexed<shiftLeft>, 2, "shiftLeft x count: zero-fill left shift, discarding overflow; count in [0, 63]."),
    CUSTOM("shiftRight", wordIndexed<shiftRight>, 2, "shiftRight x count: logical (zero-fill) right shift; count in [0, 63]."),
    CUSTOM("shiftRightArithmetic", wordIndexed<shiftRightArithmetic>, 2, "shiftRightArithmetic x count: sign-extending right shift; count in [0, 63]."),
    CUSTOM("rotateLeft", wordIndexed<rotateLeft>, 2, "rotateLeft x count: rotate a 64-bit word left; count in [0, 63]."),
    CUSTOM("rotateRight", wordIndexed<rotateRight>, 2, "rotateRight x count: rotate a 64-bit word right; count in [0, 63]."),
    CUSTOM("popCount", wordUnary<popCount>, 1, "Count set bits in a 64-bit word, including the sign bit."),
    CUSTOM("countLeadingZeros", wordUnary<countLeadingZeros>, 1, "Count leading zero bits; zero has 64."),
    CUSTOM("countTrailingZeros", wordUnary<countTrailingZeros>, 1, "Count trailing zero bits; zero has 64."),
    CUSTOM("byteSwap", wordUnary<byteSwap>, 1, "Reverse all eight bytes of a 64-bit word, independent of host endianness."),
    CUSTOM("testBit", testBit, 2, "testBit x index: return whether bit index is set; index in [0, 63], least significant bit first."),
    CUSTOM("setBit", wordIndexed<setBit>, 2, "setBit x index: set bit index; index in [0, 63]."),
    CUSTOM("clearBit", wordIndexed<clearBit>, 2, "clearBit x index: clear bit index; index in [0, 63]."),
    CUSTOM("toggleBit", wordIndexed<toggleBit>, 2, "toggleBit x index: invert bit index; index in [0, 63]."),
    CUSTOM("sum", sum<false>, 1, "Compensated floating-point sum of a list; sum [] = 0.0."),
    CUSTOM("product", product, 1, "Floating-point product of a list; product [] = 1.0."),
    CUSTOM("mean", sum<true>, 1, "Arithmetic mean of a nonempty list, avoiding intermediate sum overflow."),
    CUSTOM("norm", norm, 1, "Euclidean norm of a list; norm [] = 0.0."),
    CUSTOM("dot", dot, 2, "Compensated dot product of equally sized lists; dot [] [] = 0.0."),
};

#undef UNARY
#undef BINARY
#undef TERNARY
#undef CUSTOM

RegisterPrimOp math({
    .name = "__math",
    .args = {},
    .arity = 0,
    .doc = "mathnix's pure real-valued mathematics library. All functions reject non-finite results.",
    .impl = [](EvalState & state, PosIdx, Value **, Value & out) {
        auto attrs = state.buildBindings(std::size(functions) + 13);
        for (auto & function : functions)
            attrs.alloc(function.name.substr(5)).mkPrimOp(&function);
        attrs.alloc("pi").mkFloat(std::numbers::pi);
        attrs.alloc("tau").mkFloat(2 * std::numbers::pi);
        attrs.alloc("e").mkFloat(std::numbers::e);
        attrs.alloc("phi").mkFloat(std::numbers::phi);
        attrs.alloc("sqrt2").mkFloat(std::numbers::sqrt2);
        attrs.alloc("ln2").mkFloat(std::numbers::ln2);
        attrs.alloc("ln10").mkFloat(std::numbers::ln10);
        attrs.alloc("epsilon").mkFloat(std::numeric_limits<double>::epsilon());
        attrs.alloc("minFloat").mkFloat(std::numeric_limits<double>::min());
        attrs.alloc("maxFloat").mkFloat(std::numeric_limits<double>::max());
        attrs.alloc("minInt").mkInt(std::numeric_limits<Int>::min());
        attrs.alloc("maxInt").mkInt(maxInt);
        attrs.alloc("version").mkString("0.1.0", state.mem);
        out.mkAttrs(attrs);
    },
    .experimentalFeature = std::nullopt,
});

} // namespace
