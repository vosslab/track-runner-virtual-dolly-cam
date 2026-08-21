# Graph Report - track-runner-virtual-dolly-cam  (2026-08-19)

## Corpus Check
- cluster-only mode - file stats not available

## Summary
- 4480 nodes * 6842 edges * 262 communities (221 shown, 41 thin omitted)
- Extraction: 98% EXTRACTED * 2% INFERRED * 0% AMBIGUOUS * INFERRED: 170 edges (avg confidence: 0.77)
- Token cost: 22,523 input * 2,746 output

## Graph Freshness
- Built from commit: `d89c586a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Blob Observation
- CLI Commands
- Walk Cost Model
- Camera Motion
- Commit Changelog
- Race Start Tests
- Version Bump
- Blob Observation Contract
- Palette and Overlay
- Encode Mode Tests
- Coordinate Space
- Frame Reader
- Camera Motion Tests
- Blob Walk Tile Local
- Annotation Window
- Heat Movie Smoke Test
- Storage Source Roundtrip
- Walk IO Parity
- Goodbox
- File Utilities
- Markdown Links
- Fastread Video
- Solve Queue Tests
- Encode Analysis
- Scoring Tests
- Residual Motion
- Tools Common
- Config Migration Tests
- Import Requirements
- Render Manifest Heat
- Viterbi Brute Force
- FFmpeg Video Filters
- Module Tests
- Off Frame Geometry
- Video Identity Tests
- Interval Fingerprint
- Annotation Controller
- Exception Handling
- Monkey Patch
- Test Naming Conventions
- Walk Driver
- PyPI Submission
- State IO Tests
- Velocity Model Tests
- Video Probe
- Crop Trajectory
- Changelog Query
- Changelog Helpers
- Blob Walk Windowed
- Analyze Report Tests
- Analyze Report Writer
- State IO
- Walker Bundle
- Frame Filters
- Analyze Report Renderer
- Flatten Broken Links
- .__init__
- Baseline Comparison
- Measurement and Progress
- Analyze Report Panels
- Seed Schema Tests
- Viterbi Path Selection
- Race Start Contact Sheet
- Heat Map Overlay
- Application Shell
- FrameView
- Init File Checks
- Walker A/B Test
- Whitespace Fixer
- Blob Walk Layer Order
- Motion Gate Tests
- Fastread Video Tests
- No Crop Coupling Tests
- Write Analyze Report
- Race Start Helpers
- Analyze Report Fixtures
- Regime Classifier
- Changelog Day Block
- ASCII Compliance Fixer
- Blob Walk Visible Seed Filter
- Interval Fingerprint Tests
- Scene Coordinate Tests
- Solver Driver Tests
- Target Mode Tests
- Review Interval Severity
- Velocity Model
- Seed Controller Tests
- Solve Queue Driver
- Paths Configuration
- Heat Map Overlay
- Keyboard Event Handler
- print_step
- Bin Target Table Tests
- Blob Walk Candidate Source
- Blob Walk Coord Sentinel
- Observe Blob Contract
- Seeds View Tests
- Interval Fingerprint Bin Tests
- Refine Mode Tests
- Residual Heat Map Tests
- Walk Coverage Tests
- Walker Bin Factor Regression
- Walker Bundle Seam Tests
- Video Benchmarking
- Status Bar Controller
- Seed Mode Controller
- Pyflakes Code Lint
- README First Paragraph Tests
- Shebangs Check
- Race Phases Tests
- Residual Motion Window Tests
- CLI Argument Parsing
- In-Box Heat Measurement
- Fingerprint Anti-Drift Tests
- Frame Reader Null-Free Contract
- In-Box Heat Sentinel Tests
- Indentation Check
- Pytest Hygiene Check
- Residual Motion Bin Factor Tests
- Camera Motion Bin Tests
- Review Tests
- Solver Integration Tests
- Walker Stall Fallback Tests
- Track Tool Setup
- Race Phase Detection
- Edit Mode Controller
- Status Presenter
- SeedsView
- Torso Size Stabilizer
- Track Detection
- Walker Adapter Tests
- Blob Walk V2 Heat Movie Frame
- Import Star Check
- M1D Heat Not Computed Detection
- Solve Default Bin Tests
- .eventFilter
- Walker Adapter Behavioral Tests
- Walk HTML Generation
- Residual Pre-Pass
- Crop Trajectory
- Overlay Management
- Key Event Handling
- solver_workers.py
- Graphics Items
- _YoloLoaderThread
- test_bandit_security.py
- test_tr_residual_motion_bin.py
- test_whitespace.py
- Version Bump and Upload
- Main Function
- Project URL and Python Version Checks
- Git and Twine Checks
- AST Import Tests
- ASCII Compliance Check
- Coordinate Space Tests
- Geometry and Point Tests
- Fast-Read File Handling
- Import Dot Check
- Crop Size Stabilizer Tests
- Solve Queue Format Tests
- Motion Gate Tests
- Frame Scrubbing
- Package Build and Logging
- Anchor Interpolation Tests
- Walker Neighbor Reached Tests
- Walker Flag Routing Tests
- Utility Functions
- Mode Documentation Refresh
- Box Utility Functions
- Seed Color Utilities
- Toolbar and Step Display
- Zoom Control Widget
- Script Files
- Debug Log Tests
- Fake Frame Reader
- Solver Worker Blob Pass Tests
- Legacy Store Reuse Tests
- Schema Version Tests
- Solve Mode Tests
- Stage 4 Parity Tests
- Regime Policies
- Video Identity Fingerprinting
- Log Capture Fixture
- Action Helpers
- Wheel Event Handling
- Walk HTML Generation
- Torso Box Coordinates
- Off-Center Crop Error
- Version and Venv Checks
- Fast-Read Context
- Status Presenter Tests
- Walk HTML V2
- Audit Rule Extraction
- Seed Geometry Derivation
- Zoom Control
- Polish Preview
- Target Controller
- Git Repository Checks
- Icon and QPixmap Conversion
- Confidence Decay Tests
- Analysis & Solver
- Interval Solver Tests
- Crop Assertions
- Offset Calculations
- CLI Help
- Review Summary
- Schema Validation
- Edit Mode
- Frame Title
- Prediction Legend
- Bug Reproduction
- Parameterization
- Crop Alpha Tests
- Prompt Building
- Step Cap Test
- Path Enumeration
- Quality Summary
- Diagnostics Writer
- Status Text
- Status Update
- Status Bar
- Quit Handling
- Dist Clean Script
- Repo Graph Script
- Argument Parsing
- Toolbar Build
- Frame Interpolation
- Frame Update
- Common Tools
- Playwright Setup
- Job Display
- Blob Walk Script
- Repo Root
- Box Edges Test
- Negative Coordinates
- Box In-Bounds
- Point Double-Conversion
- Point Double-Conversion
- Import Validation
- test_require_source_point_raises_for_wrong_type
- test_require_processed_point_raises_for_wrong_type
- Box Type Check
- Box Type Check
- Box Type Check
- Edge Calculation
- Diagnostics Validation
- Interval Partitioning
- Interval Partitioning
- Interval Targeting
- Interval Targeting
- Interval Targeting
- Prediction Building
- Blob Walker V2
- Blob Walker Core
- Track Runner Tool
- Output Path
- Contact Sheet Path
- Data Directory
- Parent Directory
- Qt UI Package
- Box Type Check

## God Nodes (most connected - your core abstractions)
1. `BaseAnnotationController` - 45 edges
2. `SeedController` - 41 edges
3. `EditController` - 41 edges
4. `main()` - 41 edges
5. `ProcessedBox` - 32 edges
6. `AnnotationWindow` - 28 edges
7. `fail()` - 28 edges
8. `FrameReader` - 24 edges
9. `ProcessedPoint` - 24 edges
10. `load_palette()` - 23 edges

## Surprising Connections (you probably didn't know these)
- `test_classified_severity_uses_overlay_config_style()` --calls--> `StatusPresenter`  [INFERRED]
  tests/test_tr_status_presenter.py -> track_runner/ui/status_presenter.py
- `test_pre_race_severity_none_renders_pre_race_badge()` --calls--> `StatusPresenter`  [INFERRED]
  tests/test_tr_status_presenter.py -> track_runner/ui/status_presenter.py
- `_make_overlay()` --calls--> `HeatMapOverlay`  [INFERRED]
  tests/test_tr_heat_map_overlay.py -> track_runner/ui/heat_map_overlay.py
- `_make_live_trace()` --references--> `BlobObserverTrace`  [EXTRACTED]
  tests/test_m1d_heat_not_computed_detection.py -> track_runner/blob_trace.py
- `load_scene_transform()` --references--> `SceneTransform`  [EXTRACTED]
  tools/blob_walk_v2/walk_tool_setup.py -> track_runner/scene_coords.py

## Import Cycles
- None detected.

## Communities (262 total, 41 thin omitted)

### Community 0 - "Blob Observation"
Cohesion: 0.04
Nodes (61): _ellipse_area_from_blob(), Path, Render per-frame walker tiles with heat-map overlay and blob geometry. Produces..., Extract area from a blob dict, defaulting to zero., Render a single walker tile PNG. Reads source frame, composites heat-map..., render_walk_tile(), BlobObserverTrace, Blob observer trace dataclass for refinement diagnostics. (+53 more)

### Community 1 - "CLI Commands"
Cohesion: 0.05
Nodes (71): _apply_encode_overrides(), _build_predictions_from_solved_intervals(), _check_identity_mismatch(), _clear_stale_diagnostics_artifact(), _clear_stale_torso_artifact(), _ensure_target_diagnostics(), _generate_race_start_target_frames(), _invalidate_intervals_for_frames() (+63 more)

### Community 2 - "Walk Cost Model"
Cohesion: 0.06
Nodes (48): _fps(), _make_blob(), Synthetic-lattice tests for the pairwise velocity-delta Viterbi cost model...., The new model must select the moving runner over a stationary distractor., Mover at 0.4 torso/frame with 2x mag wins over stationary distractor. 9-frame..., Equal-mag stationary-vs-mover is underdetermined; DP must commit to one..., Walker must select a coherent center track over oscillating high-mag limbs., Coherent center track wins over leg blobs that alternate vertically. 9-frame... (+40 more)

### Community 3 - "Camera Motion"
Cohesion: 0.06
Nodes (54): _build_hann_for_model(), ContinuousZoomEstimator, DiscreteZoomEstimator, _estimate_chunk_pairs(), _estimate_parallel(), _estimator_type_to_model(), FixedZoomEstimator, load_active_camera_motion_or_fail() (+46 more)

### Community 4 - "Commit Changelog"
Cohesion: 0.05
Nodes (59): added_changelog_bullet_lines(), build_action_prompt(), build_git_status_block(), check_version_freshness(), clean_entry_text(), collapse_whitespace(), commit_with_message_file(), current_calver_month() (+51 more)

### Community 5 - "Race Start Tests"
Cohesion: 0.05
Nodes (50): FakeSceneTransform, _mk_seed(), Tests for race_start module (M2 rewrite). Tests core pre-race frame range..., Convert scene coords back to pixel space. Args: frame_index: Frame index. sx: X..., Stationary cluster followed by coherent motion -> bracket is the transition..., Adjacent-frame pre-race seeds are debounced (total annotation noise across a..., A single big annotation jump followed by return is rejected by the next-window..., Same absolute scene displacement triggers for a small torso but not for a large... (+42 more)

### Community 6 - "Version Bump"
Cohesion: 0.06
Nodes (54): advanced_help(), build_version_file_entry(), bump_prerelease(), bump_version(), choose_base_version(), current_calver_month(), ensure_version_file_entry(), format_number() (+46 more)

### Community 7 - "Blob Observation Contract"
Cohesion: 0.06
Nodes (51): ProcessedBox, ProcessedPoint, A single point in PROCESSED (post-bin, goodbox-snapped) pixels., A center-size torso box in PROCESSED (post-bin) pixels., True iff the box CENTER lies inside the processed frame. Off-frame predicate..., _make_blob(), _make_reader(), Observable-contract tests for residual_motion.observe_blob_at. Scope:... (+43 more)

### Community 8 - "Palette and Overlay"
Cohesion: 0.06
Nodes (53): get_draw_mode_badge_color(), get_encoder_overlay_opacity(), get_heat_map_style(), get_mono_font_family(), get_overlay_font_size(), get_pre_race_reference_bgr(), get_prediction_bgr(), get_prediction_color() (+45 more)

### Community 9 - "Encode Mode Tests"
Cohesion: 0.07
Nodes (46): _aspect_ok(), _full_traj(), _make_args(), _parse(), Namespace, CLI override parsing for encode mode. Focused behavioral tests for the non-..., Convenience wrapper with the test layout's defaults., True when fit_w / fit_h matches aspect_ratio within float epsilon. (+38 more)

### Community 10 - "Coordinate Space"
Cohesion: 0.05
Nodes (34): Typed coordinate primitives for the binning pipeline's two pixel spaces. THE..., Return (x1, y1, x2, y2) derived from the float center. Math stays in float; no..., Convert to SOURCE pixels via pure *bin_factor scale., True iff the point lies inside the processed frame. Off-frame predicate: 0 <=..., Convert to SOURCE pixels via pure *bin_factor scale. Center scales via..., Return obj if it is a SourcePoint, else raise ValueError (loud)., Return obj if it is a ProcessedPoint, else raise ValueError (loud)., Return obj if it is a SourceBox, else raise ValueError (loud). (+26 more)

### Community 11 - "Frame Reader"
Cohesion: 0.05
Nodes (31): FrameReader, open_analysis_reader(), ndarray, Reliable frame reader for video via cv2.VideoCapture. FrameReader wraps..., Open a FrameReader using the shared project default-bin policy. This is THE..., Resolve a FrameGeometry from raw source dims and a bin factor. scaled =..., Snap a scaled axis to its goodbox, or keep it if the snap would discard more..., Read video frames via cv2.VideoCapture. Decodes frames using cv2.VideoCapture... (+23 more)

### Community 12 - "Camera Motion Tests"
Cohesion: 0.06
Nodes (40): _dummy_identity(), _make_motion_track(), ndarray, Tests for track_runner.camera_motion module., Minimal video_identity dict for cache tests., Build a MotionTrack with matching-length dx/dy/quality arrays., fixed_zoom caches must not persist the constant-1.0 scale array., Round-trip invariant: dx/dy/quality preserved; scale synthesized. (+32 more)

### Community 13 - "Blob Walk Tile Local"
Cohesion: 0.06
Nodes (38): WS2-F: the typed processed->tile-local render conversion (single subtract). The..., Tile-local edges == PROCESSED box edges minus the ROI origin (once)., Subtracting the ROI origin once differs from subtracting it twice., A SourceBox handed to the processed->tile-local helper fails loud., test_tile_local_edges_equal_processed_edges_minus_roi_bin4(), test_tile_local_rejects_wrong_space(), test_tile_local_single_subtract_not_double(), compute_plus_arm_px() (+30 more)

### Community 14 - "Annotation Window"
Cohesion: 0.06
Nodes (22): QDialog, AnnotationWindow, Handle mode button toggled signal. Determines which mode is now active, updates..., Apply mode-specific accent color to frame view. Args: mode: Mode name ("seed",..., Set or clear the active controller. Deactivates the previous controller,..., Main annotation workspace with mode selection and frame display. Provides a..., Handle overlay toggle action. Args: checked: Whether the overlay is now enabled., Set the motion heat-map overlay status label text. Called by... (+14 more)

### Community 15 - "Heat Movie Smoke Test"
Cohesion: 0.06
Nodes (37): _fake_compute_heat_map_roi(), _FakeReader, _FakeSceneTransform, _install_patch(), main(), _make_direction_path(), Build a synthetic per-frame direction path list. Each entry matches the..., Replace compute_heat_map_roi in all modules that have already imported it.... (+29 more)

### Community 16 - "Storage Source Roundtrip"
Cohesion: 0.11
Nodes (39): _assert_edges_close(), _edges(), _geometry(), _interior_stored_box(), _processed_walker_path(), Solve -> storage round trip keeps boxes in SOURCE pixels at bin > 1. This is..., Known SOURCE-pixel box (center + size) at a frame, constant velocity., SOURCE-pixel seed dict (the solver's input space). (+31 more)

### Community 17 - "Walk IO Parity"
Cohesion: 0.07
Nodes (39): _make_seeds_dict(), Core-routing tests for the standalone blob_walk_v2 HTML tool. walk_io.py was..., walk_tool_setup interval-scores filename matches tr_paths for the same stem., normalize_video_basename strips .track_runner; filename matches tr_paths., Write a minimal valid interval_scores.json with pre_race_reference., Write a legacy interval_scores.json without pre_race_reference., load_race_start_frame reads race_start_frame from pre_race_reference., load_race_start_frame raises RuntimeError when the artifact is absent. (+31 more)

### Community 18 - "Goodbox"
Cohesion: 0.08
Nodes (25): is_good_size(), largest_goodbox_at_most(), FFT-friendly box-size predicate (cryo-EM goodbox rule). The cv2.phaseCorrelate..., Return True if n satisfies the goodbox predicate. Args: n: candidate box size,..., Return the largest goodbox not exceeding n. Walks downward from n until the..., _open(), Unit tests for FrameReader: bin_factor, FrameGeometry, .mkv guard. The bin path..., Write a tiny synthetic .mkv with a known constant pattern. (+17 more)

### Community 19 - "File Utilities"
Cohesion: 0.06
Nodes (47): clear_stale_reports(), collect_file_violations(), collect_python_violations(), discover_files(), format_violation_assert_message(), format_violation_report(), _gather_all_paths(), get_repo_root() (+39 more)

### Community 20 - "Markdown Links"
Cohesion: 0.08
Nodes (37): Match, blank_match(), build_tracked_dirs(), check_local_link(), check_path_like_text(), classify_url(), collect_report(), collect_violations() (+29 more)

### Community 21 - "Fastread Video"
Cohesion: 0.08
Nodes (37): Popen, _build_ffmpeg_transcode_cmd(), _check_duration(), _check_fps(), _check_geometry_and_count(), _check_timestamp_alignment(), create_fastread_video(), FastreadValidation (+29 more)

### Community 22 - "Solve Queue Tests"
Cohesion: 0.09
Nodes (35): _FakeSceneTransform, _make_pre_race_reference(), _make_seed(), Unit tests for solve_queue.plan_interval_work. Tests the pure-function planner..., Same input produces equal plans across calls. Pure-function property., Interval ending at interval_low is pre_race; interval seed-pair is interval;..., Pre-race intervals execute correctly when every normal+interval is cached...., Minimal scene_to_pixel stub for pre-race synthesis tests. Applies a fixed pixel... (+27 more)

### Community 23 - "Encode Analysis"
Cohesion: 0.08
Nodes (37): analyze_crop_stability(), analyze_solver_context(), _classify_region_cause(), _compute_bad_frame_runs(), _compute_center_jerk(), _compute_center_velocities(), _compute_composition_metrics(), _compute_dominant_symptom() (+29 more)

### Community 24 - "Scoring Tests"
Cohesion: 0.08
Nodes (34): _make_track(), MockSceneTransform, Unit tests for scoring metrics. Covers the new velocity_consistency smoothness..., Tracks under 3 frames return 1.0 (acceleration is undefined)., Long-interval demotion uses the passed fps, not a hardcoded value. At fps=60 a..., compute_agreement_debug returns the same mean as compute_agreement., Identity transform: scene coordinates equal pixel coordinates., Build a track list of state dicts from (cx, cy) tuples. (+26 more)

### Community 25 - "Residual Motion"
Cohesion: 0.09
Nodes (32): BlobObservation, build_warp_matrix(), colorize_jet(), compute_cue_confidence(), compute_residual_for_frame(), _compute_residual_with_extras(), _compute_roi(), compute_trajectory_tangent() (+24 more)

### Community 26 - "Tools Common"
Cohesion: 0.09
Nodes (25): check_dependency(), ensure_file_exists(), fps_fraction_to_float(), parse_time_seconds(), probe_duration_seconds(), probe_video_stream(), CompletedProcess, tools_common.py Shared utility functions for emwy tools. Consolidates... (+17 more)

### Community 27 - "Config Migration Tests"
Cohesion: 0.07
Nodes (27): Schema migration tests for tr_config. Focused behavioral check: legacy..., auto_save_migration=True rewrites the YAML so the deprecation notice never..., Without auto_save_migration, the on-disk file is left alone even when the in-..., test_load_config_auto_save_persists_migration(), test_load_config_default_does_not_rewrite_on_disk(), Interactive CLI questionnaire for per-video camera configuration. Provides a..., Run interactive CLI setup questionnaire for camera configuration. Prompts the..., run_setup() (+19 more)

### Community 28 - "Import Requirements"
Cohesion: 0.12
Nodes (25): build_violations_by_file(), collect_import_roots(), collect_repo_module_names(), collect_report(), get_stdlib_modules(), is_allowed_module(), load_requirement_modules(), make_report_lines() (+17 more)

### Community 29 - "Render Manifest Heat"
Cohesion: 0.08
Nodes (30): _base_record(), Tests for the interval-level heat REPORT in check_render_manifest (C13 rework)...., The conversion_count gate still fires on per-tile records., Build one interval-direction heat summary dict. heat_present_pct defaults to..., Return a minimal valid per-tile manifest record (gate fields only)., All eligible frames are heat-present: fraction is 100%., Eligible but zero present: fraction 0.0%, coverage surfaced., Zero eligible frames: fraction reported as 0.0%, not-computed surfaced. (+22 more)

### Community 30 - "Viterbi Brute Force"
Cohesion: 0.11
Nodes (31): _assert_dp_equals_brute_force_min(), _build_dense_lattice(), _build_far_candidate_lattice(), _build_sparse_lattice(), _build_stationary_lattice(), _build_zero_mag_lattice(), _dp_cost(), _make_blob() (+23 more)

### Community 31 - "FFmpeg Video Filters"
Cohesion: 0.09
Nodes (29): get_ffmpeg_vf_string(), Build the ffmpeg -vf value string from a filter list. Extracts only ffmpeg..., _box_to_crop_coords(), _compute_overlay_scale(), copy_audio(), draw_debug_overlay_cropped(), encode_cropped_video(), encode_cropped_video_parallel() (+21 more)

### Community 32 - "Module Tests"
Cohesion: 0.09
Nodes (30): ModuleType, collect_report(), count_error_details(), format_issue_line(), is_ascii_bytes(), is_emoji_codepoint(), list_error_files(), load_module() (+22 more)

### Community 33 - "Off Frame Geometry"
Cohesion: 0.07
Nodes (28): Behavioral tests for track_runner/off_frame_geometry.py Pure-numpy fixtures..., cy linearly interpolated: midpoint equals integer mean within 1 px., Single-bracket case (NIF runs to end): cy held flat; cx pinned to edge., Bottom-edge exit: last visible center near bottom, cx mid-frame., Right-edge exit: last visible box near right edge -> cx == frame_width., 5% tie-favoring rule: when horiz/vert distances within 5%, prefer left/right., All filled values fit in uint16 range (0 <= v < 2**16)., Pre-race NIF (seed before race_start_frame) raises ValueError. (+20 more)

### Community 34 - "Video Identity Tests"
Cohesion: 0.07
Nodes (29): Tests for track_runner.tr_video_identity module. Tests the video_identity..., Verify height mismatch is blocking., Verify fps within 0.01 tolerance produces no mismatch., Verify file rename alone produces only informational mismatch., Verify remux display-precision fps shift is absorbed by the 1.0 tolerance. A..., Verify a large fps change (30 vs 60) is informational, not blocking. fps is..., Verify duration within 0.5s tolerance produces no mismatch., Verify duration beyond 0.5s tolerance is informational. (+21 more)

### Community 35 - "Interval Fingerprint"
Cohesion: 0.09
Nodes (29): compute_interval_fingerprint(), Fingerprint wrapper that includes the unified geometry tag. Every caller that..., _apply_trajectory_erasure(), blend_paths(), collect_erased_frames(), derive_per_frame_confidence(), _dispatch_blob_pass(), Per-interval analytical solving for track_runner. Splits the video timeline... (+21 more)

### Community 36 - "Annotation Controller"
Cohesion: 0.08
Nodes (17): BaseAnnotationController, QObject, QWidget, Build the controller toolbar. Subclass must implement. Returns: QWidget for the..., Called after base activate finishes. Subclass must implement., Keybinding hint string for the key hint overlay. Subclass must implement...., Short mode name for display. Subclass must implement. Returns: String like..., Toolbar widget for the annotation toolbar. Returns: QWidget with navigation and... (+9 more)

### Community 37 - "Exception Handling"
Cohesion: 0.07
Nodes (20): Exception, GracefulQuit, handle_key(), install_sigint_handler(), KeyInputReader, _quit_trace(), Enter cbreak mode if stdin is a TTY., Restore original terminal settings and flush leftover input. (+12 more)

### Community 38 - "Monkey Patch"
Cohesion: 0.13
Nodes (25): MonkeyPatch, _make_probe(), _patched_validate(), Matching probe dicts and healthy FrameReader return a FastreadValidation., Successful validation result has a non-empty timestamp_fallback_note., Helper: run validate with two separate probe dicts, fail-on-call order., Width mismatch between original and fastread raises RuntimeError., Height mismatch raises RuntimeError. (+17 more)

### Community 39 - "Test Naming Conventions"
Cohesion: 0.11
Nodes (28): e2e_dir_exists(), get_e2e_dir(), get_playwright_dir(), has_playwright_import(), list_e2e_files(), list_files_recursive(), list_mjs_files_outside_playwright(), list_playwright_files() (+20 more)

### Community 40 - "Walk Driver"
Cohesion: 0.09
Nodes (28): aggregate_interval_heat(), check_resume_needed(), _fill_seed_pred(), IntervalSummary, _project_path_to_source(), Path, Walk engine: per-interval FWD/BWD walk + render library for blob_walk_v2...., Merge solved interval entries into the video's torso_box_coords npz. Load-... (+20 more)

### Community 41 - "PyPI Submission"
Cohesion: 0.10
Nodes (27): clean_build_artifacts(), fail(), print_error(), Resolve the repository root (parent of this script)., Resolve and validate the pyproject.toml path., Resolve the package name from pyproject metadata., Resolve the package version from pyproject metadata., Resolve the import name for the test import. Args: arg_value: Value from... (+19 more)

### Community 42 - "State IO Tests"
Cohesion: 0.07
Nodes (27): Unit tests for state_io diagnostics schema migrations. Tests the v3 diagnostics..., Reader migrates flat-shape entries on load regardless of header version...., Reader raises RuntimeError when entry lacks both nested and flat shapes. Stale..., Header 3 file without pre_race_reference loads with pre_race_reference=None. v3..., Header 2 file with flat shape migrates to nested on load. v2 files have flat..., Header 3 file with race_phase block: block is dropped on load. v3 and earlier..., Writer serializes pre_race_reference when present. Diagnostics dict with..., Writer with pre_race_reference=None loads back as None. Diagnostics dict with... (+19 more)

### Community 43 - "Velocity Model Tests"
Cohesion: 0.09
Nodes (21): Tests for track_runner.velocity_model module., Both FWD and BWD map slot 0 to start_frame and slot -1 to end_frame. Post-fix..., Test that Hermite interpolation matches endpoints exactly., Test that endpoints match seeds exactly (< 0.1 px error)., Test slope estimation from backward (left) neighbors., Both passes: slot i corresponds to absolute frame start_frame + i. Central..., BWD gate-time tangent points in the same chronological direction as FWD. Pre-..., BWD raw_pred confidence peaks at slot n-1 (end_frame anchor). Tests the low-... (+13 more)

### Community 44 - "Video Probe"
Cohesion: 0.09
Nodes (23): probe_video(), Probe video metadata via the mediainfo CLI. Provides a single public function..., Probe video metadata using mediainfo JSON output. Extracts resolution, fps,..., _draw_predictions_overlay(), _draw_preview_box(), _draw_seed_overlay(), edit_seeds(), ndarray (+15 more)

### Community 45 - "Crop Trajectory"
Cohesion: 0.12
Nodes (26): apply_crop(), apply_experiment_overrides(), center_lock_override(), _detect_zoom_phases(), direct_center_crop_trajectory(), fixed_height_override(), _forward_backward_ema(), _max_centered_fit_size() (+18 more)

### Community 46 - "Changelog Query"
Cohesion: 0.11
Nodes (25): date, apply_filters(), category_sort_key(), collect_files(), format_csv(), format_json(), format_text(), main() (+17 more)

### Community 47 - "Changelog Helpers"
Cohesion: 0.09
Nodes (25): Entry, find_duplicate_dates(), _is_valid_iso_date(), newest_date(), parse_day_blocks(), parse_file(), parse_text(), print_error() (+17 more)

### Community 48 - "Blob Walk Windowed"
Cohesion: 0.10
Nodes (18): _make_blob(), Pure-function tests for the windowed path-selection internals (v13+). Covers..., Empty corridor_blobs at center frame produces soft_miss_no_blob status., Candidates on center frame but all outside displacement cap -> interpolated or..., Last two frames in window have no candidates; first three accepted. Expected:..., Viterbi hard-prunes candidates beyond ABSOLUTE_MAX_JUMP_W torso-widths per..., All-empty candidate lists: path is all None (skip nodes)., Build a minimal corridor blob dict for Viterbi testing. (+10 more)

### Community 49 - "Analyze Report Tests"
Cohesion: 0.08
Nodes (25): Tests for track_runner.analyze_report (Groups A-G: HTML structure, JSON schema,..., Constant input -> smoothed output equals raw within float epsilon., A single NaN in a window does NOT blank the entire window output., A window with zero non-NaN elements yields nan., Constant crop_h -> smoothed series equals raw within float epsilon., Zoom-bounce signal: alternating crop_h -> smoothed values sit between extremes., Returns documented schema with four series spanning twin axes., When crop_h and torso_h scale by the same factor, achieved is constant. (+17 more)

### Community 50 - "Analyze Report Writer"
Cohesion: 0.13
Nodes (25): _build_analyze_report_data(), _camera_motion_panel_data(), _load_renderer_js(), ndarray, Path, HTML diagnostic report writer for analyze mode. Builds a self-contained HTML..., Build the runner-speed panel data dict for the analyze report. Emits raw per-..., Build the zoom-stability panel data dict for the analyze report. Emits raw... (+17 more)

### Community 51 - "State IO"
Cohesion: 0.09
Nodes (25): _canonicalize_seed_for_write(), frame_dict_to_source_box(), interval_fingerprint(), load_diagnostics(), load_torso_box_coords(), merge_seeds(), peek_diagnostics_schema(), peek_torso_box_coords_schema() (+17 more)

### Community 52 - "Walker Bundle"
Cohesion: 0.13
Nodes (20): _build_full_span_path(), build_walker_bundles_for_interval(), count_post_seed_accepts(), _NullDebugLog, Stage 4 walker input bundle seam. Defines the self-contained input the windowed..., Build the FWD and BWD walker bundles for one promoted interval. The caller..., Run one walker pass by handing a bundle to an injectable walker. This is the..., No-op stand-in for walk_debug_log.DebugLogWriter. walk_one_direction requires a... (+12 more)

### Community 53 - "Frame Filters"
Cohesion: 0.14
Nodes (24): apply_auto_levels(), apply_bilateral(), apply_bilateral_clahe(), apply_clahe(), apply_denoise(), apply_edge_enhance(), apply_filter(), apply_filter_pipeline() (+16 more)

### Community 54 - "Analyze Report Renderer"
Cohesion: 0.19
Nodes (24): buildPanel(), clearCanvas(), computeAxisRange(), drawAxes(), drawDragOverlay(), drawNoData(), drawPanel(), drawReferenceLines() (+16 more)

### Community 55 - "Flatten Broken Links"
Cohesion: 0.13
Nodes (24): build_basename_index(), build_tracked_set(), find_basename_match(), get_repo_root(), is_local_file_link(), link_target_tracked(), main(), parse_args() (+16 more)

### Community 56 - ".__init__"
Cohesion: 0.17
Nodes (11): QGraphicsItem, QGraphicsTextItem, Initialize PreviewBoxItem. Args: x: Top-left x coordinate. y: Top-left y..., A zoom scale indicator displayed in the top-right corner. Shows the zoom factor..., Initialize ScaleBarItem. Args: parent: Parent graphics item., Initialize legend item positioned at top-left. Placed in the top-left corner to..., Initialize RectItem. Args: x: Top-left x coordinate. y: Top-left y coordinate...., Short colored line sample for the prediction legend. Draws a horizontal line... (+3 more)

### Community 57 - "Baseline Comparison"
Cohesion: 0.15
Nodes (24): compare_baseline(), compare_csv(), compare_row(), compute_accepted_fraction(), _interval_dirname(), main(), parse_args(), _parse_optional_float() (+16 more)

### Community 58 - "Measurement and Progress"
Cohesion: 0.10
Nodes (16): Measurement, Text, BlockBarColumn, FrameETAColumn, make_solve_progress(), Progress, Render the progress bar with block characters. Uses terminal width minus space..., Claim all available width so the bar expands to fill the terminal. (+8 more)

### Community 59 - "Analyze Report Panels"
Cohesion: 0.08
Nodes (24): _extract_embedded_json(), Warnings section appears and the warning text is present in body and JSON., Warning strings are HTML-escaped in <li> markup but stored raw in JSON., Extract and parse the embedded JSON payload from the HTML body. The closing..., Happy path: all four panels rendered, no warnings, JSON has 4 panels., Every series.values array has the same length as data.frames., Empty trajectory produces 0 panels and no canvas elements., End-to-end: write_analyze_report under the WP-3C-1 fixture renders all four... (+16 more)

### Community 60 - "Seed Schema Tests"
Cohesion: 0.09
Nodes (23): _make_v2_seed_dict(), Tests for the canonical v3 seeds schema. Covers the WP-S1 acceptance criteria..., not_in_frame seeds carry no torso_box and must survive a round-trip with no..., The redacted real-file fixture loads through load_seeds without error and..., After the first migration write, a second load + write is byte-identical. This..., Unknown keys in the in-memory dict do not survive write_seeds., Legacy 'obstructed' status with a torso_box is migrated to 'approximate' on..., Headers outside SEEDS_ACCEPTED_HEADERS raise with a clear message. (+15 more)

### Community 61 - "Viterbi Path Selection"
Cohesion: 0.13
Nodes (23): _active_weights(), _angle_between(), compute_path_cost(), compute_path_step_costs(), compute_path_term_breakdown(), _edge_cost(), _evaluate_path_terms(), _evidence_costs_for_frame() (+15 more)

### Community 62 - "Race Start Contact Sheet"
Cohesion: 0.11
Nodes (19): FakeFrameReader, _make_tiles(), Tests for race_start_contact_sheet renderer., Error path: frame read returns None., Build a standard tile list for contact sheet tests. Args: frame_offset_fn:..., Error path: wrong tile count., Padding test: crop fully in frame, no padding needed., Regression: refine-mode seed edits can produce tiles with slightly different... (+11 more)

### Community 63 - "Heat Map Overlay"
Cohesion: 0.07
Nodes (28): _FakeReader, _make_overlay(), Smoke tests for the sticky motion heat-map overlay. Two invariants are locked..., After showing heat for frame N and requesting frame N+1, heat is visible for..., 10 rapid request_show calls produce exactly one compute invocation., Minimal BGR frame reader used to exercise the residual compute., Create a fixed grayscale-flat video in memory., Return a BGR copy of the flat frame, or None if out of range. (+20 more)

### Community 64 - "Application Shell"
Cohesion: 0.11
Nodes (17): QApplication, QMainWindow, AppShell, Application shell for track runner UI. Provides the main window with theme..., Main application window with theme support. Inherits from QMainWindow and..., Initialize the application shell. Calls apply_theme with 'system' mode to..., Toggle between dark and light themes. Flips the current theme by determining..., Set the application theme. Args: mode: Theme mode ('dark' or 'light'). (+9 more)

### Community 65 - "FrameView"
Cohesion: 0.08
Nodes (16): QGraphicsView, QResizeEvent, QShowEvent, FrameView, QWidget, QGraphicsView for displaying video frames with zoom and coordinate mapping., A QGraphicsView for displaying and interacting with video frames. Supports zoom..., Convert display coordinates to scene (frame) coordinates. Args: x: Display x... (+8 more)

### Community 66 - "Init File Checks"
Cohesion: 0.12
Nodes (21): stmt, collect_report(), collect_violations(), count_substantive_lines(), extract_target_names(), find_init_issues(), is_module_docstring(), fixture (+13 more)

### Community 67 - "Walker A/B Test"
Cohesion: 0.13
Nodes (21): _assert_corpus_matches_file(), _classify(), main(), parse_args(), Namespace, Random, Loudly fail if the constant corpus drifts from data/outdoor_corpus.txt., Convert seeds to scene tuples (frame, sx, sy, sw, sh) for scoring. not_in_frame... (+13 more)

### Community 68 - "Whitespace Fixer"
Cohesion: 0.13
Nodes (21): ensure_final_newline(), expand_inputs(), fix_whitespace_bytes(), main(), normalize_line_endings(), parse_args(), Namespace, Parse command-line arguments. Returns: argparse.Namespace: Parsed arguments. (+13 more)

### Community 69 - "Blob Walk Layer Order"
Cohesion: 0.11
Nodes (20): _make_valid_order(), Unit tests for walk_palette.resolve_layer_order validation behavior. Covers the..., Return a list with every known layer exactly once (valid input)., Missing walk_tile_layer_order key falls back to the built-in default., Unknown layer name in the YAML list raises ValueError loudly., Duplicate layer name in the YAML list raises ValueError loudly., Omitting a known layer name from the YAML list raises ValueError loudly., A complete, non-duplicate list of all known layers is accepted without error. (+12 more)

### Community 70 - "Motion Gate Tests"
Cohesion: 0.09
Nodes (16): Unit tests for tools/blob_walk_v2/core/walk_motion_gate.py. Tests cover cold-..., Test radial-allowance contribution from toward/away camera motion., Set torso_w_drift_frac and verify it contributes to expected_jump., Test that per_step_cap scales with dt_for_gate., Same torso, different dt_frames; allowed_jump scales by dt_for_gate., Test cold-start accept under chord-velocity seed., Accept cold-start motion under seed-chord velocity. After bootstrap, the walker..., Test rejection when actual_jump > ABSOLUTE_MAX_JUMP_W * torso_w. (+8 more)

### Community 71 - "Fastread Video Tests"
Cohesion: 0.09
Nodes (21): test_fastread_video.py Unit tests for fastread-video validation and path..., default_config_path stem matches original basename, not fastread basename., default_intervals_path stem matches original basename, not fastread basename., Contact-sheet artifact path uses the original stem given a fast-read decode..., write_analyze_report embeds canonical_source and decode_source in HTML., Argv contains the adjacent pair -fps_mode:v / passthrough. ffmpeg requires..., resolve_stride(59.94) == resolve_stride(60.0). Backs the fps-safety claim: the..., fastread_video_path produces a .fastread.mkv file beside the source. (+13 more)

### Community 72 - "No Crop Coupling Tests"
Cohesion: 0.12
Nodes (21): _collect_target_files(), _imports_tr_crop(), _is_crop_feel_key(), Module, parametrize, AST guard: solve/walker modules must not import tr_crop or read crop-feel..., Return True when key_value is a crop-feel config key. Exact match against..., Return (lineno, description) for crop-feel key accesses in tree. Detects two... (+13 more)

### Community 73 - "Write Analyze Report"
Cohesion: 0.09
Nodes (22): _call_write(), The inlined renderer <script> block appears after the JSON data block., Stem appears immediately after the <h1> opening tag., The embedded JSON block is valid JSON with required M1 keys., HTML must be self-contained: no http:// or https:// URLs., HTML output must be ASCII-only (no non-ASCII bytes)., Warnings section must not appear when warnings=[]., Build standard kwargs for write_analyze_report and call it. Monkeypatches... (+14 more)

### Community 74 - "Race Start Helpers"
Cohesion: 0.10
Nodes (21): choose_race_start_confirmation_frames(), compute_pre_race_reference(), compute_window_metrics(), detect_race_start(), detect_race_start_in_interval(), locate_race_start_interval(), pick_race_start_frame_midpoint(), print_race_phase_summary() (+13 more)

### Community 75 - "Analyze Report Fixtures"
Cohesion: 0.10
Nodes (19): _IdentitySceneTransform, make_synthetic_inputs(), Shared synthetic-input fixture for analyze_report integration tests. Lives in..., Stub SceneTransform that returns pixel coords unchanged. Used by tests where..., Return (trajectory, crop_rects, motion_track, scene_transform, fps, config). 30..., The four canvas IDs appear in the required document order., Production-path trajectories use cx/cy directly; verify they are read., Constant scene velocity -> per-frame raw speed array is approximately constant.... (+11 more)

### Community 76 - "Regime Classifier"
Cohesion: 0.14
Nodes (20): classify_regimes(), _classify_single_frame(), format_regime_summary(), _labels_to_spans(), _per_frame_features(), ndarray, Regime classifier for smart crop mode. Classifies trajectory spans into regimes..., Compute rolling mean with centered window, edge-padded. Args: signal: 1-D numpy... (+12 more)

### Community 77 - "Changelog Day Block"
Cohesion: 0.15
Nodes (19): DayBlock, One ``## YYYY-MM-DD`` day block from a changelog file. Attributes: date: ISO..., compute_archive_path(), find_boundary_conflict(), main(), parse_args(), print_duplicate_error(), print_loud_warning() (+11 more)

### Community 78 - "ASCII Compliance Fixer"
Cohesion: 0.14
Nodes (19): apply_simple_fixes(), find_non_latin1_chars(), fix_ascii_compliance(), main(), normalize_line_endings(), parse_args(), Namespace, Apply simple replacements for ASCII/ISO-8859-1 compliance. Args: text: Input... (+11 more)

### Community 79 - "Blob Walk Visible Seed Filter"
Cohesion: 0.14
Nodes (19): _make_interval(), Regression tests: select_random_visible returns only visible/visible pairs. Two..., Empty post_start list produces empty result., Every returned interval must have both seeds visible, regardless of input mix., When visible count exceeds n, exactly n intervals are returned., Output is sorted ascending by left_seed frame_index., Build a minimal SeedToSeedInterval with the given seed statuses., A visible/visible pair is returned. (+11 more)

### Community 80 - "Interval Fingerprint Tests"
Cohesion: 0.13
Nodes (19): _make_seed(), Unit tests for interval fingerprinting and seed filtering. Tests the low-level..., Duplicate frame_index entries resolve by keeping the latest pass. Behavior..., Obstructed seeds accepted only when torso_box is present. Non-obvious business..., A SCHEMA_VERSION bump that is NOT in GEOMETRY_AFFECTING_SCHEMAS must leave the..., Adding a higher version to GEOMETRY_AFFECTING_SCHEMAS DOES change the geometry..., Helper to build seed dicts with canonical structure., Same seed inputs produce the same fingerprint bytes. Pure-function property:... (+11 more)

### Community 81 - "Scene Coordinate Tests"
Cohesion: 0.10
Nodes (19): Tests for track_runner.scene_coords module., Test that scale != 1.0 correctly transforms coordinates and sizes., Test that scale correctly transforms bounding box sizes., Test that with identity transform, pixel coords equal scene coords., Test that translation and scale work together correctly., Test that frame 0 has no cumulative motion applied., Test SceneTransform correctly handles piecewise constant scale. Tests the..., Test that pixel_to_scene correctly removes accumulated translation. (+11 more)

### Community 82 - "Solver Driver Tests"
Cohesion: 0.18
Nodes (18): _DummyReader, _fake_result(), _make_motion(), _make_seeds(), Driver-level invariants for `interval_solver.solve_all_intervals`. These tests..., interval_results must be returned in seed order even when partially cached., Exactly one persist callback per newly solved interval; none for cache hits., on_interval_complete must fire once per interval (cached + solved alike). (+10 more)

### Community 83 - "Target Mode Tests"
Cohesion: 0.12
Nodes (19): _parse_target_args(), Tests for target mode: prediction rendering, validators, and race-start target-..., Intervals with all paths missing are skipped with a warning., Multiple intervals with different path availability are all processed. Interval..., Parse target-mode args through the centralized argparse tree., Race-start frame selection: endpoints present, sorted, unique, in-range., Outdated diagnostics schema raises a re-solve directive., Schema-5 diagnostics missing race_start_interval is an internal invariant... (+11 more)

### Community 84 - "Review Interval Severity"
Cohesion: 0.14
Nodes (19): classify_interval_severity(), _enforce_severity_gap(), _find_occlusion_exits(), generate_refinement_targets(), identify_weak_spans(), _midpoint_frame(), rank_key(), rank_target_frames_by_severity() (+11 more)

### Community 85 - "Velocity Model"
Cohesion: 0.14
Nodes (19): _compute_raw_pred_backward(), _compute_raw_pred_forward(), estimate_directional_size_slope(), estimate_directional_slope(), fit_interval_curves(), hermite_interpolate(), propagate_backward_analytical(), propagate_forward_analytical() (+11 more)

### Community 86 - "Seed Controller Tests"
Cohesion: 0.14
Nodes (13): _DummyFrameView, _DummyReader, _DummyWindow, _make_controller(), Tests for seed controller navigation behavior., Minimal frame reader stub for controller tests., Minimal frame view stub exposing fit-zoom state., Minimal window stub for key handling. (+5 more)

### Community 87 - "Solve Queue Driver"
Cohesion: 0.13
Nodes (18): execute_interval_work(), ExecutionContext, _format_interval_result(), _format_stage4_interval_result(), plan_interval_work(), _print_interval_result_rich(), Progress, Driver-side queue for interval solve work. Owns the main-process orchestration... (+10 more)

### Community 88 - "Paths Configuration"
Cohesion: 0.15
Nodes (18): _data_file_path(), default_camera_motion_path(), default_config_path(), default_diagnostics_path(), default_encode_analysis_path(), default_intervals_path(), default_seeds_path(), default_torso_box_coords_path() (+10 more)

### Community 89 - "Heat Map Overlay"
Cohesion: 0.12
Nodes (12): HeatMapOverlay, QObject, Sticky-mode ROI motion heat-map overlay in a QGraphicsScene. Owns four hidden..., Schedule the heat map to appear for `frame_index`. Always hides any currently-..., Hide the overlay and cancel any pending compute (toggle OFF path)., Cancel pending compute and FREEZE the current overlay image. Distinct from..., Remove overlay items from the scene. Safe to call twice., Debounce timer fired; run compute on the latest pending frame. (+4 more)

### Community 90 - "Keyboard Event Handler"
Cohesion: 0.14
Nodes (9): Handle keyboard events. Args: key: Qt key code. modifiers: Qt keyboard..., Accept current suggestion if available. Calls _accept_candidate() with the..., Accept a candidate from suggestion and create a seed. Args: candidate_idx:..., Process a drawn box. Args: box: Box as [x, y, w, h]., Save a seed and invoke the save callback. Args: seed: Seed dict to save., Compute a temporary step multiplier from held modifier keys. Alt multiplies by..., Mark runner as not in frame., Auto-accept average of FWD/BWD predictions if overlap sufficient. (+1 more)

### Community 91 - "print_step"
Cohesion: 0.17
Nodes (13): check_metadata(), get_dist_args(), list_dist_files(), print_step(), Path, Print a step header in cyan. Args: message: The step message to print., List distribution files in dist/. Args: dist_dir: Path to dist/. Returns: List..., Verify dist/ contains both wheel and sdist. Args: dist_dir: Dist directory. (+5 more)

### Community 92 - "Bin Target Table Tests"
Cohesion: 0.11
Nodes (17): Table-driven test for the floor-based default-bin selector. Asserts the human-..., Selector raises ValueError on non-positive inputs., Selector returns the approved bin_factor for each source width., 1080p (1920-wide) stays bin 1 under floor@1440., 1440p (2560-wide) stays bin 1 under floor@1440 (round would pick 2)., 4K (3840-wide) bins at 2 -> 1920-wide (1080p band)., Post-bin processed width equals source_width // selected bin_factor., Sources at or below target stay at bin_factor 1 (never upscaled). (+9 more)

### Community 93 - "Blob Walk Candidate Source"
Cohesion: 0.18
Nodes (17): gather_interval(), make_blob(), make_sink(), make_trace(), Behavioral test for the in-pipeline walker per-frame candidate source. Covers..., A frame with no surviving corridor blobs gathers to an empty list., An off-frame soft-miss (obs is None) gathers to an empty, aligned list., Gathered centroids stay PROCESSED full-frame, identity-preserved. The helper... (+9 more)

### Community 94 - "Blob Walk Coord Sentinel"
Cohesion: 0.16
Nodes (17): _build_geometry(), _processed_box_to_tile(), _project_processed_box_to_source(), WS2-C coordinate sentinel: hard gate over the walker box production path. A..., WS2-B1 conversion: processed box -> tile-local edge rectangle (once)., Assertion 1: the projected/persisted box scales to source full-frame., Assertion 2: reloaded npz box ~= projected source box within rounding., Assertion 3+4: tile rectangle == single processed-minus-roi conversion. (+9 more)

### Community 95 - "Observe Blob Contract"
Cohesion: 0.20
Nodes (17): _make_fake_reader(), _make_geometry(), _make_precomputed_store(), _make_scene_transform(), _proc_box_from_edges(), Tests for observe_blob_at processed-pixel contract (Option A, 2026-05-29)...., roi_override in processed coords is used as-is (no source->processed divide)...., dog_diameter_override in processed coords is passed directly to dog filter. At... (+9 more)

### Community 96 - "Seeds View Tests"
Cohesion: 0.21
Nodes (17): _make_geometry(), _make_source_seeds(), Tests for state_io.SeedsView and load_seeds_view (Option A, 2026-05-29)...., view built at bin=2 + assert against bin=4 geometry raises RuntimeError., view.source returns the original source-pixel seeds dict unchanged. Verifies..., view.seeds is computed once and the same list object is returned on repeat..., Build a minimal FrameGeometry for the given bin_factor., Build a minimal seeds dict in the state_io in-memory format. (+9 more)

### Community 97 - "Interval Fingerprint Bin Tests"
Cohesion: 0.12
Nodes (12): Behavioral test for the bin-invariant interval-fingerprint contract. Reuse..., build_geometry_tag(), build_solver_fingerprint_tag(), filter_usable_seeds_sorted(), migrate_legacy_fingerprints(), _prepare_usable_seed(), Low-level interval fingerprint and seed-filter helpers. Holds the fingerprint..., Strip the legacy `/bin<B>` suffix from bin-tagged store keys. bin_factor used... (+4 more)

### Community 98 - "Refine Mode Tests"
Cohesion: 0.25
Nodes (17): _build_seed_list(), _make_seed(), _minimal_intervals_file(), _minimal_seeds_json(), _prior_cache_for(), Regression tests for Track Runner contract C6. C6 requires that refine mode..., Write a minimal seeds JSON file and return its path., Return a minimal intervals_file dict with the given solved_intervals. (+9 more)

### Community 99 - "Residual Heat Map Tests"
Cohesion: 0.14
Nodes (15): _FakeReader, Behavioral test for the residual-motion heat-map display facade. The facade in..., _compute_roi must never return x1 > x2 or y1 > y2 even when the prediction is..., compute_residual_for_frame raises ValueError for a zero-area ROI (off-frame..., New overlay function returns BGRA with correct alpha channel. Alpha = 0 below..., WP-1C: lock compute_heat_map_roi output byte-identity via SHA256 hash. This..., In-memory video reader stub returning pre-built BGR frames., Store a list of BGR frames and expose VideoReader-compatible attrs. (+7 more)

### Community 100 - "Walk Coverage Tests"
Cohesion: 0.11
Nodes (17): Unit tests for count_post_seed_accepts and WalkCoverage...., Gate reads post_seed_accepted >= 1 for a healthy pass (no fallback)., Empty accepts list: Conant seed_1080_1111 FWD shape (total 0, post-seed 0)., Seed-only accepts: Conant seed_1126_1134 FWD shape (total 1, post-seed 0). This..., Bootstrap + many windowed accepts: Conant seed_1296_1327 FWD shape. Total 30..., Bootstrap missed, windowed step accepted: Conant seed_1134_1142 FWD shape...., BWD pass with seed at right endpoint: Conant seed_1126_1134 BWD shape. Total 3..., Duplicate non-seed entries each count once (counts appearances, not unique... (+9 more)

### Community 101 - "Walker Bin Factor Regression"
Cohesion: 0.16
Nodes (17): _make_fake_reader(), _make_geometry(), _make_scene_transform(), _proc_box_from_edges(), Regression tests for walker bin_factor coordinate consistency (Option A,..., Regression for bug #2 (override source-scale): dog_diameter at bin=4 is..., Regression for bug #3 (ROI_CLAMP_SPACE_MISMATCH): roi has x2 > x1 at bin=4. At..., SeedsView built at bin=1 raises RuntimeError when used with bin=4 geometry.... (+9 more)

### Community 102 - "Walker Bundle Seam Tests"
Cohesion: 0.17
Nodes (15): _make_seed(), _not_promoted_score(), _promoted_score(), Data-boundary tests for the Stage 4 walker input bundle seam (WP-5a). The Stage..., Negative boundary (paired): no Hermite raw_pred reachable via the bundle...., Stage-3-first: promotion is decided before any walker runs. When the Stage-3..., Fake walker callable that records every bundle it is handed. Stands in for the..., Build a minimal seed dict with the fields the bundle reads. (+7 more)

### Community 103 - "Video Benchmarking"
Cohesion: 0.16
Nodes (17): benchmark_video(), _format_size_mb(), main(), measure_scattered_seeks(), measure_sequential_run(), parse_args(), _percentile(), print_results() (+9 more)

### Community 104 - "Status Bar Controller"
Cohesion: 0.12
Nodes (9): Handle quit/done request. Subclass must implement., Short mode/state summary for the status bar. Subclass must implement. Returns:..., Handle keys common to all controllers. Handles ESC/Q, P (partial), A (approx),..., Toggle partial draw mode., Toggle approximate/obstruction draw mode., Sync toolbar button checked state with internal mode flags., Update the status bar to show active draw mode (partial/approx). Calls..., Set the status bar message text. Subclasses may override if they use a custom... (+1 more)

### Community 105 - "Seed Mode Controller"
Cohesion: 0.12
Nodes (10): Clean up seed-specific state (counters, etc)., Keybinding hints for the key hint overlay. Returns: String with keybinding..., Mode name for display. Returns: String "seed"., Manages the Seed mode annotation workflow. Handles keyboard shortcuts and mouse..., Get or create a YOLO detector instance. Lazy-loads the detector on first call...., Compute and store auto-seed suggestion for current frame. Runs YOLO detection..., Initialize the SeedController. Args: seed_frame_indices: List of frame indices..., Get all seeds collected. Returns: List of all seeds (existing + new). (+2 more)

### Community 106 - "Pyflakes Code Lint"
Cohesion: 0.16
Nodes (16): chunked(), collect_report(), collect_violations(), index_output_lines(), normalize_path(), fixture, parametrize, Run pyflakes once over all files and return per-file violation lines. Runs a... (+8 more)

### Community 107 - "README First Paragraph Tests"
Cohesion: 0.18
Nodes (15): _is_badge_only_block(), _load_first_paragraph(), Return repo name spellings considered "verbatim" for the no-name rule. Repo..., Return the full README.md text. Returns: str: README file contents., Replace Markdown links with their visible text only. Drops the URL portion so a..., Return True when a block consists only of image badges or links. Used to skip..., Load the first prose paragraph of README.md. Skips leading heading-only blocks..., _read_readme_text() (+7 more)

### Community 108 - "Shebangs Check"
Cohesion: 0.15
Nodes (16): check_file(), collect_report(), has_main_guard(), is_executable(), is_test_file(), fixture, parametrize, Detect whether a Python file is a test file. Checks if the filename matches... (+8 more)

### Community 109 - "Race Phases Tests"
Cohesion: 0.19
Nodes (15): _make_trajectory(), MockSceneTransform, Unit tests for race_phases.detect_race_start(). Tests use synthetic trajectory..., Short jitter burst, back to stationary, then real sustained motion., Runner gradually accelerates. Should detect onset with lower confidence., Identity transform: scene coordinates equal pixel coordinates., Build a trajectory list from (cx, cy) tuples. Args: positions: List of (cx, cy)..., Stationary runner for 60 frames, then moves right at constant speed. (+7 more)

### Community 110 - "Residual Motion Window Tests"
Cohesion: 0.12
Nodes (16): Tests for resolve_stride helper function. Tests the fps-invariant stride model:..., 60 fps anchor: stride=1 (byte-identical to legacy contiguous window)., fps <= 0 raises ValueError., fps=None raises ValueError., resolve_stride always returns a Python int., Sub-reference fps clamps to stride=1; well-above-reference fps yields stride>1., Higher fps -> stride >= lower fps stride (non-decreasing)., Number of neighbors is always 2 * DEFAULT_HALF_WINDOW = 8 regardless of fps. (+8 more)

### Community 111 - "CLI Argument Parsing"
Cohesion: 0.21
Nodes (16): _add_bin_arg(), _add_encode_args(), _add_gaps_arg(), _add_seed_interval_arg(), _add_severity_arg(), _add_top_arg(), _build_parser(), ArgumentParser (+8 more)

### Community 112 - "In-Box Heat Measurement"
Cohesion: 0.15
Nodes (14): measure_in_box_heat(), ndarray, In-box motion-cue heat primitive (shared coordinate-sensitive seam). This..., Measure motion-cue heat inside a PROCESSED torso box. Args: residual_dog:..., build_heat_movie_frame(), compute_fixed_heat_roi_size(), _draw_hot_mean_text(), _paste_composite_into_window() (+6 more)

### Community 113 - "Fingerprint Anti-Drift Tests"
Cohesion: 0.15
Nodes (15): _make_seed(), Anti-drift tripwire for interval fingerprint allow-list. Fingerprint allow-list..., Behavior check: the geometry tag encodes the geometry-affecting schema...., Behavioral sensitivity: redrawing a box at the same frame produces a different..., Behavioral sensitivity: the same box at a different frame produces a different..., Stability: a fixed seed pair always produces the same fingerprint format...., Build a minimal seed dict for fingerprint testing. Args: frame_index: Frame..., Shape gate: build_geometry_tag has zero parameters total (required or... (+7 more)

### Community 114 - "Frame Reader Null-Free Contract"
Cohesion: 0.15
Nodes (9): _FakeCapture, _make_reader(), Tests for FrameReader null-free contract. Verifies that read_frame() raises..., Minimal cv2.VideoCapture stand-in for unit testing., Construct a FrameReader backed by a fake capture., Requesting a frame past total_frames raises RuntimeError., A valid in-range read returns a numpy.ndarray, never None., test_read_frame_eof_raises_runtime_error() (+1 more)

### Community 115 - "In-Box Heat Sentinel Tests"
Cohesion: 0.17
Nodes (15): _bin4_arrays(), Sentinel tests for common_tools.in_box_heat.measure_in_box_heat. These pin the..., A pixel above threshold but validity_mask == 0 is excluded (mask authoritative)., A SourceBox handed to the primitive raises ValueError (require guard)., Return (residual_dog float32, validity_mask uint8) of given shape, all..., Only in-box pixels above threshold contribute to the mean and count., A box whose in-box pixels are all below threshold returns (None, 0)., A fractional box center floors to a NAMED region; only those pixels count. (+7 more)

### Community 116 - "Indentation Check"
Cohesion: 0.18
Nodes (15): check_file(), collect_report(), inspect_file(), multiline_string_lines(), fixture, parametrize, Path, Run indentation checks on one file and return any violations. Runs inspect_file... (+7 more)

### Community 117 - "Pytest Hygiene Check"
Cohesion: 0.17
Nodes (15): check_file(), check_no_banned_functions(), check_no_banned_module_assignments(), collect_report(), _keep_top_level_test(), fixture, Module, parametrize (+7 more)

### Community 118 - "Residual Motion Bin Factor Tests"
Cohesion: 0.18
Nodes (15): _compute_roi_override_bootstrap(), _make_fake_reader(), _make_scene_transform(), Tests for bin_factor correctness in residual_motion.py and walk_walker.py...., compute_residual_for_frame returns non-empty residual with bin_factor=4 after..., Replicate the ROI construction from walk_walker bootstrap/per-step. This..., ROI_CLAMP_SPACE_MISMATCH fix: roi_x2 must exceed roi_x1 at bin_factor=4. Pre-..., At bin_factor=1, source_width == reader.width, so the fix is a no-op. Verify... (+7 more)

### Community 119 - "Camera Motion Bin Tests"
Cohesion: 0.18
Nodes (15): _estimate_source_dx(), fixture, ndarray, Behavioral tests for camera_motion bin awareness. These tests assert the..., Write an MKV of a textured patch translating right by `dx_per_frame` source..., Run FixedZoomEstimator end-to-end and return source-frame dx array., test_bin1_and_bin2_agree_in_source_frame(), test_bin_change_invalidates_camera_motion_cache() (+7 more)

### Community 120 - "Review Tests"
Cohesion: 0.16
Nodes (15): _make_interval(), Unit tests for severity classification and interval ranking. Tests only..., Sorting by rank_key places pre_race intervals last., Build a minimal interval dict for severity and rank testing., Identity swap always produces high severity, regardless of other signals...., Lower agreement sorts before higher agreement (worst-first). Behavioral..., With agreement tied, confidence tier orders low < fair < good < high...., classify_interval_severity returns None for pre_race intervals. Pre-race... (+7 more)

### Community 121 - "Solver Integration Tests"
Cohesion: 0.17
Nodes (15): _make_seeds_linear_motion(), _make_synthetic_motion_track(), Integration tests for the analytical solver pipeline. Tests the full solve path..., Write v3 diagnostics with analytical scores and read back., Review module correctly reads confidence_tier from v3 scores., Analytical solve persists each interval through the callback hook., P0 regression: hermite-only solve must produce intervals with finite per-frame..., Create a zero-motion track for testing (stationary camera). (+7 more)

### Community 122 - "Walker Stall Fallback Tests"
Cohesion: 0.21
Nodes (14): _FakeReader, Per-pass walker stall fallback in solve_interval_analytical. The Stage-4 walker..., A pass with zero accepted walker frames uses its Hermite path., A bootstrap-only pass (seed frame accepted, all others missed) falls back. This..., A pass with post_seed_accepted >= 1 keeps the walker path., Minimal reader stub carrying a real FrameGeometry (bin_factor=1). The walker..., One full-span state dict carrying a source tag the test can read back., Stub curve fit, pre-pass, blend, and scoring; keep blend identity. blend_paths... (+6 more)

### Community 123 - "Track Tool Setup"
Cohesion: 0.19
Nodes (15): load_race_start_frame(), load_scene_transform(), load_seeds(), load_seeds_view(), normalize_video_basename(), open_reader(), Repo-root setup glue for the standalone blob_walk_v2 HTML tool. This module is..., Load seeds and wrap them in a SeedsView in PROCESSED-pixel coords. Args:... (+7 more)

### Community 124 - "Race Phase Detection"
Cohesion: 0.17
Nodes (15): _compute_confidence(), _compute_scene_velocities(), detect_race_start(), enumerate_seed_to_seed_intervals(), _estimate_stationary_baseline(), Race phase detection from solved trajectory. Post-hoc interpretation layer:..., Estimate the stationary velocity baseline from early frames. Uses the lowest..., Scan velocity series for race start onset. Uses sliding pre/post windows with... (+7 more)

### Community 125 - "Edit Mode Controller"
Cohesion: 0.12
Nodes (9): EditController, Clean up edit-specific state., Keybinding hints for the key hint overlay. Returns: String with keybinding..., Mode name for display. Returns: String "edit"., Get zoom center from current seed or predictions. Returns: Tuple of (cx, cy) or..., Set status via the StatusPresenter widget. Args: text: Message to display., Manages the Edit mode annotation workflow. Allows reviewing, filtering,..., Handle YOLO loading completion. (+1 more)

### Community 126 - "Status Presenter"
Cohesion: 0.16
Nodes (9): QLabel, Map status to a display color from overlay_styles.yaml. Args: status: Status..., Format interval_info reasons as compact human-readable labels. Args:..., Clear the status label and reset styling., Displays seed status information in the annotation toolbar. Updates status..., Initialize the StatusPresenter. Creates a monospace QLabel with styling for..., Get the status label widget. Returns: The QLabel widget for display in the..., Update the status label with seed information. Args: seed: Seed dict with... (+1 more)

### Community 127 - "SeedsView"
Cohesion: 0.14
Nodes (8): View over source-pixel seeds projected to a target FrameGeometry. Source-of-..., Bin factor from the geometry; int >= 1., Original source-pixel seeds dict; do not mutate., Seeds header passthrough (track_runner_seeds version and any extras)., Seed list with cx/cy/w/h in PROCESSED-pixel coords. Computed once on first..., Project source seeds to processed-pixel coords via geometry., Raise RuntimeError when geometry.bin_factor differs from view.bin_factor. Call..., SeedsView

### Community 128 - "Torso Size Stabilizer"
Cohesion: 0.21
Nodes (14): _apply_method(), hampel_filter_1d(), mad_gated_filter_1d(), median_filter_1d(), ndarray, Stabilize the per-frame torso h/w as a noisy observation. Contract clause C5..., MAD-gated median replacement. Variant of Hampel that uses the local median (not..., Dispatch to the named filter; preserve input on `none`. Falls back to the... (+6 more)

### Community 129 - "Track Detection"
Cohesion: 0.17
Nodes (11): create_detector(), ensure_yolo_weights(), ndarray, YOLO person detection for track_runner., Detect persons in a frame region-of-interest (ROI). Crops a region around a..., Create a YOLO person detector from config settings. Detection thresholds..., Get YOLOv8n ONNX weights from cache. Checks for the cached ONNX file. If..., Person detector using YOLOv8 ONNX model via OpenCV DNN. (+3 more)

### Community 130 - "Walker Adapter Tests"
Cohesion: 0.14
Nodes (7): Load and display the current seed frame., Recenter the view on the current seed bbox when zoomed in., Show the existing seed box on the frame., Process a drawn box. Args: box: Box as [x, y, w, h]., Keep seed as-is and advance., Go back to previous seed., Advance to next seed.

### Community 131 - "Blob Walk V2 Heat Movie Frame"
Cohesion: 0.22
Nodes (13): _install_fake_heat(), _make_seed(), M2-A: fixed-ROI heat-movie frame compositor + raw-BGR spill. These tests pin..., Reloaded raw bytes reshape to the same (roiH, roiW, 3) for several boxes., A compute_heat_map_roi-None frame still writes the fixed-shape byte count., Build a typed PROCESSED seed box., Monkeypatch compute_heat_map_roi to avoid any real video decode. When..., Window size derives from the larger seed by height, regardless of order. (+5 more)

### Community 132 - "Import Star Check"
Cohesion: 0.19
Nodes (13): check_file(), collect_report(), _find_import_star_matches(), _format_issue(), fixture, Module, parametrize, Enforce no import * usage repo-wide. (+5 more)

### Community 133 - "M1D Heat Not Computed Detection"
Cohesion: 0.19
Nodes (13): _make_live_trace(), M1-D measurement finding: verify that lighten_trace drops residual_dog. The..., Confirm: has_live_residual = (trace.residual_dog is not None). This mirrors..., A manifest with no heat keys still passes the two gates (C13 rework). Heat is..., Build a minimal 'live' trace with a non-None residual_dog array., lighten_trace always sets residual_dog=None -- confirmed design choice., lighten_trace also drops validity_mask, mirroring residual_dog., lighten_trace preserves corridor_blobs, winner_blob, roi_origin_xy. (+5 more)

### Community 134 - "Solve Default Bin Tests"
Cohesion: 0.14
Nodes (13): Tests for the solve-path default bin resolver in cli._resolve_solve_bin_factor...., No --bin/--auto-bin: bin resolves from source width (floor @ 1440)., Explicit --bin N is used as-is, ignoring source dims., --bin 1 forces full resolution on a 4K source., --auto-bin HEIGHT keys on source HEIGHT, not width., Bare --auto-bin (sentinel -1) resolves the same bin as no-flag default. Bare..., Sub-1 --bin and sub-1 --auto-bin target raise ValueError., test_auto_bin_keeps_height_based_meaning() (+5 more)

### Community 135 - ".eventFilter"
Cohesion: 0.17
Nodes (6): Process a completed drawn box. Subclass must implement. Args: box: Box as [x,..., Handle keyboard events. Subclass must implement. Args: key: Qt key code...., Handle window and viewport events. Args: obj: Object that received the event...., Handle mouse button press. Args: scene_x: Scene x coordinate. scene_y: Scene y..., Handle mouse move. Args: scene_x: Scene x coordinate. scene_y: Scene y..., Handle mouse button release. Args: scene_x: Scene x coordinate. scene_y: Scene...

### Community 136 - "Walker Adapter Behavioral Tests"
Cohesion: 0.21
Nodes (13): _bundle(), _fake_summary(), _patch_walker(), Behavioral tests for the Stage 4 walker adapter (WP-5b). The adapter..., An early-stop walk is padded so the path covers the whole interval. The walker..., Minimal seed dict with the fields the bundle and adapter read., Build a WalkerInputBundle directly (reader/transform are unused fakes)., Stand-in for WalkSummary: the adapter only reads .direction_path. (+5 more)

### Community 137 - "Walk HTML Generation"
Cohesion: 0.20
Nodes (13): _classify_interval(), _compute_fwd_bwd_agreement_px(), _compute_interval_stats(), _compute_walk_quality(), IntervalSummaryStats, Generate walk.html: per-interval power-of-2 sampled walk view with dense-fill..., Classify one interval from its FWD/BWD accepted fractions. A missing (None)..., Median per-frame FWD/BWD distance over frames accepted in BOTH passes. Per... (+5 more)

### Community 138 - "Residual Pre-Pass"
Cohesion: 0.19
Nodes (12): _build_rois_for_frame(), _center_in_frame(), _compute_center(), precompute_interval_residuals(), Per-worker per-interval sequential residual pre-pass. Eliminates scattered..., Return True when the processed-space center lies within the frame bounds. Uses..., Pre-compute per-frame ROI tuples once before the walk. Builds a dict keyed by..., Convert source-frame coords to processed-frame coords if binned. Args: cx:... (+4 more)

### Community 139 - "Crop Trajectory"
Cohesion: 0.14
Nodes (11): compute_crop_trajectory(), create_crop_controller(), CropController, parse_aspect_ratio(), Compute a smoothed crop rectangle for each frame in a trajectory. Creates a..., Parse an aspect ratio string into a float. Args: aspect_str: Ratio string like..., Adaptive crop controller that smoothly follows a tracked target. Uses..., Update crop position given a tracking state dict. Args: state: Tracking state... (+3 more)

### Community 140 - "Overlay Management"
Cohesion: 0.15
Nodes (7): Add an overlay item to the scene and tracking list. Args: item: QGraphicsItem..., Remove an overlay item from the scene and tracking list. Args: item:..., Update FWD/BWD/blended/consensus prediction overlays on the scene., Toggle temporary prediction overlay suppression for current frame. Suppression..., Apply three-layer visibility model to prediction overlays. visible = available..., Set persistent visibility for a specific overlay type. Args: key: Overlay key..., Route the current heat visibility flag to the heat overlay. Called from...

### Community 141 - "Key Event Handling"
Cohesion: 0.14
Nodes (7): Handle keyboard events. Args: key: Qt key code. modifiers: Qt keyboard..., Delete the current seed., Change seed status (only not_in_frame supported). Args: new_status: New status..., Jump forward 10% of the filtered seed list., Jump backward 10% of the filtered seed list., Jump to the next low-confidence seed after the current position. Searches..., Enter seed-add mode via SeedController. Saves the current frame position,...

### Community 142 - "solver_workers.py"
Cohesion: 0.19
Nodes (12): ProcessPoolExecutor, make_pool(), Per-interval parallel solver execution. The analytical solver in..., Close the worker's reader on process exit., Solve one interval inside a pool worker. Takes a tiny pickleable task tuple and..., Create a ProcessPoolExecutor configured with `_worker_init`. The heavy run-..., Run-invariant state for a single worker process. Constructed once per worker by..., Initialize per-process solver state for a pool worker. Runs exactly once per... (+4 more)

### Community 143 - "Graphics Items"
Cohesion: 0.15
Nodes (12): QGraphicsRectItem, QPainter, QRectF, Create hidden overlay items and attach them to the scene. Args: is_drawing_fn:..., PreviewBoxItem, Expand the bounding rect upward to include the label pill. Without this, Qt's..., Paint with dark outline behind the colored border for contrast. Args: painter:..., A semi-transparent preview box for user confirmation. Represents a proposed box... (+4 more)

### Community 144 - "_YoloLoaderThread"
Cohesion: 0.15
Nodes (8): QThread, Background thread for loading YOLO detector. Loads YOLO weights in a non-..., Initialize the YOLO loader thread. Args: detector_list: Mutable list [None] to..., Load YOLO detector in background thread., Run YOLO polish on current seed and show preview., Start background YOLO loading in QThread., Initialize the EditController. Args: work_seeds: Mutable list of all seeds..., _YoloLoaderThread

### Community 145 - "test_bandit_security.py"
Cohesion: 0.19
Nodes (12): collect_report(), collect_violations(), format_result_line(), fixture, parametrize, Autouse fixture: clear stale reports, populate VIOLATIONS_BY_FILE, write..., Enforce no medium-or-higher bandit security findings repo-wide., Run bandit ONCE over all files and return the parsed JSON report. Uses the same... (+4 more)

### Community 146 - "test_tr_residual_motion_bin.py"
Cohesion: 0.23
Nodes (10): _observe_at_geometry(), _patch_inner_pipeline(), Behavioral tests for observe_blob_at's SOURCE-space return under bin. These..., Helper: run observe_blob_at with the given geometry and stubs. proc_pred_xy /..., Minimal reader stub matching the observe_blob_at contract., Stub residual_motion's inner pipeline to deliver a single blob. `blob_proc_xy`..., _StubReader, test_observe_blob_at_cache_key_is_processed_frame() (+2 more)

### Community 147 - "test_whitespace.py"
Cohesion: 0.18
Nodes (12): check_file(), _check_whitespace_bytes(), collect_report(), Config, fixture, parametrize, Repo-wide whitespace hygiene: BOM, CRLF, trailing whitespace, missing final..., Fail on whitespace issues in tracked text files. (+4 more)

### Community 148 - "Version Bump and Upload"
Cohesion: 0.20
Nodes (10): commit_version_bump(), has_tracked_changes(), Return True if git has tracked changes., Commit the version bump if there are tracked changes., Tag and push the version., Upgrade build and upload tools. Args: python_exe: Python executable...., Run a command and fail on error. Args: args: Command arguments. cwd: Working..., run_command() (+2 more)

### Community 149 - "Main Function"
Cohesion: 0.17
Nodes (12): extract_project_metadata(), main(), parse_args(), Namespace, Parse command line arguments. Returns: The parsed arguments., Load pyproject.toml into a dict. Args: pyproject_path: Path to pyproject.toml...., Extract package name and version from pyproject data. Args: pyproject_data:..., Resolve the index URL based on repo. (+4 more)

### Community 150 - "Project URL and Python Version Checks"
Cohesion: 0.25
Nodes (8): open_project_url(), print_warning(), Ensure the running Python satisfies requires-python., Run pytest if it is installed., Print a warning message in yellow. Args: message: The warning message to print., Open the project URL in a browser when possible. Args: url: The URL to open., require_pytest_passes_if_available(), require_python_version()

### Community 151 - "Git and Twine Checks"
Cohesion: 0.17
Nodes (12): Run a command and return the result, even if it fails. Args: args: Command..., Ensure the git working tree has no staged or unstaged changes., Ensure the release is on the main branch., Ensure the git tag for the version exists., Ensure twine is installed and runnable., Ensure local main is synced with origin/main., require_git_clean(), require_main_branch() (+4 more)

### Community 152 - "AST Import Tests"
Cohesion: 0.12
Nodes (14): AST, Import, ImportFrom, AST scan test: blob_walk_v2 must not import Hermite-related modules. Enforces..., Scan the relocated walker core and deny Hermite imports. The walker core now..., Scan walker_bundle.py and deny Hermite imports. walker_bundle.py is the Stage-4..., test_blob_walk_v2_no_hermite_import(), test_walker_bundle_no_hermite_import() (+6 more)

### Community 153 - "ASCII Compliance Check"
Cohesion: 0.23
Nodes (11): check_ascii_compliance(), find_non_latin1_chars(), main(), parse_args(), Namespace, Run the ISO-8859-1/ASCII compliance check. Returns: int: Process exit code., Read UTF-8 text from a file. Args: input_file: Path to the file. Returns:..., Find non-ISO-8859-1 characters in text. Args: text: Input text. Returns:... (+3 more)

### Community 154 - "Coordinate Space Tests"
Cohesion: 0.17
Nodes (11): Unit tests for common_tools/coord_space.py typed coordinate primitives...., ProcessedBox must not have a to_processed method., SourceBox must not have a to_source method., require_source_point returns the same object when given a SourcePoint., require_processed_point returns the object when given a ProcessedPoint., SourceBox.edges() midpoint == (cx, cy) and span == (w, h)., test_processed_box_has_no_to_processed(), test_require_processed_point_passes_correct_type() (+3 more)

### Community 155 - "Geometry and Point Tests"
Cohesion: 0.17
Nodes (12): _make_geometry(), ProcessedPoint just inside [0, processed_width) x [0, processed_height) -> True., ProcessedPoint at exactly processed_width -> False (half-open interval)., ProcessedPoint at exactly processed_height -> False (half-open interval)., bin=4, non-square, non-aligned: SourceBox round-trips within bin_factor..., bin=4: ProcessedBox -> SourceBox scales by exactly bin_factor, not bin^2. A..., Build a real FrameGeometry via the production _resolve_frame_geometry path...., test_in_bounds_at_processed_height_is_false() (+4 more)

### Community 156 - "Fast-Read File Handling"
Cohesion: 0.20
Nodes (10): _make_original(), Create an original .mkv file on disk and return its path., No fast-read file -> working_decode is the original with the absent reason., final_encode and metadata_identity always select the original (absent case)., Present + valid fast-read -> working_decode is the fast-read path., Validation runs exactly once per resolve call (present + valid)., test_resolve_absent_fastread_uses_original(), test_resolve_absent_final_and_identity_are_original() (+2 more)

### Community 157 - "Import Dot Check"
Cohesion: 0.20
Nodes (11): check_file(), collect_report(), format_issue(), fixture, Module, parametrize, Format a report line for a relative from-import statement. Args: rel_path:..., Return violations for any relative from-import in the parsed module. Scans... (+3 more)

### Community 158 - "Crop Size Stabilizer Tests"
Cohesion: 0.35
Nodes (10): _config(), _crop_heights(), ndarray, _ramp_traj(), C5 behavior guard for robust torso-size spike hardening in the crop. The crop..., _stable_traj_with_spikes(), test_multi_frame_ramp_still_tracked_direct_center(), test_single_frame_spike_rejected_direct_center() (+2 more)

### Community 159 - "Solve Queue Format Tests"
Cohesion: 0.21
Nodes (11): _make_stage3_result(), _make_stage4_result(), Unit tests for solve_queue interval result formatters. Locks the per-interval..., Stage 4 formatter must omit delta parentheticals when baseline is None., Build a minimal Stage 3 v3 interval result dict., Build a minimal Stage 4 v3 interval result dict., Stage 3 formatter must not emit blob_accept and must end with [stage3]., Stage 4 formatter must include delta, confidence label, [stage4] tag. (+3 more)

### Community 160 - "Motion Gate Tests"
Cohesion: 0.21
Nodes (11): bootstrap_search_radius_w(), clamp_velocity_w_per_s(), evaluate(), max_runner_jump_per_frame(), MotionGateResult, Motion gate for blob walker v2: per-frame jump acceptance gate. The motion gate..., Derive the per-frame max runner displacement from the physical envelope. Args:..., Clamp a velocity magnitude (W/s) to the physical envelope. Args: v_w_per_s:... (+3 more)

### Community 161 - "Frame Scrubbing"
Cohesion: 0.17
Nodes (6): Load and display the current frame., Recenter the view on the prediction center when zoomed in., Scrub backward by the current step size times multiplier. Args: multiplier:..., Handle toolbar previous button clicks., Scrub forward by the current step size times multiplier. Args: multiplier:..., Handle toolbar next button clicks.

### Community 162 - "Package Build and Logging"
Cohesion: 0.18
Nodes (11): build_package(), format_bytes(), print_info(), CompletedProcess, Run a command and write stdout/stderr to a log file., Print a normal info message. Args: message: The info message to print., Format byte counts for human-readable output. Args: size_bytes: Size in bytes...., Print dist files with sizes. Args: dist_dir: Path to dist/. (+3 more)

### Community 163 - "Anchor Interpolation Tests"
Cohesion: 0.18
Nodes (11): range, anchor_to_seeds(), _build_local_fit(), _collect_anchor_knots(), _eval_fit(), Collect trusted knots from seeds for anchor interpolation. Filters to..., Build local interpolators from a subset of knots near center_frame. Selects up..., Evaluate interpolators at a given frame index. Handles both callable... (+3 more)

### Community 164 - "Walker Neighbor Reached Tests"
Cohesion: 0.27
Nodes (8): Unit tests for the walker neighbor-seed crossing predicate. Covers the P12..., Replay the walker stepping loop using only the crossing predicate. Mirrors..., test_span_smaller_than_stride_terminates_immediately(), test_stride1_fwd_walk_observes_interior_only(), test_stride2_even_span_lands_exactly_clamp_is_noop(), test_stride2_odd_span_crossing_detected_and_clamped_bwd(), test_stride2_odd_span_crossing_detected_and_clamped_fwd(), walk_frames()

### Community 165 - "Walker Flag Routing Tests"
Cohesion: 0.27
Nodes (10): Flag-routing test for the Stage 4 walker (WP-5b). solve_interval_analytical..., Default (blob_pass off): the pure-Hermite propagators produce the paths., blob_pass on (reader present): the walker produces the paths., Reader stub carrying a real bin_factor=1 FrameGeometry. The walker branch reads..., Stub curve fit, pre-pass, blend, and scoring; record path producers. Each..., _reader(), _seed(), _stub_heavy() (+2 more)

### Community 166 - "Utility Functions"
Cohesion: 0.20
Nodes (10): Random, Shared generic helpers for blob_walk_v2 modules. Small conversion and selection..., Safely convert CSV string to float., Safely convert CSV string to int., Convert CSV string to float, or None if empty/invalid., Pick up to n random intervals where both bracketing seeds are visible. Filters..., select_random_visible(), _to_float() (+2 more)

### Community 167 - "Mode Documentation Refresh"
Cohesion: 0.25
Nodes (10): get_help_text(), get_repo_root(), main(), normalize_help_text(), Get repository root using git., Run track_runner.py <mode> -h and capture output., Normalize help text for idempotency., Refresh docs/modes/<MODE>.md with help text for mode. (+2 more)

### Community 168 - "Box Utility Functions"
Cohesion: 0.18
Nodes (10): center_to_corners(), clamp_box_to_frame(), compute_iou(), draw_transparent_rect(), ndarray, Shared geometric and drawing utilities for bounding boxes. All bounding boxes..., Draw a filled rectangle with alpha blending and a solid border. Modifies frame..., Convert center-format box to corner coordinates. Pure float arithmetic with no... (+2 more)

### Community 169 - "Seed Color Utilities"
Cohesion: 0.20
Nodes (10): _build_seed_dict(), detection_to_torso_box(), normalize_seed_box(), ndarray, Seed-assistance utilities. Provides helpers for user seeding: normalizing user-..., Normalize an inconsistently-drawn seed box. Enforces minimum dimensions and..., Build a canonical v3 seed dict with derived geometry attached. Canonical on-..., Extract upper 60% of detection bbox as torso region. Args: bbox: Bounding box... (+2 more)

### Community 170 - "Toolbar and Step Display"
Cohesion: 0.18
Nodes (6): QWidget, Format the current step size for display. Returns: String like "2f (0.07s)"..., Double the scrub step in frames, ceiling at fps*10., Halve the scrub step in frames, floor at 1 frame., Update the step label in the toolbar and window title., Build the toolbar widget with nav and draw mode buttons. Returns: QWidget...

### Community 171 - "Zoom Control Widget"
Cohesion: 0.20
Nodes (7): QWidget, Zoom control widget for the track runner status bar., Horizontal zoom control bar with buttons, label, and slider. Provides zoom..., Initialize ZoomControls. Args: parent: Parent widget., Forward slider value changes as zoom_slider_changed signal. Args: value: Slider..., Update label and slider to reflect the current zoom percentage. Blocks slider..., ZoomControls

### Community 172 - "Script Files"
Cohesion: 0.20
Nodes (6): encode_all.sh script, re-solve.sh script, run_random_walk.sh script, PYTHONDONTWRITEBYTECODE, PYTHONUNBUFFERED, source_me.sh script

### Community 173 - "Debug Log Tests"
Cohesion: 0.20
Nodes (9): Tests for walk_debug_log.DebugLogWriter and DebugLogRow. Covers CSV value..., Use DebugLogWriter as a context manager. After the with block, assert file..., Write one row, read it back; the written values survive the CSV round-trip., Write a row with most optional fields None. Read CSV. Assert empty cells for..., Construct writer, attempt write_row with invalid status. Assert ValueError..., test_context_manager_closes_file(), test_invalid_status_raises_value_error(), test_none_fields_render_blank() (+1 more)

### Community 174 - "Fake Frame Reader"
Cohesion: 0.20
Nodes (5): _FakeFrameReader, Minimal FrameReader stand-in: context manager, read_frame returns a sentinel...., _smoke_read_fastread seeks to the tail start and reads contiguously to the last..., Record the seek start position for tail-read behavior assertions., test_smoke_read_reads_tail_sequentially_through_last_frame()

### Community 175 - "Solver Worker Blob Pass Tests"
Cohesion: 0.27
Nodes (9): _make_context(), Worker honors WorkerContext.blob_pass when solving an interval. Placement: a..., Build a WorkerContext with stub run-invariant state. Only blob_pass is..., Invoke the worker once and return the blob_pass kwarg it routed., A blob_pass=True context drives the Stage-4 walker pass., A blob_pass=False context keeps Stage-3 pure Hermite., _run_worker_capturing_blob_pass(), test_worker_routes_blob_pass_false() (+1 more)

### Community 176 - "Legacy Store Reuse Tests"
Cohesion: 0.27
Nodes (9): _build_legacy_store(), Reuse proof on a synthetic legacy-store fixture. Proves that a store whose keys..., A store keyed at bin1 fully reuses after migration, no intervals pending. The..., Legacy keys at different bins become byte-identical after migration. A solve at..., Build a synthetic solved-results dict keyed with a trailing bin suffix...., A store keyed at bin4 fully reuses after migration, no intervals pending...., test_bin4_and_bin1_migrate_to_same_keys(), test_legacy_store_fully_reuses_after_migration_bin1() (+1 more)

### Community 177 - "Schema Version Tests"
Cohesion: 0.24
Nodes (9): Drift gate for contract C10: single source of truth for SCHEMA_VERSION. The..., Every legacy alias must be assigned exactly from SCHEMA_VERSION., No module outside tr_schema.py may define a schema-authority constant. Catches..., Governance tripwire: SCHEMA_VERSION must match the approved pin. Changing..., Yield (filepath, line_no, line_text) for every line in production code., _scan_python_files(), test_legacy_aliases_equal_schema_version(), test_no_shadow_schema_authority_constants() (+1 more)

### Community 178 - "Solve Mode Tests"
Cohesion: 0.24
Nodes (9): _make_test_fixture(), End-to-end smoke test for all three solve modes (M7 closure). Exercises the..., The unified GEOMETRY_TAG encodes only the geometry-affecting schema., Same inputs produce the same blended trajectory (round-trip determinism)., Create a minimal multi-interval setup for smoke testing. Returns: (seeds,..., All three modes produce complete interval results with finite trajectories., test_cache_hit_on_rerun_same_mode(), test_geometry_tag_encodes_schema_version() (+1 more)

### Community 179 - "Stage 4 Parity Tests"
Cohesion: 0.20
Nodes (9): Tests for Stage-4 interval promotion selection. Covers..., select_promoted_intervals picks only low and fair confidence tiers., Pre-race intervals are never promoted (Contract C4)., None entries (e.g. quit in progress) are skipped without raising., PROMOTION_TIERS includes the two tiers that should be re-solved., test_promotion_tiers_contains_low_and_fair(), test_select_promoted_intervals_excludes_pre_race(), test_select_promoted_intervals_filters_low_fair() (+1 more)

### Community 180 - "Regime Policies"
Cohesion: 0.24
Nodes (9): _find_span_index(), get_frame_params(), _get_regime_params(), get_size_mode_multiplier(), Regime policy mapping for smart crop mode. Maps regime labels to exactly 2 crop..., Find the span index that contains a given frame. Args: frame_index: Frame index..., Get the max_height_change multiplier for a size_update_mode. Args:..., Get the default parameters for a regime. Args: regime: Regime label ('clear',... (+1 more)

### Community 181 - "Video Identity Fingerprinting"
Cohesion: 0.22
Nodes (9): _check_rule(), compare_video_identity(), make_video_identity(), tr_video_identity.py Video identity fingerprinting for track_runner data files...., Compare stored video identity against current video identity. Returns a dict..., Format comparison result dict as human-readable multi-line string. Takes the..., Apply one comparison rule; return mismatch message or None. Both `stored` and..., Build a video identity dict from file metadata and probe info. Args:... (+1 more)

### Community 182 - "Log Capture Fixture"
Cohesion: 0.22
Nodes (9): LogCaptureFixture, _make_two_probe_patcher(), Patch probe_video to return two distinct probes on consecutive calls., fps_mismatch_fatal=False: 59.94 vs 60.0 mismatch warns-and-continues. The..., fps_mismatch_fatal=True (default): same fps mismatch raises RuntimeError. The..., frame_count mismatch raises even with fps_mismatch_fatal=False. The fps..., test_fps_mismatch_fatal_false_does_not_raise_warns(), test_fps_mismatch_fatal_true_raises() (+1 more)

### Community 183 - "Action Helpers"
Cohesion: 0.22
Nodes (7): QAction, StandardPixmap, make_action(), QObject, Action helpers for track runner UI. Provides factory functions for creating..., Create a standardized QAction. Creates a QAction with optional icon support and..., Initialize the AnnotationWindow. Args: title: Window title to display....

### Community 184 - "Wheel Event Handling"
Cohesion: 0.33
Nodes (4): QWheelEvent, Detect whether a wheel event came from the trackpad. macOS trackpad events..., Expand scene rect so scroll bars have range for panning. Adds 2% of the image..., Handle mouse wheel zoom and trackpad pan events. Trackpad two-finger swipe pans...

### Community 185 - "Walk HTML Generation"
Cohesion: 0.28
Nodes (9): build_walk_html(), Path, Return {frame_index: row_dict} for the given debug log CSV., Return (width, height) of PNG, or None if unreadable. Reads the IHDR chunk..., Render a per-interval trajectory PNG using matplotlib. Plots accepted FWD/BWD..., Walk the blob_walk_v2/ output directory and write walk.html. Args: run_root:..., _read_debug_log_csv(), _read_png_size() (+1 more)

### Community 186 - "Torso Box Coordinates"
Cohesion: 0.22
Nodes (9): _extract_source_box_coords(), ndarray, Write a dict of numpy arrays to an NPZ file atomically. Shared helper used by..., Extract (cx, cy, w, h) floats from a SOURCE-space frame box object. Accepts..., Round float coords to nearest int, clip to [0, 65535], cast to uint16. Per..., Write unified torso box coordinates to an NPZ file. Extracts forward_path,..., _round_clip_uint16(), _write_npz_atomic() (+1 more)

### Community 187 - "Off-Center Crop Error"
Cohesion: 0.22
Nodes (7): _diagnose_offcenter_cause(), OffCenterCropError, Return (edge_tag, explanation) describing why the crop is off-center. Pure..., Raise OffCenterCropError when the runner stays outside the safe central crop..., Initialize the crop controller. Args: frame_width: Width of the source video..., Raised when the runner exits the safe central crop window for longer than the..., validate_torso_within_central_window()

### Community 188 - "Version and Venv Checks"
Cohesion: 0.17
Nodes (12): check_version_exists(), get_venv_python(), normalize_version_string(), parse_pip_versions_output(), Return the normalized PEP 440 version string., Parse pip index versions output. Args: output: Combined stdout and stderr from..., Check if a version already exists on the repository. Args: python_exe: Python..., Get the python executable in a venv. Args: venv_dir: Path to the venv... (+4 more)

### Community 189 - "Fast-Read Context"
Cohesion: 0.33
Nodes (6): Build a VideoContext whose working_decode is a valid fast-read. Creates the..., Seed artifact path uses the original stem even when decode is fast-read., Config artifact path uses the original stem even when decode is fast-read., test_config_path_stays_original_under_fastread_context(), test_seeds_path_stays_original_under_fastread_context(), _valid_fastread_context()

### Community 190 - "Status Presenter Tests"
Cohesion: 0.32
Nodes (7): Tests for StatusPresenter severity-badge rendering. Locks in the contract C4..., Minimal seed dict accepted by StatusPresenter.update., interval_info with severity=None must not crash and shows PRE-RACE., A classified severity routes through overlay_config.get_severity_style., _seed(), test_classified_severity_uses_overlay_config_style(), test_pre_race_severity_none_renders_pre_race_badge()

### Community 191 - "Walk HTML V2"
Cohesion: 0.32
Nodes (7): main(), parse_args(), process_video(), Namespace, Path, Walk + render the sampled intervals of one video. Returns counters dict., Parse command-line arguments for a single-video walk.

### Community 192 - "Audit Rule Extraction"
Cohesion: 0.25
Nodes (8): _determine_walker_stop_row(), _extract_audit_rule_name(), Determine the last row the walker visited (max or min frame_index). Returns the..., Extract audit rule name when modes disagree. If audit_winner_rule is present..., Build per-tile caption with frame_index, status, stop_reason always included,..., Build per-cell HTML for one column (FWD or BWD). Include power-of-2 offsets +..., _render_cell_caption(), _render_column_cells()

### Community 193 - "Seed Geometry Derivation"
Cohesion: 0.25
Nodes (8): _derive_seed_geometry(), load_seeds(), load_seeds_view(), Remove banned and derivable fields from a seed dict in place. Strips every key..., Derive cx/cy/w/h from torso_box and attach in memory. Called after..., Load a seeds JSON file and normalize to the in-memory v3 shape. Accepts headers..., Load seeds and return a SeedsView projected to the target geometry. Source-of-..., _strip_legacy_seed_fields()

### Community 194 - "Zoom Control"
Cohesion: 0.25
Nodes (4): Get center of best prediction for the current frame. Prefers the REFINED..., Update the zoom scale bar display., Cycle zoom: fit -> 1x -> 1.5x -> 2.25x -> 3.375x -> 5x -> 8x -> 12x -> fit...., Get zoom center point. Subclasses may override. Default uses prediction center....

### Community 195 - "Polish Preview"
Cohesion: 0.25
Nodes (4): Run FWD/BWD consensus polish and show preview., Show a polish preview box as a QGraphicsItem. Args: refined: Refined box dict..., Clear the polish preview item from the scene., Accept the polish preview and update seed.

### Community 196 - "Target Controller"
Cohesion: 0.25
Nodes (5): Seed collection controller for track runner annotation. Manages the Seed mode..., Target collection controller for track runner annotation. Manages the Target..., Manages the Target mode annotation workflow. Inherits all functionality from..., Initialize the TargetController. Args: sorted_targets: List of frame indices to..., TargetController

### Community 197 - "Git Repository Checks"
Cohesion: 0.29
Nodes (7): ensure_in_git_repo(), get_git_root(), CompletedProcess, Run a git command and return the completed process. Args: args: Argument list..., Return the absolute path of the git repository root. Returns: Absolute path of..., Raise if the current working directory is not inside a git work tree. Raises:..., run_git()

### Community 198 - "Icon and QPixmap Conversion"
Cohesion: 0.29
Nodes (6): QIcon, QPixmap, _bgr_to_pixmap(), ndarray, Convert a BGR uint8 image to a detached QPixmap. The QImage constructor shares..., Create a small colored swatch icon for toolbar actions. Args: hex_color: Hex...

### Community 199 - "Confidence Decay Tests"
Cohesion: 0.33
Nodes (6): _make_decay_fixture(), Shared six-frame interval fixture for confidence-decay tests., FWD confidence peaks at slot 0 and monotonically decreases., BWD confidence peaks at slot n-1 and monotonically decreases. Locks the "BWD..., test_bwd_confidence_decays_from_end(), test_fwd_confidence_decays_from_start()

### Community 200 - "Analysis & Solver"
Cohesion: 0.33
Nodes (6): _minimal_analysis(), _minimal_solver_context(), Return the smallest analysis dict the YAML writer indexes., Return the smallest solver_context dict the YAML writer indexes., write_analysis_yaml emits canonical_source and decode_source verbatim., test_analyze_yaml_carries_source_fields()

### Community 202 - "Crop Assertions"
Cohesion: 0.60
Nodes (5): _config(), Guards for trajectory_to_crop_rects input bounds. Locks in P2-1 from..., test_raises_when_trajectory_longer_than_total_frames(), test_shorter_trajectory_pads_via_hold_state(), _video_info()

### Community 203 - "Offset Calculations"
Cohesion: 0.33
Nodes (6): _build_sampled_offsets(), _compute_dense_fill_offsets(), _powers_of_two_up_to(), Return [0, 1, 2, 4, 8, ...] up to max_offset., Return the list of offsets to fill between walker_stop_row and..., Build the list of offsets to sample: power-of-2 + dense-fill in failure...

### Community 204 - "CLI Help"
Cohesion: 0.47
Nodes (5): find_subparsers(), main(), print_header(), ArgumentParser, Return ordered {name: subparser} dict from a parser with subcommands.

### Community 205 - "Review Summary"
Cohesion: 0.33
Nodes (6): format_review_summary(), get_confidence_label(), needs_refinement(), Extract confidence label from v2 or v3 interval score. Args: score: Interval..., Produce a human-readable summary of all intervals with scores and suggestions...., Return True if any interval has low or fair confidence. Only low and fair tiers...

### Community 206 - "Schema Validation"
Cohesion: 0.33
Nodes (5): is_supported_artifact_schema(), latest_geometry_affecting_schema(), Single source of truth for track-runner schema version and policy. Everything..., Return True iff `version` is a readable schema for `artifact`. Loaders should..., Highest geometry-affecting schema <= SCHEMA_VERSION. Used by the geometry-cache...

### Community 207 - "Edit Mode"
Cohesion: 0.33
Nodes (3): Resume edit mode after returning from add-seed mode. Args: new_seeds: List of..., Rebuild filtered indices after seed list changes. Sorts work_seeds in place by..., Restore nav position to first seed at or after saved frame.

### Community 208 - "Frame Title"
Cohesion: 0.33
Nodes (3): Update window title with frame, step, zoom, and interval quality info., Build a short quality string from the current frame's interval info. Returns:..., Build a human-readable targeting reason from interval info. Returns: String...

### Community 209 - "Prediction Legend"
Cohesion: 0.50
Nodes (3): PredictionLegendItem, Compact legend showing prediction overlay colors and line styles. Positioned in..., Move the legend to the corner farthest from the tracked box. Uses setPos() for...

### Community 210 - "Bug Reproduction"
Cohesion: 0.50
Nodes (4): main(), Run the reproduction and map the outcome to an exit code., Drive the batch call path that triggers bug #101. Mirrors..., reproduce_degenerate_roi()

### Community 211 - "Parameterization"
Cohesion: 0.40
Nodes (5): parametrize, SourcePoint -> ProcessedPoint -> SourcePoint is identity within rounding., SourceBox -> ProcessedBox -> SourceBox is identity within rounding., test_source_box_round_trip(), test_source_point_round_trip()

### Community 212 - "Crop Alpha Tests"
Cohesion: 0.60
Nodes (4): _base_config(), Crop-rect equivalence guard for direct_center size smoothing. The crop SIZE..., _synthetic_trajectory(), test_direct_center_rects_match_old_config_alphas()

### Community 213 - "Prompt Building"
Cohesion: 0.50
Nodes (4): build_choice_prompt(), confirm(), Build a colored y/N prompt string. Args: prompt: Base prompt text. Returns: The..., Ask the user to confirm via a y/N prompt. Args: prompt: Prompt text shown...

### Community 214 - "Step Cap Test"
Cohesion: 0.50
Nodes (3): Test rejection when actual_jump > per_step_cap (but < absolute_cap)., Reject when per_step_cap is the binding constraint. Set dt_frames high (large..., TestPerStepCapReject

### Community 215 - "Path Enumeration"
Cohesion: 0.50
Nodes (4): _brute_force_min_cost(), _enumerate_all_paths(), Return the minimum path cost over all enumerated paths. Uses..., Enumerate every possible path through the candidate lattice. Each frame...

### Community 216 - "Quality Summary"
Cohesion: 0.50
Nodes (4): _build_quality_summary_html(), _median_of_values(), Median of a list of floats, ignoring None entries. Args: vals: list of floats..., Build the corpus-level Quality summary section (folded-in scorer output)....

### Community 217 - "Diagnostics Writer"
Cohesion: 0.50
Nodes (4): Serialize interval solver diagnostics to a JSON file. Strips non-serializable..., Write diagnostics data to a JSON file. Ensures the required header key is..., write_diagnostics(), write_solver_diagnostics()

### Community 224 - "Argument Parsing"
Cohesion: 0.67
Nodes (3): parse_args(), Namespace, Parse command-line arguments with subcommands for track_runner v2. Returns:...

### Community 226 - "Frame Interpolation"
Cohesion: 0.50
Nodes (4): _box_at(), _interpolate_missing_frame(), Fill one un-emitted interior frame by bracketing linear interpolation. Searches..., Return the cx/cy/w/h box for a frame from walker rows or seed anchors.

### Community 238 - "Import Validation"
Cohesion: 0.67
Nodes (3): parametrize, Validate imports against stdlib, repo modules, requirements, and whitelist., test_import_requirements()

## Knowledge Gaps
- **10 isolated node(s):** `encode_all.sh script`, `re-solve.sh script`, `run_random_walk.sh script`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED` (+5 more)
  These have <=1 connection - possible missing edges or undocumented components.
- **41 thin communities (<3 nodes) omitted from report** - run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SeedController` connect `Seed Mode Controller` to `Frame Scrubbing`, `Target Controller`, `Annotation Controller`, `Toolbar and Step Display`, `Video Probe`, `Frame Title`, `Seed Controller Tests`, `Keyboard Event Handler`, `Status Bar`, `Quit Handling`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `BaseAnnotationController` connect `Annotation Controller` to `Zoom Control`, `.eventFilter`, `Status Bar Controller`, `Seed Mode Controller`, `Overlay Management`, `Edit Mode Controller`, `Heat Map Overlay`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `EditController` connect `Edit Mode Controller` to `Toolbar Build`, `Walker Adapter Tests`, `Polish Preview`, `Annotation Controller`, `Video Probe`, `Key Event Handling`, `Edit Mode`, `_YoloLoaderThread`, `Status Text`, `Status Update`, `Heat Map Overlay`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Are the 97 inferred relationships involving `RuntimeError` (e.g. with `._init_cv2_backend()` and `.__iter__()`) actually correct?**
  _`RuntimeError` has 97 INFERRED edges - model-reasoned connections that need verification._
- **What connects `encode_all.sh script`, `re-solve.sh script`, `run_random_walk.sh script` to the rest of the system?**
  _10 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Blob Observation` be split into smaller, more focused modules?**
  _Cohesion score 0.04105263157894737 - nodes in this community are weakly interconnected._
- **Should `CLI Commands` be split into smaller, more focused modules?**
  _Cohesion score 0.0517503805175038 - nodes in this community are weakly interconnected._
