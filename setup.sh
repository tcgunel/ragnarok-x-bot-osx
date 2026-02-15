#!/bin/bash
# Setup script for Ragnarok X Bot (Garden + Boss Farming)

echo "Installing dependencies..."
echo

# Tesseract OCR engine
if ! command -v tesseract &> /dev/null; then
    echo "Installing Tesseract OCR..."
    brew install tesseract
else
    echo "Tesseract already installed: $(tesseract --version 2>&1 | head -1)"
fi

echo

# Python packages
echo "Installing Python packages..."
pip3 install -r requirements.txt

echo
echo "Done! Quick start:"
echo
echo "  Option 1: Terminal UI (recommended)"
echo "    python3 gui.py"
echo
echo "  Option 2: Garden bot only (CLI)"
echo "    python3 garden_bot.py calibrate"
echo "    python3 garden_bot.py run"
echo
echo "  Boss farming setup:"
echo "    1. python3 gui.py"
echo "    2. Press C to calibrate boss positions"
echo "    3. Check the bosses you want to farm"
echo "    4. Press S to start"
echo
echo "IMPORTANT: Grant these macOS permissions:"
echo "  - Screen Recording: System Settings > Privacy > Screen Recording > Terminal"
echo "  - Accessibility:    System Settings > Privacy > Accessibility > Terminal"
