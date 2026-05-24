#!/usr/bin/env python3
"""
Consume leave-one-out seed-oracle CSV and test blob refinement hypotheses.

Hypotheses H1, H2, H3, H6, H8 are tested against pre-registered thresholds.
Each hypothesis gets a verdict (SUPPORTED, REFUTED, INCONCLUSIVE) at the
primary threshold and sensitivity variants at 20%, 30%, 40%.

Per-bin tables (video, scale, race phase, seed distance, pass direction)
show frame counts and hit rates per category.

The TRUST_raw_pred_quality summary for IMG_4005 TRUST 0% intervals
quantifies how often raw_pred_loo is far from seed_torso.

Semantic concern from WP-0.7A smoke: blob_gate column source may be
ambiguous (LOO call's gate vs oracle call's gate). Documented in the report.
"""

import argparse
import dataclasses
import json
import math
import os
from pathlib import Path

import numpy
import pandas


#============================================
# Constants and pre-registered thresholds
#============================================

# Pre-registered TRUST 0% intervals (from plan context for IMG_4005)
IMG_4005_TRUST_ZERO_INTERVALS = [
	(2444, 2491),
	(2491, 2538),
	(2632, 2726),
	(2726, 2773),
	(2773, 2820),
	(2820, 2867),
]

# From track_runner.residual_motion.DEFAULT_THRESHOLD
DEFAULT_THRESHOLD = 10.0


#============================================
# Hypothesis test definitions
#============================================

@dataclasses.dataclass
class HypothesisResult:
	name: str
	key: str  # short identifier: h1, h2, h3, h6, h8
	verdict_primary: str  # SUPPORTED, REFUTED, INCONCLUSIVE
	verdict_20: str
	verdict_30: str
	verdict_40: str
	threshold_primary: float
	n_frames: int
	n_videos: int
	n_eligible: int  # eligible-frame denominator used for rate_primary
	rate_primary: float  # observed hit rate at primary threshold (0 when n_eligible == 0)
	details: dict
	per_bin_breakdown: dict = dataclasses.field(default_factory=dict)


def _compute_dist_h(cx1, cy1, cx2, cy2, h_seed):
	"""Euclidean distance in units of torso height."""
	# Handle pandas NA and None
	if pandas.isna(cx1) or pandas.isna(cy1) or pandas.isna(cx2) or pandas.isna(cy2):
		return numpy.nan
	if pandas.isna(h_seed) or h_seed == 0:
		return numpy.nan
	d_px = math.sqrt((float(cx1) - float(cx2))**2 + (float(cy1) - float(cy2))**2)
	return d_px / float(h_seed)


def _test_h1_limb_centroid_bias(df: pandas.DataFrame) -> HypothesisResult:
	"""
	H1: extracted blob overlaps runner but centroid is offset > 0.6*h_seed.
	Signature: mask_overlap >= 50% AND dist(blob_center, seed_torso) > 0.6*h_seed.
	Threshold: >= 30% of hidden-seed frames.
	"""
	# All frames with LOO winner data and h_seed > 0
	all_frames_with_loo_winner = df[
		~df['loo_winner_cx'].isna() &
		~df['loo_winner_cy'].isna() &
		(df['h_seed'] > 0)
	].copy()

	# Subset with overlap >= 50% (the H1 signature condition)
	subset = all_frames_with_loo_winner[
		all_frames_with_loo_winner['loo_winner_mask_overlap_frac'] >= 0.5
	].copy()

	if len(subset) == 0:
		return HypothesisResult(
			name='H1 (limb centroid bias)',
			key='h1',
			verdict_primary='INCONCLUSIVE',
			verdict_20='INCONCLUSIVE',
			verdict_30='INCONCLUSIVE',
			verdict_40='INCONCLUSIVE',
			threshold_primary=0.30,
			n_frames=0,
			n_videos=0,
			n_eligible=int(len(all_frames_with_loo_winner)),
			rate_primary=0.0,
			details={'reason': 'no frames with loo_winner and overlap >= 50%'}
		)

	# Compute dist(loo_winner, seed_torso) in units of h_seed
	subset['winner_dist_h_computed'] = subset.apply(
		lambda row: _compute_dist_h(
			row['loo_winner_cx'], row['loo_winner_cy'],
			row['seed_torso_cx'], row['seed_torso_cy'],
			row['h_seed']
		),
		axis=1
	)

	# H1 signature: dist > 0.6 * h_seed
	h1_hits = subset[subset['winner_dist_h_computed'] > 0.6]
	# Denominator is all frames with loo_winner, not just the overlap-filtered subset
	rate_primary = len(h1_hits) / len(all_frames_with_loo_winner) if len(all_frames_with_loo_winner) > 0 else 0

	# Verdicts at sensitivity levels
	thresholds_by_level = {
		'primary': 0.30,
		'20': 0.20,
		'30': 0.30,
		'40': 0.40,
	}

	verdicts = {}
	for level_name, threshold in thresholds_by_level.items():
		if rate_primary >= threshold:
			verdicts[f'verdict_{level_name}'] = 'SUPPORTED'
		elif rate_primary < threshold * 0.5:  # Well below threshold
			verdicts[f'verdict_{level_name}'] = 'REFUTED'
		else:
			verdicts[f'verdict_{level_name}'] = 'INCONCLUSIVE'

	n_videos = subset['video'].nunique()

	# Compute per-bin breakdown
	per_bin = _compute_per_bin_breakdown(df, h1_hits.index, 'H1')

	return HypothesisResult(
		name='H1 (limb centroid bias)',
		key='h1',
		verdict_primary=verdicts['verdict_primary'],
		verdict_20=verdicts['verdict_20'],
		verdict_30=verdicts['verdict_30'],
		verdict_40=verdicts['verdict_40'],
		threshold_primary=0.30,
		n_frames=len(subset),
		n_videos=n_videos,
		n_eligible=int(len(all_frames_with_loo_winner)),
		rate_primary=float(rate_primary),
		details={
			'rate_primary': rate_primary,
			'h1_hits': len(h1_hits),
			'total_frames': len(subset),
		},
		per_bin_breakdown=per_bin
	)


def _test_h2_raw_pred_wrong(df: pandas.DataFrame) -> HypothesisResult:
	"""
	H2: solver raw_pred is far from seed_torso, but oracle observer finds runner close.
	Signature: dist(raw_pred_loo, seed_torso) > 0.6*h_seed AND
		dist(oracle_winner, seed_torso) <= 0.3*h_seed.
	Threshold: >= 30% of seed-adjacent frames.
	"""
	# Rows where h_seed > 0 and we have oracle data
	has_oracle = (
		(df['h_seed'] > 0) &
		~df['oracle_winner_cx'].isna() &
		~df['oracle_winner_cy'].isna()
	)
	subset = df[has_oracle].copy()

	if len(subset) == 0:
		return HypothesisResult(
			name='H2 (raw_pred is wrong)',
			key='h2',
			verdict_primary='INCONCLUSIVE',
			verdict_20='INCONCLUSIVE',
			verdict_30='INCONCLUSIVE',
			verdict_40='INCONCLUSIVE',
			threshold_primary=0.30,
			n_frames=0,
			n_videos=0,
			n_eligible=0,
			rate_primary=0.0,
			details={'reason': 'no frames with oracle winner data'}
		)

	# Compute distances
	subset['raw_pred_loo_dist_h'] = subset.apply(
		lambda row: _compute_dist_h(
			row['raw_pred_loo_cx'], row['raw_pred_loo_cy'],
			row['seed_torso_cx'], row['seed_torso_cy'],
			row['h_seed']
		),
		axis=1
	)
	subset['oracle_winner_dist_h_computed'] = subset.apply(
		lambda row: _compute_dist_h(
			row['oracle_winner_cx'], row['oracle_winner_cy'],
			row['seed_torso_cx'], row['seed_torso_cy'],
			row['h_seed']
		),
		axis=1
	)

	# H2 signature
	h2_hits = subset[
		(subset['raw_pred_loo_dist_h'] > 0.6) &
		(subset['oracle_winner_dist_h_computed'] <= 0.3)
	]
	rate_primary = len(h2_hits) / len(subset) if len(subset) > 0 else 0

	thresholds_by_level = {
		'primary': 0.30,
		'20': 0.20,
		'30': 0.30,
		'40': 0.40,
	}

	verdicts = {}
	for level_name, threshold in thresholds_by_level.items():
		if rate_primary >= threshold:
			verdicts[f'verdict_{level_name}'] = 'SUPPORTED'
		elif rate_primary < threshold * 0.5:
			verdicts[f'verdict_{level_name}'] = 'REFUTED'
		else:
			verdicts[f'verdict_{level_name}'] = 'INCONCLUSIVE'

	n_videos = subset['video'].nunique()

	# Compute per-bin breakdown
	per_bin = _compute_per_bin_breakdown(df, h2_hits.index, 'H2')

	return HypothesisResult(
		name='H2 (raw_pred is wrong)',
		key='h2',
		verdict_primary=verdicts['verdict_primary'],
		verdict_20=verdicts['verdict_20'],
		verdict_30=verdicts['verdict_30'],
		verdict_40=verdicts['verdict_40'],
		threshold_primary=0.30,
		n_frames=len(subset),
		n_videos=n_videos,
		n_eligible=int(len(subset)),
		rate_primary=float(rate_primary),
		details={
			'rate_primary': rate_primary,
			'h2_hits': len(h2_hits),
			'total_frames': len(subset),
		},
		per_bin_breakdown=per_bin
	)


def _test_h3_corridor_too_narrow(df: pandas.DataFrame) -> HypothesisResult:
	"""
	H3: raw blobs exist near seed_torso within 2.0*corridor_radius,
	but corridor filter removes them (n_corridor_blobs == 0).
	Threshold: >= 30% of seed frames.
	"""
	# Check if loo_closest_raw_blob_dist_h column exists
	if 'loo_closest_raw_blob_dist_h' not in df.columns:
		return HypothesisResult(
			name='H3 (corridor too narrow)',
			key='h3',
			verdict_primary='INCONCLUSIVE_SCHEMA',
			verdict_20='INCONCLUSIVE_SCHEMA',
			verdict_30='INCONCLUSIVE_SCHEMA',
			verdict_40='INCONCLUSIVE_SCHEMA',
			threshold_primary=0.30,
			n_frames=0,
			n_videos=0,
			n_eligible=0,
			rate_primary=0.0,
			details={'reason': 'missing loo_closest_raw_blob_dist_h column in CSV schema'}
		)

	# Rows where h_seed > 0, LOO has raw blobs but no corridor blobs,
	# and closest raw blob is < 2.0 torso-height units from seed_torso
	subset = df[
		(df['h_seed'] > 0) &
		(df['loo_n_raw_blobs'] >= 1) &
		(df['loo_n_corridor_blobs'] == 0) &
		(df['loo_closest_raw_blob_dist_h'] < 2.0)
	].copy()

	if len(subset) == 0:
		return HypothesisResult(
			name='H3 (corridor too narrow)',
			key='h3',
			verdict_primary='INCONCLUSIVE',
			verdict_20='INCONCLUSIVE',
			verdict_30='INCONCLUSIVE',
			verdict_40='INCONCLUSIVE',
			threshold_primary=0.30,
			n_frames=0,
			n_videos=0,
			n_eligible=int(len(df[df['h_seed'] > 0])),
			rate_primary=0.0,
			details={'reason': 'no frames with raw_blobs but no corridor_blobs within 2.0 h_seed'}
		)

	# H3 signature: we trust the CSV blob counts as ground truth
	# (plan does not require additional distance check in this analyzer)
	rate_primary = len(subset) / len(df[df['h_seed'] > 0]) if len(df[df['h_seed'] > 0]) > 0 else 0

	thresholds_by_level = {
		'primary': 0.30,
		'20': 0.20,
		'30': 0.30,
		'40': 0.40,
	}

	verdicts = {}
	for level_name, threshold in thresholds_by_level.items():
		if rate_primary >= threshold:
			verdicts[f'verdict_{level_name}'] = 'SUPPORTED'
		elif rate_primary < threshold * 0.5:
			verdicts[f'verdict_{level_name}'] = 'REFUTED'
		else:
			verdicts[f'verdict_{level_name}'] = 'INCONCLUSIVE'

	n_videos = subset['video'].nunique()

	# Compute per-bin breakdown
	per_bin = _compute_per_bin_breakdown(df, subset.index, 'H3')

	return HypothesisResult(
		name='H3 (corridor too narrow)',
		key='h3',
		verdict_primary=verdicts['verdict_primary'],
		verdict_20=verdicts['verdict_20'],
		verdict_30=verdicts['verdict_30'],
		verdict_40=verdicts['verdict_40'],
		threshold_primary=0.30,
		n_frames=len(subset),
		n_videos=n_videos,
		n_eligible=int(len(df[df['h_seed'] > 0])),
		rate_primary=float(rate_primary),
		details={
			'rate_primary': rate_primary,
			'corridor_loss_frames': len(subset),
			'total_seed_frames': len(df[df['h_seed'] > 0]),
		},
		per_bin_breakdown=per_bin
	)


def _test_h6_wrong_winner(df: pandas.DataFrame) -> HypothesisResult:
	"""
	H6: cue-confidence picks wrong blob.
	Signature: loo_n_corridor_blobs > 1 AND
		dist(best_alt, seed_torso) < dist(winner, seed_torso) - 0.2*h_seed.
	Threshold: >= 20% of multi-blob-corridor seed frames.
	"""
	# Filter to multi-blob corridor frames
	multi_blob = df[
		(df['h_seed'] > 0) &
		(df['loo_n_corridor_blobs'] > 1) &
		~df['loo_best_alt_dist_h'].isna()
	].copy()

	if len(multi_blob) == 0:
		return HypothesisResult(
			name='H6 (wrong winner)',
			key='h6',
			verdict_primary='INCONCLUSIVE',
			verdict_20='INCONCLUSIVE',
			verdict_30='INCONCLUSIVE',
			verdict_40='INCONCLUSIVE',
			threshold_primary=0.20,
			n_frames=0,
			n_videos=0,
			n_eligible=0,
			rate_primary=0.0,
			details={'reason': 'no multi-blob corridor frames'}
		)

	# H6 signature: best_alt is closer than winner (minus threshold).
	# Both loo_winner_dist_h and loo_best_alt_dist_h are already in torso-height units.
	multi_blob['h6_hit'] = (
		multi_blob['loo_best_alt_dist_h'] <
		multi_blob['loo_winner_dist_h'] - 0.2
	)

	h6_hits = multi_blob[multi_blob['h6_hit']]
	rate_primary = len(h6_hits) / len(multi_blob) if len(multi_blob) > 0 else 0

	thresholds_by_level = {
		'primary': 0.20,
		'20': 0.20,
		'30': 0.30,
		'40': 0.40,
	}

	verdicts = {}
	for level_name, threshold in thresholds_by_level.items():
		if rate_primary >= threshold:
			verdicts[f'verdict_{level_name}'] = 'SUPPORTED'
		elif rate_primary < threshold * 0.5:
			verdicts[f'verdict_{level_name}'] = 'REFUTED'
		else:
			verdicts[f'verdict_{level_name}'] = 'INCONCLUSIVE'

	n_videos = multi_blob['video'].nunique()

	# Compute per-bin breakdown
	per_bin = _compute_per_bin_breakdown(df, h6_hits.index, 'H6')

	return HypothesisResult(
		name='H6 (wrong winner)',
		key='h6',
		verdict_primary=verdicts['verdict_primary'],
		verdict_20=verdicts['verdict_20'],
		verdict_30=verdicts['verdict_30'],
		verdict_40=verdicts['verdict_40'],
		threshold_primary=0.20,
		n_frames=len(multi_blob),
		n_videos=n_videos,
		n_eligible=int(len(multi_blob)),
		rate_primary=float(rate_primary),
		details={
			'rate_primary': rate_primary,
			'h6_hits': len(h6_hits),
			'multi_blob_frames': len(multi_blob),
		},
		per_bin_breakdown=per_bin
	)


def _test_h8_dog_suppresses(df: pandas.DataFrame) -> HypothesisResult:
	"""
	H8: DoG band suppresses runner.
	Signature: pre_dog_max_at_seed_torso >= 3 * DEFAULT_THRESHOLD AND
		post_dog_max_at_seed_torso < DEFAULT_THRESHOLD.
	Threshold: >= 20% of seed frames.
	"""
	# Filter to rows with h_seed > 0 and both pre/post DoG values
	subset = df[
		(df['h_seed'] > 0) &
		~df['pre_dog_max_at_seed_torso'].isna() &
		~df['post_dog_max_at_seed_torso'].isna()
	].copy()

	if len(subset) == 0:
		return HypothesisResult(
			name='H8 (DoG suppresses)',
			key='h8',
			verdict_primary='INCONCLUSIVE',
			verdict_20='INCONCLUSIVE',
			verdict_30='INCONCLUSIVE',
			verdict_40='INCONCLUSIVE',
			threshold_primary=0.20,
			n_frames=0,
			n_videos=0,
			n_eligible=0,
			rate_primary=0.0,
			details={'reason': 'no frames with pre/post DoG data'}
		)

	# H8 signature
	h8_hits = subset[
		(subset['pre_dog_max_at_seed_torso'] >= 3.0 * DEFAULT_THRESHOLD) &
		(subset['post_dog_max_at_seed_torso'] < DEFAULT_THRESHOLD)
	]
	rate_primary = len(h8_hits) / len(subset) if len(subset) > 0 else 0

	thresholds_by_level = {
		'primary': 0.20,
		'20': 0.20,
		'30': 0.30,
		'40': 0.40,
	}

	verdicts = {}
	for level_name, threshold in thresholds_by_level.items():
		if rate_primary >= threshold:
			verdicts[f'verdict_{level_name}'] = 'SUPPORTED'
		elif rate_primary < threshold * 0.5:
			verdicts[f'verdict_{level_name}'] = 'REFUTED'
		else:
			verdicts[f'verdict_{level_name}'] = 'INCONCLUSIVE'

	n_videos = subset['video'].nunique()

	# Compute per-bin breakdown
	per_bin = _compute_per_bin_breakdown(df, h8_hits.index, 'H8')

	return HypothesisResult(
		name='H8 (DoG suppresses)',
		key='h8',
		verdict_primary=verdicts['verdict_primary'],
		verdict_20=verdicts['verdict_20'],
		verdict_30=verdicts['verdict_30'],
		verdict_40=verdicts['verdict_40'],
		threshold_primary=0.20,
		n_frames=len(subset),
		n_videos=n_videos,
		n_eligible=int(len(subset)),
		rate_primary=float(rate_primary),
		details={
			'rate_primary': rate_primary,
			'h8_hits': len(h8_hits),
			'total_frames': len(subset),
		},
		per_bin_breakdown=per_bin
	)


#============================================
# Per-bin analysis helpers
#============================================

def _bin_h_seed(h_seed):
	"""Bin h_seed into size categories."""
	if h_seed < 25:
		return '[0, 25)'
	elif h_seed < 75:
		return '[25, 75)'
	else:
		return '[75, inf)'


def _bin_dist_from_seed(frame_index, seeds_in_video):
	"""
	Bin distance from nearest seed for a given frame.
	Returns distance in frames to nearest seed, binned into:
	[0, 1] (seed rows), [2, 10], [11, 30], [31, inf)
	"""
	if frame_index in seeds_in_video:
		return '[0, 1]'

	# Find distance to nearest seed
	dists = [abs(frame_index - s) for s in seeds_in_video]
	if not dists:
		return '[31, inf)'

	min_dist = min(dists)
	if min_dist <= 10:
		return '[2, 10]'
	elif min_dist <= 30:
		return '[11, 30]'
	else:
		return '[31, inf)'


def _compute_per_bin_breakdown(df, hit_indices, hypothesis_name):
	"""
	Compute per-bin hit rates for video, scale, race_phase, dist_from_nearest_seed,
	and pass_direction (if column exists).
	hit_indices: set or index of rows that are hits (for this hypothesis).
	Returns dict of dicts: {bin_type: {bin_value: {'frames': N, 'hits': M, 'rate': rate}}}
	"""
	# Convert hit_indices to a set for fast membership checks
	if hasattr(hit_indices, 'tolist'):
		hit_set = set(hit_indices.tolist())
	else:
		hit_set = set(hit_indices)

	bins = {}

	# Per-video breakdown
	video_bins = {}
	for video in df['video'].unique():
		video_df = df[df['video'] == video]
		video_frames = len(video_df)
		video_hits = sum(1 for idx in video_df.index if idx in hit_set)
		rate = video_hits / video_frames if video_frames > 0 else 0
		video_bins[video] = {
			'frames': int(video_frames),
			'hits': int(video_hits),
			'rate': rate,
		}
	bins['video'] = video_bins

	# Per-scale breakdown (h_seed bins)
	scale_bins = {}
	df_temp = df.copy()
	df_temp['scale_bin'] = df_temp['h_seed'].apply(_bin_h_seed)
	for scale_bin in ['[0, 25)', '[25, 75)', '[75, inf)']:
		scale_df = df_temp[df_temp['scale_bin'] == scale_bin]
		scale_frames = len(scale_df)
		scale_hits = sum(1 for idx in scale_df.index if idx in hit_set)
		rate = scale_hits / scale_frames if scale_frames > 0 else 0
		scale_bins[scale_bin] = {
			'frames': int(scale_frames),
			'hits': int(scale_hits),
			'rate': rate,
		}
	bins['scale'] = scale_bins

	# Per-race_phase breakdown (if column exists)
	if 'race_phase' in df.columns:
		race_phase_bins = {}
		for phase in df['race_phase'].unique():
			if pandas.isna(phase):
				continue
			phase_df = df[df['race_phase'] == phase]
			phase_frames = len(phase_df)
			phase_hits = sum(1 for idx in phase_df.index if idx in hit_set)
			rate = phase_hits / phase_frames if phase_frames > 0 else 0
			race_phase_bins[str(phase)] = {
				'frames': int(phase_frames),
				'hits': int(phase_hits),
				'rate': rate,
			}
		if race_phase_bins:
			bins['race_phase'] = race_phase_bins

	# Per-dist_from_nearest_seed breakdown
	dist_bins = {
		'[0, 1]': {'frames': 0, 'hits': 0, 'rate': 0},
		'[2, 10]': {'frames': 0, 'hits': 0, 'rate': 0},
		'[11, 30]': {'frames': 0, 'hits': 0, 'rate': 0},
		'[31, inf)': {'frames': 0, 'hits': 0, 'rate': 0},
	}
	for video in df['video'].unique():
		video_df = df[df['video'] == video]
		seed_frames = set(video_df[video_df['h_seed'] > 0]['frame'].unique())
		for idx, row in video_df.iterrows():
			dist_bin = _bin_dist_from_seed(row['frame'], seed_frames)
			dist_bins[dist_bin]['frames'] += 1
			if idx in hit_set:
				dist_bins[dist_bin]['hits'] += 1

	for dist_bin in dist_bins:
		frames = dist_bins[dist_bin]['frames']
		if frames > 0:
			dist_bins[dist_bin]['rate'] = dist_bins[dist_bin]['hits'] / frames
	bins['dist_from_nearest_seed'] = dist_bins

	# Per-pass_direction breakdown (if column exists)
	if 'pass_direction' in df.columns:
		pass_dir_bins = {}
		for direction in df['pass_direction'].unique():
			if pandas.isna(direction):
				continue
			dir_df = df[df['pass_direction'] == direction]
			dir_frames = len(dir_df)
			dir_hits = sum(1 for idx in dir_df.index if idx in hit_set)
			rate = dir_hits / dir_frames if dir_frames > 0 else 0
			pass_dir_bins[str(direction)] = {
				'frames': int(dir_frames),
				'hits': int(dir_hits),
				'rate': rate,
			}
		if pass_dir_bins:
			bins['pass_direction'] = pass_dir_bins

	return bins


#============================================
# TRUST_raw_pred_quality analysis for IMG_4005
#============================================

def _compute_trust_raw_pred_quality(df: pandas.DataFrame) -> dict:
	"""
	For IMG_4005 TRUST 0% intervals, count frames where
	dist(raw_pred_loo, seed_torso) > 0.5 * h_seed.
	"""
	img4005 = df[df['video'].str.contains('IMG_4005')].copy()

	# Filter to frames in TRUST 0% intervals
	trust_zero_frames = []
	for start, end in IMG_4005_TRUST_ZERO_INTERVALS:
		trust_zero_frames.extend(img4005[
			(img4005['frame'] >= start) & (img4005['frame'] < end)
		].index.tolist())

	subset = img4005.loc[trust_zero_frames].copy()

	if len(subset) == 0:
		return {
			'img4005_trust_zero_frames': 0,
			'far_from_seed': 0,
			'rate': 0,
			'per_interval': {},
		}

	# Compute raw_pred_loo distance
	subset['raw_pred_loo_dist_h'] = subset.apply(
		lambda row: _compute_dist_h(
			row['raw_pred_loo_cx'], row['raw_pred_loo_cy'],
			row['seed_torso_cx'], row['seed_torso_cy'],
			row['h_seed']
		),
		axis=1
	)

	# Count frames far from seed (> 0.5 * h_seed)
	far_mask = subset['raw_pred_loo_dist_h'] > 0.5
	far_count = far_mask.sum()
	rate = far_count / len(subset) if len(subset) > 0 else 0

	# Per-interval breakdown
	per_interval = {}
	for start, end in IMG_4005_TRUST_ZERO_INTERVALS:
		interval_frames = subset[
			(subset['frame'] >= start) & (subset['frame'] < end)
		]
		if len(interval_frames) > 0:
			interval_far = (interval_frames['raw_pred_loo_dist_h'] > 0.5).sum()
			interval_rate = interval_far / len(interval_frames)
			per_interval[f'{start}-{end}'] = {
				'total': len(interval_frames),
				'far': interval_far,
				'rate': interval_rate,
			}

	return {
		'img4005_trust_zero_frames': len(subset),
		'far_from_seed': far_count,
		'rate': rate,
		'per_interval': per_interval,
	}


#============================================
# Report generation
#============================================

def _generate_report(
	hypotheses: list,
	trust_pred_quality: dict,
	output_path: str
) -> None:
	"""Generate the HYPOTHESIS_TESTS.md report."""
	lines = []

	lines.append('# Hypothesis Tests: Blob Refinement Under-Acceptance')
	lines.append('')
	lines.append('## Overview')
	lines.append('')
	lines.append('This report tests five key hypotheses (H1, H2, H3, H6, H8) from the')
	lines.append('seed-oracle leave-one-out audit. Each hypothesis is pre-registered with')
	lines.append('a threshold; verdicts are computed at 20%, 30%, 40% sensitivity levels')
	lines.append('to assess robustness.')
	lines.append('')
	lines.append('**Semantic concern**: The CSV `blob_gate` column source may be ambiguous.')
	lines.append('It currently reflects the LOO call\'s gate verdict. Whether this represents')
	lines.append('oracle-side or LOO-side outcomes should be clarified in WP-0.7A schema review.')
	lines.append('')

	# Per-hypothesis results
	lines.append('## Per-Hypothesis Verdicts')
	lines.append('')

	for hyp in hypotheses:
		lines.append(f'### {hyp.name}')
		lines.append('')
		lines.append(f'**Primary verdict**: {hyp.verdict_primary} (threshold: {hyp.threshold_primary:.0%})')
		lines.append('')
		lines.append('**Sensitivity table**:')
		lines.append('')
		lines.append('| Threshold | Verdict |')
		lines.append('| --- | --- |')
		lines.append(f'| 20% | {hyp.verdict_20} |')
		lines.append(f'| 30% | {hyp.verdict_30} |')
		lines.append(f'| 40% | {hyp.verdict_40} |')
		lines.append('')
		lines.append(
			f'Supported by {hyp.n_frames} seed frames across {hyp.n_videos} videos.'
		)
		lines.append('')

		# Details
		if 'rate_primary' in hyp.details:
			lines.append(
				f'Observed rate: {hyp.details["rate_primary"]:.1%} of eligible frames.'
			)
		for key, val in hyp.details.items():
			if key not in ['rate_primary', 'reason']:
				lines.append(f'- {key}: {val}')
		lines.append('')

		# Per-bin breakdown tables
		if hyp.per_bin_breakdown:
			lines.append('**Per-bin breakdown**:')
			lines.append('')

			# Per-video table
			if 'video' in hyp.per_bin_breakdown:
				lines.append('**By video**:')
				lines.append('')
				lines.append('| Video | Frames | Hits | Rate |')
				lines.append('| --- | --- | --- | --- |')
				for video, stats in sorted(hyp.per_bin_breakdown['video'].items()):
					lines.append(f'| {video} | {stats["frames"]} | {stats["hits"]} | {stats["rate"]:.1%} |')
				lines.append('')

			# Per-scale table
			if 'scale' in hyp.per_bin_breakdown:
				lines.append('**By scale (h_seed)**:')
				lines.append('')
				lines.append('| Scale bin | Frames | Hits | Rate |')
				lines.append('| --- | --- | --- | --- |')
				for scale, stats in hyp.per_bin_breakdown['scale'].items():
					lines.append(f'| {scale} | {stats["frames"]} | {stats["hits"]} | {stats["rate"]:.1%} |')
				lines.append('')

			# Per-race_phase table (if present)
			if 'race_phase' in hyp.per_bin_breakdown:
				lines.append('**By race phase**:')
				lines.append('')
				lines.append('| Race phase | Frames | Hits | Rate |')
				lines.append('| --- | --- | --- | --- |')
				for phase, stats in sorted(hyp.per_bin_breakdown['race_phase'].items()):
					lines.append(f'| {phase} | {stats["frames"]} | {stats["hits"]} | {stats["rate"]:.1%} |')
				lines.append('')

			# Per-dist_from_nearest_seed table
			if 'dist_from_nearest_seed' in hyp.per_bin_breakdown:
				lines.append('**By distance from nearest seed**:')
				lines.append('')
				lines.append('| Distance bin | Frames | Hits | Rate |')
				lines.append('| --- | --- | --- | --- |')
				for dist_bin in ['[0, 1]', '[2, 10]', '[11, 30]', '[31, inf)']:
					if dist_bin in hyp.per_bin_breakdown['dist_from_nearest_seed']:
						stats = hyp.per_bin_breakdown['dist_from_nearest_seed'][dist_bin]
						if stats['frames'] > 0:
							lines.append(f'| {dist_bin} | {stats["frames"]} | {stats["hits"]} | {stats["rate"]:.1%} |')
				lines.append('')

			# Per-pass_direction table (if present)
			if 'pass_direction' in hyp.per_bin_breakdown:
				lines.append('**By pass direction**:')
				lines.append('')
				lines.append('| Pass direction | Frames | Hits | Rate |')
				lines.append('| --- | --- | --- | --- |')
				for direction, stats in sorted(hyp.per_bin_breakdown['pass_direction'].items()):
					lines.append(f'| {direction} | {stats["frames"]} | {stats["hits"]} | {stats["rate"]:.1%} |')
				lines.append('')

	# TRUST_raw_pred_quality summary
	lines.append('## TRUST_raw_pred_quality Summary')
	lines.append('')
	lines.append('For IMG_4005 TRUST 0% intervals (where FWD/BWD agree, blob_accept=0%):')
	lines.append('')

	tpq = trust_pred_quality
	if tpq['img4005_trust_zero_frames'] > 0:
		lines.append(
			f'- Total probed frames: {tpq["img4005_trust_zero_frames"]}'
		)
		lines.append(
			f'- Frames where raw_pred_loo > 0.5*h_seed: {tpq["far_from_seed"]} '
			f'({tpq["rate"]:.1%})'
		)
		lines.append('')
		lines.append('**Per-interval breakdown**:')
		lines.append('')
		for interval_name, stats in tpq['per_interval'].items():
			lines.append(
				f'- {interval_name}: {stats["far"]}/{stats["total"]} '
				f'({stats["rate"]:.1%})'
			)
		lines.append('')
	else:
		lines.append('No TRUST 0% frames found in IMG_4005 data.')
		lines.append('')

	lines.append('## CSV Schema Notes')
	lines.append('')
	lines.append('**Semantic concern**: The `blob_gate` column reports the verdict from')
	lines.append('the LOO observer call. On seed frames, this represents what the solver')
	lines.append('would have decided WITHOUT that seed. On oracle calls (with the true')
	lines.append('seed center as raw_pred), the `blob_gate` column may be misaligned with')
	lines.append('the oracle measurement columns. This ambiguity should be resolved in')
	lines.append('WP-0.7A schema documentation: clarify whether `blob_gate` is always LOO,')
	lines.append('always oracle, or mixed per call context.')
	lines.append('')
	lines.append('## Interpretation')
	lines.append('')
	lines.append('Each verdict uses the phrase "supported by N seed frames across M videos"')
	lines.append('to ground the claim in data without claiming universal proof. The audit')
	lines.append('corpus is the twelve tracked videos with thousands of human-authored seeds.')
	lines.append('')

	# Write report
	os.makedirs(Path(output_path).parent, exist_ok=True)
	with open(output_path, 'w') as f:
		f.write('\n'.join(lines))


def _json_safe(value):
	"""Recursively coerce numpy and pandas scalars into plain JSON types."""
	# numpy scalars (covers numpy.int64, numpy.float64, numpy.bool_, etc.)
	if isinstance(value, numpy.generic):
		return value.item()
	# pandas NA / NaT
	if value is pandas.NA:
		return None
	# numpy arrays
	if isinstance(value, numpy.ndarray):
		return [_json_safe(item) for item in value.tolist()]
	# nested containers
	if isinstance(value, dict):
		return {str(_json_safe(k)): _json_safe(v) for k, v in value.items()}
	if isinstance(value, (list, tuple)):
		return [_json_safe(item) for item in value]
	# float NaN
	if isinstance(value, float) and math.isnan(value):
		return None
	return value


def _run_all_hypotheses(df: pandas.DataFrame) -> list:
	"""Run the five hypothesis tests on a dataframe slice (corpus or per-video)."""
	# Helper used by both corpus-level and per-video JSON emission so the
	# eligibility / hit-rate definitions stay consistent across scopes.
	tests = [
		_test_h1_limb_centroid_bias,
		_test_h2_raw_pred_wrong,
		_test_h3_corridor_too_narrow,
		_test_h6_wrong_winner,
		_test_h8_dog_suppresses,
	]
	results = []
	for test_fn in tests:
		results.append(test_fn(df))
	return results


def _hyp_verdict_block(hyp: HypothesisResult) -> dict:
	"""Reduce a HypothesisResult to the WP-1A sidecar verdict block."""
	# Plan-spec keys only: verdict, rate, n_eligible. Per the sidecar
	# contract, downstream control-flow consumers read these three values.
	return {
		'verdict': hyp.verdict_primary,
		'rate': float(hyp.rate_primary),
		'n_eligible': int(hyp.n_eligible),
	}


def _generate_json(
	corpus_hypotheses: list,
	df: pandas.DataFrame,
	output_path: str
) -> None:
	"""Write the WP-1A machine-readable sidecar (corpus + per_video schema).

	Markdown report stays unchanged; this writer only emits the JSON
	transport described in
	docs/active_plans plan kind-exploring-cray "WP-1A (full oracle)"
	Sidecar requirement: {"corpus": {h*: {verdict, rate, n_eligible}},
	"per_video": {video: {h*: ...}}}.
	"""
	# Corpus-scope block: one verdict block per hypothesis key.
	corpus_block = {}
	for hyp in corpus_hypotheses:
		corpus_block[hyp.key] = _hyp_verdict_block(hyp)

	# Per-video block: re-run the same five hypothesis tests on each
	# video's rows so eligibility counts stay scoped to that video. Plan
	# requires each video appear as a top-level key under "per_video".
	per_video_block = {}
	for video in sorted(df['video'].unique()):
		video_df = df[df['video'] == video]
		video_results = _run_all_hypotheses(video_df)
		video_block = {}
		for hyp in video_results:
			video_block[hyp.key] = _hyp_verdict_block(hyp)
		per_video_block[str(video)] = video_block

	payload = {
		'corpus': _json_safe(corpus_block),
		'per_video': _json_safe(per_video_block),
	}

	os.makedirs(Path(output_path).parent, exist_ok=True)
	with open(output_path, 'w') as f:
		json.dump(payload, f, indent=2, sort_keys=True)


def main() -> None:
	parser = argparse.ArgumentParser(
		description='Test blob hypotheses from seed-oracle CSV.'
	)
	parser.add_argument(
		'-i', '--input',
		dest='input_csv',
		default='output_smoke/seed_oracle/oracle.csv',
		help='Path to oracle CSV (default: output_smoke/seed_oracle/oracle.csv)',
	)
	parser.add_argument(
		'-o', '--output',
		dest='output_dir',
		default='output_smoke/seed_oracle/',
		help='Output directory for HYPOTHESIS_TESTS.md and HYPOTHESIS_TESTS.json',
	)
	args = parser.parse_args()

	# Load CSV
	if not os.path.exists(args.input_csv):
		raise FileNotFoundError(f'Input CSV not found: {args.input_csv}')

	df = pandas.read_csv(args.input_csv, dtype_backend='numpy_nullable')

	# Test hypotheses at corpus scope; per-video scope is recomputed
	# inside _generate_json against the same five test functions.
	results = _run_all_hypotheses(df)

	# TRUST_raw_pred_quality
	trust_pred_quality = _compute_trust_raw_pred_quality(df)

	# Generate report (Markdown and JSON)
	report_path = os.path.join(args.output_dir, 'HYPOTHESIS_TESTS.md')
	json_path = os.path.join(args.output_dir, 'HYPOTHESIS_TESTS.json')
	os.makedirs(args.output_dir, exist_ok=True)
	_generate_report(results, trust_pred_quality, report_path)
	_generate_json(results, df, json_path)

	print(f'Report written to {report_path}')
	print(f'JSON written to {json_path}')


if __name__ == '__main__':
	main()
