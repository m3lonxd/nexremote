#!/usr/bin/env bash
# Siteyi Vercel'e (production) yayınlar.
#
# Vercel Hobby planı, Git bilgisi taşıyan bir dağıtımı commit yazarı ekip
# sahibinin GitHub hesabı değilse BLOCKED bırakır. Site statik ve Vercel'de
# derlenmediği için Git bilgisine gerek yok: site/ Git dışı geçici bir kopyaya
# alınır ve oradan dağıtılır. .env.local (Vercel OIDC token'ı) kopyalanmaz.
#
#   bash site/deploy.sh            # ~/nexremote kökünden ya da site/ içinden
set -euo pipefail

SITE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCOPE="${VERCEL_SCOPE:-1337om3rs-projects}"

command -v vercel >/dev/null || { echo "vercel CLI yok: npm i -g vercel" >&2; exit 1; }
[[ -f "$SITE/.vercel/project.json" ]] || {
    echo "site bağlı değil: cd site && vercel link --yes --project nexremote --scope $SCOPE" >&2
    exit 1
}

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
rsync -a --exclude='.env*' --exclude='__pycache__' "$SITE/" "$STAGE/site/"

cd "$STAGE/site"
vercel deploy --prod --yes --scope "$SCOPE"
