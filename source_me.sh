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
