"""Guards every CLI mode passes through before any decode work begins."""

# PIP3 modules
import pytest

# local repo modules
import modes.shared as mode_shared


#============================================
def test_supported_container_is_accepted() -> None:
	"""A source naming the supported container passes the guard."""
	mode_shared.validate_source_container("/videos/race.mkv")


#============================================
def test_container_check_ignores_case() -> None:
	"""An upper-case extension names the same container."""
	mode_shared.validate_source_container("/videos/RACE.MKV")


#============================================
def test_unsupported_container_is_rejected() -> None:
	"""A source naming another container fails before any decode work."""
	with pytest.raises(RuntimeError):
		mode_shared.validate_source_container("/videos/race.mp4")


#============================================
def test_rejection_names_the_remux_command() -> None:
	"""The rejection tells the caller how to produce an acceptable source."""
	with pytest.raises(RuntimeError) as excinfo:
		mode_shared.validate_source_container("/videos/race.mov")
	message = str(excinfo.value)
	assert "mkvmerge" in message
	assert "/videos/race.mkv" in message
