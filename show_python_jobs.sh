#!/bin/sh

ps -ef | grep Python | sed 's#/opt/homebrew/Cellar/python@3.12/3.12.13_2/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/##' | grep -v grep | grep -v sed
echo ""
uptime
