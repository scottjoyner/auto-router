"""Research-only LFM3 evidence gates. NEVER imported by routing or provider admission.

This package is never shipped by setuptools (which only packages src/).
All modules in this package are advisory pure functions. They do not start
inference, create provider eligibility, authorize signed admission, set routes,
or verify cryptographic signatures without an external authoritative verifier.
"""

RESEARCH_ONLY = True
CREATES_PROVIDER_ELIGIBILITY = False
REQUIRES_SIGNED_ADMISSION = True
