#!/usr/bin/env bash
set -euo pipefail

IMAGE="jannesbrunner/health-coach"
TOML="pyproject.toml"

# --- Aktuelle Version lesen ---
CURRENT=$(grep '^version = ' "$TOML" | sed 's/version = "\(.*\)"/\1/')
MAJOR=$(echo "$CURRENT" | cut -d. -f1)
MINOR=$(echo "$CURRENT" | cut -d. -f2)
PATCH=$(echo "$CURRENT" | cut -d. -f3)

# --- Patch bumpen ---
NEW_PATCH=$((PATCH + 1))
NEW_VERSION="${MAJOR}.${MINOR}.${NEW_PATCH}"

sed -i "s/^version = \"${CURRENT}\"/version = \"${NEW_VERSION}\"/" "$TOML"
echo "Version: ${CURRENT} → ${NEW_VERSION}"

# --- Docker build ---
echo ""
echo "Building ${IMAGE}:${NEW_VERSION} ..."
docker build \
  -t "${IMAGE}:${NEW_VERSION}" \
  -t "${IMAGE}:latest" \
  .

# --- Docker push ---
echo ""
echo "Pushing ${IMAGE}:${NEW_VERSION} ..."
docker push "${IMAGE}:${NEW_VERSION}"

echo "Pushing ${IMAGE}:latest ..."
docker push "${IMAGE}:latest"

echo ""
echo "Done. Released ${IMAGE}:${NEW_VERSION}"
