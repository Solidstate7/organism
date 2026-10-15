# where.sh: shell side of where.py (source it). The rules live in where.py only,
# so both languages always agree; this file just asks it.
#   organism_where root|hooks|state|logs|mode|pairings|pairings-write|cache
_ORGANISM_WHERE_PY="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd -P)/where.py"
organism_where() { python3 "$_ORGANISM_WHERE_PY" "$@"; }
