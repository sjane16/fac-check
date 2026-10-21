#!/usr/bin/env bash
# Render build script: install packages, build the Tailwind CSS, collect static files, migrate.
set -o errexit
set -o nounset
set -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

# The built Tailwind CSS is not committed (theme/static/css/dist is gitignored), so build it here.
# It needs Node.js; use a temporary copy if the build image doesn't have npm.
if ! command -v npm >/dev/null 2>&1; then
  NODE_VERSION=22.11.0
  echo "npm not found; downloading Node.js ${NODE_VERSION} for the CSS build"
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" | tar -xJ -C /tmp
  export PATH="/tmp/node-v${NODE_VERSION}-linux-x64/bin:${PATH}"
fi
python manage.py tailwind install
python manage.py tailwind build

python manage.py collectstatic --no-input

# Applies new database migrations. Note: this runs against whatever DATABASE_URL points to.
python manage.py migrate --no-input
