# Pair-local interpolation decision

Date: 2026-08-21

Human torso boxes provide position-and-size anchors. They do not provide a
measured velocity or size derivative. The earlier shared and local
seed-derivative experiments are retired.

Each interval now converts only its two endpoint seed boxes to scene space.
The endpoint chord supplies both Hermite derivatives, yielding linear center
interpolation and log-linear size interpolation. No neighboring seed can alter
an interval result without changing one of its fingerprinted endpoints.

FWD and BWD retain independent confidence anchors and independent image-based
walker evidence. Pair-local fallback geometry is deliberately simple and does
not invent motion information from annotations.
