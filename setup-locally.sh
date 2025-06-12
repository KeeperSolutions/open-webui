#!/bin/bash
# Ollama needs to be installed locally to run this script successfully.

# Setup a virtual environment and run Open WebUI locally
set -e

echo "Setting up Open WebUI development environment..."

# Ensure Python 3.11 is installed and get its path
PYTHON_PATH=$(which python3.11)
if [ -z "$PYTHON_PATH" ]; then
    echo "Error: Python 3.11 is not installed. Please install it with: brew install python@3.11"
    exit 1
fi

echo "Using Python at: $PYTHON_PATH"

# Remove existing venv if it's using the wrong Python version
if [ -d "venv" ]; then
    VENV_PYTHON_VERSION=$(./venv/bin/python --version 2>&1)
    if [[ $VENV_PYTHON_VERSION != *"3.11"* ]]; then
        echo "Removing existing venv with incorrect Python version: $VENV_PYTHON_VERSION"
        rm -rf venv
    fi
fi

# Create a fresh virtual environment if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment with Python 3.11..."
    $PYTHON_PATH -m venv venv
fi

# Activate the virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Verify Python version
python_version=$(python --version)
echo "Using $python_version inside virtual environment"

# Install Open WebUI
echo "Installing Open WebUI..."
pip install open-webui


# make it executable with chmod +x setup-locally.sh, and run it with ./setup-locally.sh. or
#sh setup-locally.sh

# to start Open WebUI run in the terminal:
# Create a virtual environment with Python 3.11 exclusively
#python3.11 -m venv venv
# Activate the virtual environment
#source venv/bin/activate
# install dependencies
# pip install -r requirements.txt
# go to backend dir and run sh dev.sh in virtual environment
#sh dev.sh


# Troubleshooting the dependency installation:
# Deactivate current venv
#deactivate

# Remove the problematic venv
#rm -rf venv

# Create new venv with explicit Python 3.11
#python3.11 -m venv venv

# Activate the new venv
#source venv/bin/activate

# Verify it's using Python 3.11
#python --version
#pip --version

# Now install requirements
#pip install -r requirements.txt
#or
#pip install --no-cache-dir -r requirements.txt