# Indicator references

These small reference cases are hand-derived from the definitions in
`docs/INDICATORS.md`; they are not downloaded provider data or outputs generated
by the production indicator functions. Fractions denote exact rational answers;
tests compare their Decimal expansions with a stated tolerance.

Wilder RSI(2), closes 100,102,101,104,100,100:
initial gain/loss averages are 1 and 1/2; the following pairs are 2 and 1/4,
then 1 and 17/8, then 1/2 and 17/16. RSI = 100 * gain / (gain + loss).
For the RSI(14) case, initial total gains are 3.34 and losses 1.40; the next
Wilder update uses a loss of 0.28. No intermediate averages are rounded to cents.

MACD(2,3,2), closes 1,2,4,3,6,5:
fast EMA starts at 3/2, slow at 7/3. The fast EMA at close 4 is 19/6, giving
the first line value 5/6. At close 3, fast=55/18 and slow=8/3, giving 7/18;
the first signal is their two-value line mean 11/18. Remaining fractions follow
EMA alpha=2/(period+1), with each EMA independently SMA-seeded. This definition
is intentionally explicit; libraries with differently seeded EMAs can differ.

The volatility test has an independent closed-form check: closes 1,2,2,4 give
log returns ln(2),0,ln(2). Each two-return sample has standard deviation
ln(2)/sqrt(2), using sample denominator n-1. Constant geometric growth has
constant log returns and zero *centered* sample volatility, despite nonzero drift.
