let
  m = builtins.math;
in {
  circleArea = m.pi * m.pow 3 2;
  sine45 = m.sin (m.radians 45);
  logarithm = m.logBase 2 1024;
  integerPower = m.powInt 2 60;
  combinations = m.comb 52 5;
  vectorLength = m.norm [ 3 4 12 ];
  dotProduct = m.dot [ 1 2 3 ] [ 4 5 6 ];
  accurateSum = m.sum [ 1.0e16 1 (-1.0e16) ];
  decomposition = m.frexp 12;
  approximatelyEqual = m.isClose (m.sin m.pi) 0; # false without an absolute tolerance
  nearZero = m.isCloseWith { absTol = 1.0e-15; } (m.sin m.pi) 0;
}
