#!/bin/bash

# Script to prepare a Foundry project for analysis
# Usage: ./prepare-project.sh [project-directory]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if project directory is provided
if [ -z "$1" ]; then
    echo -e "${RED}Error: Please provide the project directory${NC}"
    echo "Usage: $0 <project-directory>"
    exit 1
fi

PROJECT_DIR="$1"
PROJECT_NAME=$(basename "$PROJECT_DIR")
OUTPUT_ZIP="${PROJECT_NAME}.zip"

# Check if directory exists
if [ ! -d "$PROJECT_DIR" ]; then
    echo -e "${RED}Error: Directory '$PROJECT_DIR' does not exist${NC}"
    exit 1
fi

cd "$PROJECT_DIR"

# Check for foundry.toml
if [ ! -f "foundry.toml" ]; then
    echo -e "${YELLOW}Warning: foundry.toml not found. This may not be a Foundry project.${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check for src directory
if [ ! -d "src" ]; then
    echo -e "${RED}Error: src directory not found${NC}"
    exit 1
fi

# Install dependencies if needed
if [ ! -d "lib" ] || [ -z "$(ls -A lib)" ]; then
    echo -e "${YELLOW}Installing dependencies with forge...${NC}"
    forge install
fi

# Build the project to ensure it compiles
echo -e "${GREEN}Building project to verify compilation...${NC}"
forge build

# Count contracts
CONTRACT_COUNT=$(find src -name "*.sol" | wc -l)
echo -e "${GREEN}Found $CONTRACT_COUNT Solidity files${NC}"

# Create the ZIP file
echo -e "${GREEN}Creating ZIP file...${NC}"
cd ..
zip -r "$OUTPUT_ZIP" "$PROJECT_NAME" \
    -x "*.git/*" \
    -x "*cache/*" \
    -x "*out/*" \
    -x "*node_modules/*" \
    -x "*.env*" \
    -x "*broadcast/*" \
    -x "*.DS_Store" \
    -x "*test/*" \
    -x "*script/*" \
    -q

# Get file size
FILE_SIZE=$(du -h "$OUTPUT_ZIP" | cut -f1)

echo -e "${GREEN} Successfully created $OUTPUT_ZIP ($FILE_SIZE)${NC}"
echo -e "${GREEN} The ZIP file includes:${NC}"
echo "   - src/ (contract sources)"
echo "   - lib/ (dependencies)"
echo "   - foundry.toml (configuration)"
echo ""
echo -e "${GREEN}You can now upload this file to the Solidity Reentrancy Analyzer${NC}"
