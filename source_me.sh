set | grep -q '^BASH_VERSION=' || echo "use bash for your shell"
set | grep -q '^BASH_VERSION=' || exit 1

# Note: BASHRC unsets PYTHONPATH
source ~/.bashrc

if [ -d /opt/homebrew/bin ]; then
	export PATH="/opt/homebrew/bin:$PATH"
fi
if [ -d /opt/homebrew/opt/python@3.12/libexec/bin ]; then
	export PATH="/opt/homebrew/opt/python@3.12/libexec/bin:$PATH"
fi
if [ -d /opt/homebrew/opt/python@3.12/bin ]; then
	export PATH="/opt/homebrew/opt/python@3.12/bin:$PATH"
fi

# Set Python environment optimizations
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Put repo root on PYTHONPATH so `common_tools` (and other repo-root packages)
# are importable from scripts under tools/, track_runner/, tests/, etc.
_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -n "$_REPO_ROOT" ]; then
	export PYTHONPATH="$_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi
unset _REPO_ROOT
