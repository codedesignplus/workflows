#!/usr/bin/env bash
#
# Guardrail: los filtros de Criteria no admiten espacios alrededor del operador.
#
# El parser del SDK no recorta el nombre de la propiedad, asi que "Status = 1" busca una propiedad
# llamada 'Status ' —con el espacio dentro— y lanza:
#
#   CriteriaException: Instance property 'Status ' is not defined for type '...'
#
# No falla al compilar. Falla en tiempo de ejecucion, con un 500, y solo cuando alguien abre esa pantalla.
# Asi estuvieron caidos los cinco informes contables, el de documentos vencidos y el de ingresos sin que
# nadie se enterara.
#
# Uso:  bash workflows/scripts/criteria-guardrails.sh [microservicio ...]
#       sin argumentos, revisa todos.

set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

VERDE='\033[32m'; ROJO='\033[31m'; RESET='\033[0m'

if [ "$#" -gt 0 ]; then
  OBJETIVOS=("$@")
else
  mapfile -t OBJETIVOS < <(find . -maxdepth 1 -type d -name "*.Net.Microservice.*" -printf "%f\n" | sort)
fi

# Un nombre de propiedad seguido de espacio(s) y un operador, dentro de la cadena de Filters.
PATRON='Filters[[:space:]]*=[[:space:]]*\$?"[^"]*[A-Za-z] +[=<>~!]'

hallazgos=0

for objetivo in "${OBJETIVOS[@]}"; do
  [ -d "$objetivo" ] || continue

  while IFS= read -r linea; do
    [ -z "$linea" ] && continue
    printf "${ROJO}  ESPACIO EN EL FILTRO  %s${RESET}\n" "$linea"
    hallazgos=$((hallazgos + 1))
  done < <(grep -rnE "$PATRON" --include=*.cs "$objetivo" 2>/dev/null | grep -v "/obj/\|/bin/")
done

echo

if [ "$hallazgos" -gt 0 ]; then
  printf "${ROJO}%d filtro(s) con espacios alrededor del operador.${RESET}\n" "$hallazgos"
  echo "Escribalos pegados: \"Status=1\", no \"Status = 1\". El separador de condiciones es ',' o '|and|'."
  exit 1
fi

printf "${VERDE}Sin filtros de Criteria con espacios.${RESET}\n"
