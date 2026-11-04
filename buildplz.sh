#!/bin/sh 

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt 
pip install -r requirements-build.txt 
pyinstaller -F main.py 
echo "built in some seconds or mins"
sudo cp dist/main /bin/pix
echo "done! run pix -h"
