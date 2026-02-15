#!/bin/bash
# Setup script for Ragnarok X Garden Bot

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
echo "Done! Next steps:"
echo "  1. python3 garden_bot.py calibrate"
echo "  2. python3 garden_bot.py test"
echo "  3. python3 garden_bot.py run"
echo
echo "IMPORTANT: Grant these macOS permissions:"
echo "  - Screen Recording: System Settings > Privacy > Screen Recording > Terminal"
echo "  - Accessibility:    System Settings > Privacy > Accessibility > Terminal"
