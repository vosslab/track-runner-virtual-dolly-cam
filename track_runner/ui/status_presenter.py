"""Status presenter for seed editor annotation display.

Shows seed status information including frame index, time, confidence,
and status color in the annotation toolbar.
"""

# Standard Library
# (none)

# PIP3 modules
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

# local repo modules
import overlay_config

# Neutral grey used for pre-race badge and reason-text spans.
PRE_RACE_COLOR = "#94A3B8"

#============================================

class StatusPresenter:
	"""Displays seed status information in the annotation toolbar.

	Updates status label with seed index, frame information, and confidence
	scores. Color-codes status for quick visual feedback.
	"""

	def __init__(self) -> None:
		"""Initialize the StatusPresenter.

		Creates a monospace QLabel with styling for status display.
		"""
		mono_family = overlay_config.get_mono_font_family()
		self._label = QLabel("")
		self._label.setStyleSheet(
			f"font-family: '{mono_family}'; "
			"font-size: 11px; "
			"padding: 4px; "
		)

	#============================================

	def get_widget(self) -> QLabel:
		"""Get the status label widget.

		Returns:
			The QLabel widget for display in the toolbar.
		"""
		return self._label

	#============================================

	def update(
		self,
		seed: dict,
		seed_index: int,
		total_seeds: int,
		fps: float,
		confidence: dict | None = None,
		interval_info: dict | None = None,
	) -> None:
		"""Update the status label with seed information.

		Args:
			seed: Seed dict with frame_index and status keys.
			seed_index: 0-based index in the filtered list.
			total_seeds: Total number of seeds being reviewed.
			fps: Video frame rate, used to compute time_s for display.
			confidence: Optional dict with 'score' and 'label' keys.
			interval_info: Optional dict with severity, agreement, margin,
				and reasons keys from prediction diagnostics. May also carry an
				in-memory commitment review item.
		"""
		frame_index = int(seed.get("frame_index", 0))
		status = seed.get("status", "unknown")
		# time_s derived from frame_index / fps; not a stored seed field
		time_s = float(frame_index) / fps if fps > 0 else 0.0

		# primary info line
		text = (
			f"Seed {seed_index + 1}/{total_seeds}  "
			f"frame {frame_index}  "
			f"{time_s:.1f}s  "
			f"{status}"
		)

		if confidence is not None:
			score = float(confidence.get("score", 0.0))
			text += f"  conf {score:.2f}"

		# severity badge with color from overlay_styles.yaml
		severity_html = ""
		if interval_info is not None:
			# Pre-race intervals are not severity-classified per contract C4;
			# severity is None in that case (key is present, value is None).
			raw_severity = interval_info["severity"]
			if raw_severity is None:
				sev_color = PRE_RACE_COLOR
				sev_label = "PRE-RACE"
			else:
				sev_style = overlay_config.get_severity_style(raw_severity.lower())
				sev_color = sev_style["color"]
				sev_label = sev_style["label"]
			severity_html = f"  <span style='color: {sev_color};'>[{sev_label}]</span>"
			# compact reason text
			reason_parts = self._format_reasons(interval_info)
			if reason_parts:
				reason_text = ", ".join(reason_parts)
				severity_html += (
					f" <span style='color: {PRE_RACE_COLOR};'>({reason_text})</span>"
				)

		# use rich text if severity info is present
		if severity_html:
			# wrap the primary text as HTML and append severity
			status_color = self._get_status_color(status)
			html = (
				f"<span style='color: {status_color}; font-weight: bold;'>"
				f"{text}</span>{severity_html}"
			)
			self._label.setText(html)
			self._label.setTextFormat(Qt.TextFormat.RichText)
		else:
			self._label.setText(text)
			self._label.setTextFormat(Qt.TextFormat.PlainText)

		# Apply color based on status
		mono_family = overlay_config.get_mono_font_family()
		color = self._get_status_color(status)
		stylesheet = (
			f"font-family: '{mono_family}'; color: {color}; "
			"font-weight: bold; padding: 4px;"
		)
		self._label.setStyleSheet(stylesheet)

	#============================================

	def show_feedback(self, text: str) -> None:
		"""Show an annotation action result in the persistent status area.

		This is deliberately separate from :meth:`update`: feedback is not
		seed metadata, and the next navigation update is allowed to replace it
		with the normal seed summary.

		Args:
			text: Human-readable result or warning from an annotation action.
		"""
		self._label.setText(text)
		self._label.setTextFormat(Qt.TextFormat.PlainText)
		mono_family = overlay_config.get_mono_font_family()
		self._label.setStyleSheet(
			f"font-family: '{mono_family}'; color: #F8FAFC; "
			"font-weight: bold; padding: 4px;"
		)

	#============================================

	def _get_status_color(self, status: str) -> str:
		"""Map status to a display color from overlay_styles.yaml.

		Args:
			status: Status string from the seed.

		Returns:
			Hex color string for the status.
		"""
		color = overlay_config.get_seed_status_color(status)
		return color

	#============================================

	def _format_reasons(self, interval_info: dict) -> list:
		"""Format interval_info reasons as compact human-readable labels.

		Args:
			interval_info: Dict with agreement, velocity_consistency, and reasons keys.

		Returns:
			List of short reason strings.
		"""
		parts = []
		agreement = interval_info.get("agreement", 1.0)
		velocity_consistency = interval_info.get("velocity_consistency", 1.0)
		# agreement level
		if agreement < 0.2:
			parts.append("low agree")
		elif agreement < 0.4:
			parts.append("mod agree")
		if velocity_consistency < 0.2:
			parts.append("motion weak")
		# Review owns the commitment wording.  The GUI only presents its
		# already-human-readable item, so it cannot drift from other review paths.
		commitment_item = interval_info.get("commitment_review_item")
		if commitment_item is not None:
			parts.append(commitment_item)
		return parts

	#============================================

	def clear(self) -> None:
		"""Clear the status label and reset styling."""
		self._label.setText("")
		self._label.setStyleSheet("padding: 4px;")
