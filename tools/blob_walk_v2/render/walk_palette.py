"""Shared overlay palette access for blob_walk_v2 renderers.

Both walk_render.py (PNG-baked vector overlays) and walk_html.py (trajectory
PNGs and HTML legend swatches) read overlay colors from the same source of
truth: track_runner/overlay_styles.yaml. Each module used to carry its own
yaml loader and walk_render additionally reimplemented hex->BGR conversion.
This module is the single owner of both: the walk_overlays sub-palette loader
and the walker_overlays BGR lookup. Color values and lookup behavior are
unchanged; this only removes the duplicate loaders.

The track_runner overlay_config module is the upstream owner of the yaml
parse, cache, hex->BGR conversion, and palette validation. walk_palette
consumes those public APIs rather than re-reading the yaml. Callers must have
track_runner on sys.path before importing this module (walk_render does this
via walk_paths.setup(); walk_html relies on its launchers, matching the
established blob_walk_v2 import pattern).
"""

# local repo modules
import overlay_config

# Closed set of layer names the renderer knows.
# source_frame is the implicit canvas base and is NOT in this set.
_KNOWN_LAYERS = (
	"heat",
	"seed_box",
	"solved_box",
	"blob_ellipses",
	"plus_marker",
	"acceptance_box",
	"audit_square",
	"residual_line",
	"allowed_jump_circle",
	"velocity_vector",
)

# Built-in default order (heat on top).
_DEFAULT_LAYER_ORDER = (
	"seed_box",
	"solved_box",
	"blob_ellipses",
	"plus_marker",
	"acceptance_box",
	"audit_square",
	"residual_line",
	"allowed_jump_circle",
	"velocity_vector",
	"heat",
)


#============================================
def resolve_layer_order() -> tuple:
	"""Return the tile draw order as a tuple of layer-name strings.

	Reads walk_tile_layer_order from track_runner/overlay_styles.yaml via
	overlay_config.load_palette().  Falls back to the built-in default order
	when the key is absent (backward-compatible).  Raises loudly on unknown
	layer names, duplicate names, or omitted known layers.

	Returns:
		tuple: Ordered layer names, first entry drawn first (bottom).

	Raises:
		ValueError: If the YAML list contains unknown names, duplicates,
			or omits any known layer name.
	"""
	palette = overlay_config.load_palette()
	# Missing key -> fall back to default (backward compatible)
	if "walk_tile_layer_order" not in palette:
		return _DEFAULT_LAYER_ORDER
	yaml_order = list(palette["walk_tile_layer_order"])
	known_set = set(_KNOWN_LAYERS)
	yaml_names = [str(n) for n in yaml_order]
	# Check for unknown names
	unknown = [n for n in yaml_names if n not in known_set]
	if unknown:
		raise ValueError(
			f"walk_tile_layer_order: unknown layer name(s): {unknown}. "
			f"Allowed names: {list(_KNOWN_LAYERS)}"
		)
	# Check for duplicates
	seen = set()
	duplicates = []
	for n in yaml_names:
		if n in seen:
			duplicates.append(n)
		seen.add(n)
	if duplicates:
		raise ValueError(
			f"walk_tile_layer_order: duplicate layer name(s): {duplicates}."
		)
	# Check for omitted known layers
	omitted = [n for n in _KNOWN_LAYERS if n not in seen]
	if omitted:
		raise ValueError(
			f"walk_tile_layer_order: omitted known layer name(s): {omitted}. "
			f"All known layers must appear exactly once."
		)
	return tuple(yaml_names)


#============================================
def load_walk_overlays() -> dict:
	"""Load the walk_overlays sub-palette from track_runner overlay styles.

	The walk_overlays block holds the per-tile vector-overlay colors
	(velocity vector, allowed-jump circle, residual accept/reject) and the
	trajectory/seed-marker colors used by the HTML view. Values are #RRGGBB
	hex strings.

	Returns:
		dict: the walk_overlays mapping of color-key -> hex string.
	"""
	# overlay_config.load_palette caches the parsed yaml; _merge_defaults
	# touches only seed_status/predictions, never walk_overlays, so this dict
	# is identical to a raw yaml.safe_load(...)['walk_overlays'].
	palette = overlay_config.load_palette()
	walk_overlays = palette["walk_overlays"]
	return walk_overlays


#============================================
def get_walker_overlay_color_bgr(color_key: str) -> tuple:
	"""Retrieve a walker overlay color as a BGR tuple.

	Looks up color_key in the walker_overlays sub-palette (blob ellipse,
	prior cross, acceptance box, audit-disagreement marker colors) and
	converts the hex string to a BGR tuple for OpenCV.

	Args:
		color_key: Key name in walker_overlays (e.g. 'winner',
			'corridor_non_winner', 'prior_cross').

	Returns:
		tuple: BGR color as (B, G, R) integers.

	Raises:
		KeyError: If the key is not in walker_overlays.
	"""
	palette = overlay_config.load_palette()
	walker_palette = palette["walker_overlays"]
	hex_color = walker_palette[color_key]
	bgr = overlay_config.hex_to_bgr(hex_color)
	return bgr
