#!/bin/bash

cd /home/container || exit 1


if [ -d "/home/container/dc-lb-lite" ]; then
		echo "dc-lb-lite directory already exists, skipping clone."
		git -C dc-lb-lite pull --rebase --depth 1
else
		echo "Cloning dc-lb-lite repository..."
		git clone -b alt/ptero --depth 1 https://github.com/UltimateForm/dc-lb-lite
		pip install -r requirements.txt
fi


cd dc-lb-lite

if [[ -z "${STARTUP}" ]]; then
		echo "CUSTOM STARTUP DEFINED: '${STARTUP}'"
		${STARTUP}
else
		python -u main.py
fi
