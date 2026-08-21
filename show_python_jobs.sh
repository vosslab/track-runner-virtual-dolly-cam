#!/bin/sh

ps -axo pid,ppid,time,command \
  | grep Python \
  | sed 's#/opt/homebrew/Cellar/python@3.12/3.[0-9._]*/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/##' \
  | grep -v grep \
  | grep -v sed
echo ""
uptime
