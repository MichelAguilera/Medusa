"""The medusactl<->controller wire contract (T-099).

One global integer covering every ``--json`` payload the medusa CLI emits.
Bump it on ANY breaking change to any payload shape. medusactl probes
``medusa contract-version`` once per run and refuses on mismatch -- it
never infers a payload shape from the binary in front of it (T-095).
"""

CONTRACT_VERSION = 2
