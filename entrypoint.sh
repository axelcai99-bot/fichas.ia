#!/bin/bash
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
sleep 2
export DISPLAY=:99
exec python app.py
