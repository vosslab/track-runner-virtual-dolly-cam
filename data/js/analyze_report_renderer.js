/* analyze_report_renderer.js
 *
 * Vanilla-JS renderer for the encode-analysis HTML report.
 *
 * Patch 2 stub. The real renderer lands in Patch 6 of the
 * analyze-mode HTML report plan
 * (~/.claude/plans/groovy-singing-neumann.md and the in-repo
 * docs/active_plans/ANALYZE_MODE_PLOTS.md).
 *
 * --- Embedded JSON schema (consumed by the renderer) ---
 *
 * The Python writer emits a single <script type="application/json"
 * id="encode-analysis-data"> block whose contents parse to:
 *
 * {
 *   "video_stem": string,
 *   "fps":        number | null,
 *   "frames":     array of integer frame indices,
 *   "panels":     array of panel objects (see below),
 *   "warnings":   array of strings (rendered verbatim, already escaped
 *                 in the document body)
 * }
 *
 * Each panel object has:
 *
 * {
 *   "id":               string matching a <canvas id="..."> element
 *                       (one of: "zoom-stability", "zoom-multiple",
 *                        "camera-motion", "runner-speed"),
 *   "title":            string,
 *   "y_left_label":     string,
 *   "y_right_label":    string | null,
 *   "default_visible":  array of series.name strings,
 *   "reference_lines":  optional array of {axis, value, label},
 *   "series":           array of {
 *                         "name":   string,
 *                         "axis":   "left" | "right",
 *                         "values": array of number|null
 *                                   (same length as top-level frames)
 *                       }
 * }
 *
 * --- Renderer interaction set (Patch 6) ---
 *
 *  - hover -> tooltip with frame, seconds (when fps known), and the
 *    value of every visible series at that frame
 *  - drag-zoom on x-range, synced across all panels
 *  - double-click resets the x-range across all panels
 *  - per-panel checkbox row toggles series visibility
 *  - null in a series.values renders as a gap (no line through null)
 *
 * Out of scope: lasso select, annotations, export, themes, Y-zoom,
 * panning, per-panel config UI.
 */

(function () {
	"use strict";

	// Parse the embedded JSON payload. The script tag is required; if
	// missing, abort silently (dev-mode pages without data should not
	// throw uncaught exceptions on page load).
	var dataNode = document.getElementById("encode-analysis-data");
	if (dataNode === null) { return; }
	var data = JSON.parse(dataNode.textContent);

	var FPS = data.fps;
	var FRAMES = data.frames || [];
	var N_FRAMES = FRAMES.length;

	// Matplotlib-default-style palette indexed by series order.
	var SERIES_COLORS = [
		"#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
	];

	// Plot margins inside the canvas: left for the y-axis label and
	// ticks, right for the optional twin axis, top for breathing room,
	// bottom for the x-axis ticks.
	var MARGIN_LEFT = 60;
	var MARGIN_RIGHT = 60;
	var MARGIN_TOP = 12;
	var MARGIN_BOTTOM = 28;

	// Global x-range state, synced across all panels.
	var xRangeStart = 0;
	var xRangeEnd = N_FRAMES;

	// Per-panel runtime state. One entry per <canvas> we successfully
	// initialize; populated by buildPanel.
	var panelStates = [];

	// Shared tooltip element appended to body.
	var tooltip = document.createElement("div");
	tooltip.className = "tooltip";
	tooltip.style.display = "none";
	document.body.appendChild(tooltip);

	//============================================
	// Number formatting helpers.
	//============================================

	function formatNumber(v) {
		if (v === null || v === undefined) { return "-"; }
		if (!isFinite(v)) { return "-"; }
		var abs = Math.abs(v);
		if (abs !== 0 && (abs < 0.01 || abs >= 1e6)) {
			return v.toExponential(2);
		}
		// Three significant digits for values >= 1, three decimals for small.
		if (abs >= 100) { return v.toFixed(0); }
		if (abs >= 10) { return v.toFixed(1); }
		if (abs >= 1) { return v.toFixed(2); }
		return v.toFixed(3);
	}

	function formatFrame(i) {
		return "frame " + i;
	}

	function formatSeconds(i) {
		if (FPS === null || FPS === undefined || FPS <= 0) { return null; }
		var s = i / FPS;
		return s.toFixed(2) + " s";
	}

	//============================================
	// Per-axis range computation. Returns {min, max} that respect the
	// current xRange and visibility mask, or {min:0, max:1} when no
	// finite values are visible (avoids div-by-zero in scaling).
	//============================================

	function computeAxisRange(panel, axisName, visibility) {
		var min = Infinity;
		var max = -Infinity;
		for (var i = 0; i < panel.series.length; i++) {
			var series = panel.series[i];
			if (series.axis !== axisName) { continue; }
			if (!visibility[series.name]) { continue; }
			var values = series.values;
			for (var k = xRangeStart; k < xRangeEnd && k < values.length; k++) {
				var v = values[k];
				if (v === null || v === undefined) { continue; }
				if (!isFinite(v)) { continue; }
				if (v < min) { min = v; }
				if (v > max) { max = v; }
			}
		}
		// Reference lines must also sit inside the y-range.
		if (panel.reference_lines) {
			for (var j = 0; j < panel.reference_lines.length; j++) {
				var ref = panel.reference_lines[j];
				if (ref.axis !== axisName) { continue; }
				if (ref.value < min) { min = ref.value; }
				if (ref.value > max) { max = ref.value; }
			}
		}
		if (!isFinite(min) || !isFinite(max)) {
			return { min: 0, max: 1 };
		}
		if (min === max) {
			// Pad so the line is not on the very edge.
			var pad = Math.abs(min) * 0.1 + 1.0;
			return { min: min - pad, max: max + pad };
		}
		// 5% headroom top/bottom for readability.
		var span = max - min;
		return { min: min - span * 0.05, max: max + span * 0.05 };
	}

	//============================================
	// Coordinate transforms.
	//============================================

	function xToPixel(state, frameIdx) {
		var w = state.cssW;
		var plotW = w - MARGIN_LEFT - MARGIN_RIGHT;
		var span = xRangeEnd - xRangeStart;
		if (span <= 0) { return MARGIN_LEFT; }
		var t = (frameIdx - xRangeStart) / span;
		return MARGIN_LEFT + t * plotW;
	}

	function pixelToFrame(state, px) {
		var w = state.cssW;
		var plotW = w - MARGIN_LEFT - MARGIN_RIGHT;
		if (plotW <= 0) { return xRangeStart; }
		var t = (px - MARGIN_LEFT) / plotW;
		var span = xRangeEnd - xRangeStart;
		var frame = xRangeStart + t * span;
		return Math.max(xRangeStart, Math.min(xRangeEnd - 1, Math.round(frame)));
	}

	function yToPixel(state, axisName, value) {
		var h = state.cssH;
		var plotH = h - MARGIN_TOP - MARGIN_BOTTOM;
		var range = (axisName === "right") ? state.rangeRight : state.rangeLeft;
		var span = range.max - range.min;
		if (span <= 0) { return MARGIN_TOP + plotH / 2; }
		var t = (value - range.min) / span;
		// Y axis inverted: max at top.
		return MARGIN_TOP + (1 - t) * plotH;
	}

	//============================================
	// Drawing helpers.
	//============================================

	function clearCanvas(state) {
		var ctx = state.ctx;
		var w = state.cssW;
		var h = state.cssH;
		ctx.clearRect(0, 0, w, h);
		ctx.fillStyle = "#ffffff";
		ctx.fillRect(0, 0, w, h);
	}

	function drawAxes(state) {
		var ctx = state.ctx;
		var w = state.cssW;
		var h = state.cssH;
		var plotW = w - MARGIN_LEFT - MARGIN_RIGHT;
		var plotH = h - MARGIN_TOP - MARGIN_BOTTOM;

		ctx.strokeStyle = "#888";
		ctx.lineWidth = 1;
		ctx.fillStyle = "#444";
		ctx.font = "10px sans-serif";

		// Plot box.
		ctx.beginPath();
		ctx.rect(MARGIN_LEFT, MARGIN_TOP, plotW, plotH);
		ctx.stroke();

		// X-axis ticks: roughly 6 labels across the visible range.
		var span = xRangeEnd - xRangeStart;
		if (span > 0) {
			var nTicks = 6;
			ctx.textAlign = "center";
			ctx.textBaseline = "top";
			for (var i = 0; i <= nTicks; i++) {
				var frame = xRangeStart + Math.round(i * span / nTicks);
				if (frame > xRangeEnd) { frame = xRangeEnd; }
				var px = xToPixel(state, frame);
				ctx.beginPath();
				ctx.moveTo(px, MARGIN_TOP + plotH);
				ctx.lineTo(px, MARGIN_TOP + plotH + 4);
				ctx.stroke();
				ctx.fillText("" + frame, px, MARGIN_TOP + plotH + 6);
			}
		}

		// Left y-axis ticks (5 evenly spaced labels).
		drawYAxisTicks(state, "left", state.rangeLeft, MARGIN_LEFT, "right");
		// Right y-axis ticks (only when rangeRight is non-null).
		if (state.rangeRight !== null) {
			drawYAxisTicks(state, "right",
				state.rangeRight, w - MARGIN_RIGHT, "left");
		}

		// Axis labels.
		ctx.save();
		ctx.translate(12, MARGIN_TOP + plotH / 2);
		ctx.rotate(-Math.PI / 2);
		ctx.textAlign = "center";
		ctx.textBaseline = "middle";
		ctx.fillStyle = "#333";
		ctx.fillText(state.panel.y_left_label || "", 0, 0);
		ctx.restore();

		if (state.panel.y_right_label) {
			ctx.save();
			ctx.translate(w - 12, MARGIN_TOP + plotH / 2);
			ctx.rotate(Math.PI / 2);
			ctx.textAlign = "center";
			ctx.textBaseline = "middle";
			ctx.fillStyle = "#333";
			ctx.fillText(state.panel.y_right_label, 0, 0);
			ctx.restore();
		}
	}

	function drawYAxisTicks(state, axisName, range, anchorPx, textAlign) {
		var ctx = state.ctx;
		var h = state.cssH;
		var plotH = h - MARGIN_TOP - MARGIN_BOTTOM;
		var nTicks = 5;
		ctx.textAlign = textAlign;
		ctx.textBaseline = "middle";
		ctx.fillStyle = "#444";
		for (var i = 0; i <= nTicks; i++) {
			var v = range.min + (i * (range.max - range.min)) / nTicks;
			var py = MARGIN_TOP + plotH - (i * plotH) / nTicks;
			ctx.beginPath();
			if (axisName === "left") {
				ctx.moveTo(anchorPx - 4, py);
				ctx.lineTo(anchorPx, py);
				ctx.stroke();
				ctx.fillText(formatNumber(v), anchorPx - 6, py);
			} else {
				ctx.moveTo(anchorPx, py);
				ctx.lineTo(anchorPx + 4, py);
				ctx.stroke();
				ctx.fillText(formatNumber(v), anchorPx + 6, py);
			}
		}
	}

	function drawReferenceLines(state) {
		var refs = state.panel.reference_lines;
		if (!refs) { return; }
		var ctx = state.ctx;
		var w = state.cssW;
		ctx.save();
		ctx.strokeStyle = "#888";
		ctx.lineWidth = 1;
		// Dash pattern for the reference line.
		if (typeof ctx.setLineDash === "function") {
			ctx.setLineDash([6, 4]);
		}
		ctx.fillStyle = "#666";
		ctx.font = "10px sans-serif";
		ctx.textAlign = "right";
		ctx.textBaseline = "bottom";
		for (var i = 0; i < refs.length; i++) {
			var ref = refs[i];
			if (ref.axis === "right" && state.rangeRight === null) { continue; }
			var py = yToPixel(state, ref.axis, ref.value);
			ctx.beginPath();
			ctx.moveTo(MARGIN_LEFT, py);
			ctx.lineTo(w - MARGIN_RIGHT, py);
			ctx.stroke();
			if (ref.label) {
				ctx.fillText(ref.label, w - MARGIN_RIGHT - 4, py - 2);
			}
		}
		ctx.restore();
	}

	function drawSeries(state) {
		var ctx = state.ctx;
		for (var i = 0; i < state.panel.series.length; i++) {
			var series = state.panel.series[i];
			if (!state.visibility[series.name]) { continue; }
			// Skip right-axis series when there is no right axis.
			if (series.axis === "right" && state.rangeRight === null) { continue; }
			var color = SERIES_COLORS[i % SERIES_COLORS.length];
			ctx.strokeStyle = color;
			ctx.lineWidth = 1.5;
			ctx.beginPath();
			var pen = false;
			var values = series.values;
			for (var k = xRangeStart; k < xRangeEnd && k < values.length; k++) {
				var v = values[k];
				if (v === null || v === undefined || !isFinite(v)) {
					// gap: lift the pen
					pen = false;
					continue;
				}
				var px = xToPixel(state, k);
				var py = yToPixel(state, series.axis, v);
				if (!pen) {
					ctx.moveTo(px, py);
					pen = true;
				} else {
					ctx.lineTo(px, py);
				}
			}
			ctx.stroke();
		}
	}

	function drawDragOverlay(state) {
		if (!state.dragActive) { return; }
		var ctx = state.ctx;
		var x0 = state.dragStartPx;
		var x1 = state.dragLastPx;
		var lo = Math.min(x0, x1);
		var hi = Math.max(x0, x1);
		var h = state.cssH;
		ctx.save();
		ctx.fillStyle = "rgba(31, 119, 180, 0.18)";
		ctx.fillRect(lo, MARGIN_TOP, hi - lo, h - MARGIN_TOP - MARGIN_BOTTOM);
		ctx.strokeStyle = "rgba(31, 119, 180, 0.6)";
		ctx.lineWidth = 1;
		ctx.beginPath();
		ctx.moveTo(lo, MARGIN_TOP);
		ctx.lineTo(lo, h - MARGIN_BOTTOM);
		ctx.moveTo(hi, MARGIN_TOP);
		ctx.lineTo(hi, h - MARGIN_BOTTOM);
		ctx.stroke();
		ctx.restore();
	}

	function drawNoData(state) {
		var ctx = state.ctx;
		var w = state.cssW;
		var h = state.cssH;
		ctx.save();
		ctx.fillStyle = "#888";
		ctx.font = "14px sans-serif";
		ctx.textAlign = "center";
		ctx.textBaseline = "middle";
		ctx.fillText("(no data to display)", w / 2, h / 2);
		ctx.restore();
	}

	function drawPanel(state) {
		// HiDPI-aware sizing: backing store gets cssDim * dpr px, but all
		// drawing calls (and downstream xToPixel/yToPixel/event handling)
		// continue to operate in CSS-pixel space because we apply
		// setTransform(dpr, 0, 0, dpr, 0, 0) on every redraw. Reading
		// state.cssW/cssH (not state.canvas.width/height) keeps the
		// coordinate functions in CSS pixels regardless of the device
		// pixel ratio.
		var dpr = window.devicePixelRatio || 1;
		var rect = state.canvas.getBoundingClientRect();
		var cssW = Math.max(1, Math.round(rect.width));
		var cssH = Math.max(1, Math.round(rect.height));
		if (state.canvas.width !== cssW * dpr) { state.canvas.width = cssW * dpr; }
		if (state.canvas.height !== cssH * dpr) { state.canvas.height = cssH * dpr; }
		state.cssW = cssW;
		state.cssH = cssH;
		// reset transform on every redraw so the dpr scale survives resize
		// and arbitrary number of drawPanel calls (canvas state can be
		// reset by some browsers when the backing store is resized).
		state.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

		clearCanvas(state);

		if (N_FRAMES === 0 || xRangeEnd <= xRangeStart) {
			drawNoData(state);
			return;
		}

		// Recompute axis ranges based on current visibility + xRange.
		state.rangeLeft = computeAxisRange(state.panel, "left", state.visibility);
		if (state.panel.y_right_label) {
			state.rangeRight = computeAxisRange(state.panel, "right", state.visibility);
		} else {
			state.rangeRight = null;
		}

		drawAxes(state);
		drawReferenceLines(state);
		drawSeries(state);
		drawDragOverlay(state);
	}

	function redrawAll() {
		for (var i = 0; i < panelStates.length; i++) {
			drawPanel(panelStates[i]);
		}
	}

	//============================================
	// Tooltip + interaction handlers.
	//============================================

	function showTooltip(state, evt, frameIdx) {
		var lines = [];
		lines.push(formatFrame(frameIdx));
		var sec = formatSeconds(frameIdx);
		if (sec !== null) { lines.push(sec); }
		for (var i = 0; i < state.panel.series.length; i++) {
			var series = state.panel.series[i];
			if (!state.visibility[series.name]) { continue; }
			if (series.axis === "right" && state.rangeRight === null) { continue; }
			var v = (frameIdx >= 0 && frameIdx < series.values.length)
				? series.values[frameIdx]
				: null;
			lines.push(series.name + ": " + formatNumber(v));
		}
		tooltip.textContent = lines.join("\n");
		tooltip.style.display = "block";
		// Position near cursor; keep on-screen.
		var px = evt.pageX + 12;
		var py = evt.pageY + 12;
		tooltip.style.left = px + "px";
		tooltip.style.top = py + "px";
	}

	function hideTooltip() {
		tooltip.style.display = "none";
	}

	function handleMouseMove(state, evt) {
		var rect = state.canvas.getBoundingClientRect();
		var localX = evt.clientX - rect.left;
		var localY = evt.clientY - rect.top;

		// If the cursor leaves the plot box, hide tooltip.
		if (localX < MARGIN_LEFT
				|| localX > state.cssW - MARGIN_RIGHT
				|| localY < MARGIN_TOP
				|| localY > state.cssH - MARGIN_BOTTOM) {
			hideTooltip();
			if (state.dragActive) {
				state.dragLastPx = localX;
				drawPanel(state);
			}
			return;
		}

		if (state.dragActive) {
			state.dragLastPx = localX;
			drawPanel(state);
			return;
		}

		var frameIdx = pixelToFrame(state, localX);
		if (N_FRAMES > 0) {
			showTooltip(state, evt, frameIdx);
		}
	}

	function handleMouseDown(state, evt) {
		var rect = state.canvas.getBoundingClientRect();
		var localX = evt.clientX - rect.left;
		// Only start drag on left mouse button.
		if (evt.button !== 0) { return; }
		// Only inside the plot box.
		if (localX < MARGIN_LEFT
				|| localX > state.cssW - MARGIN_RIGHT) {
			return;
		}
		state.dragActive = true;
		state.dragStartPx = localX;
		state.dragLastPx = localX;
		drawPanel(state);
	}

	function handleMouseUp(state, evt) {
		if (!state.dragActive) { return; }
		var rect = state.canvas.getBoundingClientRect();
		var localX = evt.clientX - rect.left;
		state.dragLastPx = localX;
		var x0 = Math.min(state.dragStartPx, state.dragLastPx);
		var x1 = Math.max(state.dragStartPx, state.dragLastPx);
		state.dragActive = false;
		// Require a minimum drag distance to commit a zoom.
		if (Math.abs(x1 - x0) < 4) {
			drawPanel(state);
			return;
		}
		var f0 = pixelToFrame(state, x0);
		var f1 = pixelToFrame(state, x1);
		if (f1 <= f0) {
			drawPanel(state);
			return;
		}
		// Sync the new x-range across all panels.
		xRangeStart = f0;
		xRangeEnd = f1 + 1;
		redrawAll();
	}

	function handleMouseLeave(state) {
		hideTooltip();
		if (state.dragActive) {
			// Cancel the drag, don't commit a zoom.
			state.dragActive = false;
			drawPanel(state);
		}
	}

	function handleDoubleClick() {
		xRangeStart = 0;
		xRangeEnd = N_FRAMES;
		redrawAll();
	}

	//============================================
	// Per-panel build: locate canvas, build checkbox row, attach handlers.
	//============================================

	function buildPanel(panel) {
		var canvas = document.getElementById(panel.id);
		if (canvas === null) { return; }
		var ctx = canvas.getContext("2d");
		if (ctx === null) { return; }

		// Initial visibility comes from default_visible.
		var visibility = {};
		var defaultSet = {};
		var defaults = panel.default_visible || [];
		for (var i = 0; i < defaults.length; i++) {
			defaultSet[defaults[i]] = true;
		}
		for (var k = 0; k < panel.series.length; k++) {
			var name = panel.series[k].name;
			visibility[name] = !!defaultSet[name];
		}

		// Locate the per-panel checkbox container (rendered by Python).
		var controls = null;
		var details = canvas.parentElement;
		if (details !== null) {
			var nodes = details.getElementsByClassName("panel-controls");
			if (nodes.length > 0) { controls = nodes[0]; }
		}

		var state = {
			panel: panel,
			canvas: canvas,
			ctx: ctx,
			visibility: visibility,
			rangeLeft: { min: 0, max: 1 },
			rangeRight: null,
			dragActive: false,
			dragStartPx: 0,
			dragLastPx: 0,
			// CSS-pixel dimensions. Overwritten on every drawPanel call;
			// these defaults exist only so any handler that fires before
			// the first redraw has finite (non-NaN) numbers to work with.
			cssW: 1,
			cssH: 1,
		};

		// Build checkbox row.
		if (controls !== null) {
			for (var s = 0; s < panel.series.length; s++) {
				var series = panel.series[s];
				var color = SERIES_COLORS[s % SERIES_COLORS.length];
				var label = document.createElement("label");
				var box = document.createElement("input");
				box.type = "checkbox";
				box.checked = visibility[series.name];
				box.dataset.seriesName = series.name;
				// Closure to capture series name correctly per checkbox.
				(function (sName, sBox) {
					sBox.addEventListener("change", function () {
						visibility[sName] = sBox.checked;
						drawPanel(state);
					});
				})(series.name, box);
				var swatch = document.createElement("span");
				swatch.style.display = "inline-block";
				swatch.style.width = "10px";
				swatch.style.height = "10px";
				swatch.style.background = color;
				swatch.style.marginRight = "4px";
				swatch.style.marginLeft = "4px";
				swatch.style.verticalAlign = "middle";
				label.appendChild(box);
				label.appendChild(swatch);
				label.appendChild(document.createTextNode(series.name));
				controls.appendChild(label);
			}
		}

		// Attach interaction handlers.
		canvas.addEventListener("mousemove", function (evt) {
			handleMouseMove(state, evt);
		});
		canvas.addEventListener("mousedown", function (evt) {
			handleMouseDown(state, evt);
		});
		// mouseup is global so a drag that ends outside the canvas still
		// commits or cancels cleanly.
		window.addEventListener("mouseup", function (evt) {
			if (state.dragActive) { handleMouseUp(state, evt); }
		});
		canvas.addEventListener("mouseleave", function () {
			handleMouseLeave(state);
		});
		canvas.addEventListener("dblclick", handleDoubleClick);

		panelStates.push(state);
		drawPanel(state);
	}

	//============================================
	// Initialize: build all panels then redraw on window resize.
	//============================================

	if (data.panels) {
		for (var p = 0; p < data.panels.length; p++) {
			buildPanel(data.panels[p]);
		}
	}

	// Redraw on resize so the canvas backing store matches CSS pixels.
	var resizeTimer = null;
	window.addEventListener("resize", function () {
		if (resizeTimer !== null) { clearTimeout(resizeTimer); }
		resizeTimer = setTimeout(function () {
			redrawAll();
			resizeTimer = null;
		}, 100);
	});

})();
