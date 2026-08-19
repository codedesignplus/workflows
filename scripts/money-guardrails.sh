#!/usr/bin/env bash
#
# Guardrails del estandar de dinero y tasas para microservicios .NET.
#
# El compilador no protege este estandar: Money.FromDecimal(unLong, ...) compila sin queja por
# conversion implicita de long a decimal, y un VO de dominio en la firma de un command es codigo
# perfectamente valido. Estas reglas son la unica red.
#
# Uso local, desde la raiz del repo del microservicio:
#   bash money-guardrails.sh
#
# Excepciones: cree un archivo .money-guardrails-ignore en la raiz del repo con un patron por
# linea (se compara contra "archivo:linea:contenido"). Toda excepcion debe llevar encima un
# comentario que explique por que, para que la siguiente persona no la borre ni la imite.
#
# Las reglas y su porque estan en Microservices/rules/.

set -uo pipefail

ROOT="${1:-.}"
FAILED=0

RED=$'\033[31m'
GREEN=$'\033[32m'
RESET=$'\033[0m'

IGNORE_FILE="$ROOT/.money-guardrails-ignore"

# Solo codigo de produccion: los tests construyen datos a proposito y no siguen el estandar.
# Se excluyen tambien artefactos de compilacion y el codigo de los source generators.
find_cs() {
  find "$ROOT" -path '*/src/*' -name '*.cs' -not -path '*/obj/*' -not -path '*/bin/*' -not -name '*.g.cs' "$@"
}

# ¿Este tipo transporta dinero o tasas?
#
# La regla sobre *Dto existe por las unidades, no por el sufijo: un ScheduleRuleDto o un ImageDto
# en un command es un nombre desafortunado, no un riesgo. Se resuelve la definicion del tipo y se
# mira si tiene miembros monetarios; si no los tiene, no hay nada que romper.
carries_money() {
  local type_name="$1" definition

  definition=$(find_cs -name "${type_name}.cs" | head -1)

  [ -z "$definition" ] && return 1

  grep -qE '\b(Money|decimal|BasisPoints|Currency)\b|\blong\??\s+[A-Za-z]*(Amount|Price|Salary|Fee|Cost|Total|Balance|Base)' "$definition"
}

# Quita las lineas cubiertas por una excepcion declarada.
apply_ignore() {
  if [ -f "$IGNORE_FILE" ]; then
    grep -vFf <(grep -vE '^\s*(#|$)' "$IGNORE_FILE") || true
  else
    cat
  fi
}

report() {
  local rule="$1" explanation="$2" matches="$3"

  matches=$(printf '%s' "$matches" | grep -v '^$' | apply_ignore)

  if [ -z "$matches" ]; then
    printf '%s  OK  %s%s\n' "$GREEN" "$rule" "$RESET"
    return
  fi

  FAILED=1
  printf '%s FALLA %s%s\n' "$RED" "$rule" "$RESET"
  printf '       %s\n\n' "$explanation"
  printf '%s\n\n' "$matches" | sed 's/^/       /'
}

# ─── Commands que llegan desde un cliente HTTP ───────────────────────────────────
#
# La distincion es la bisagra de casi todo el estandar. Un command disparado por un evento de
# dominio recibe unidades menores y puntos base, y eso es correcto: es el formato en que viajan
# los eventos entre microservicios. Un command que expone un controller lo llena una persona
# desde un formulario, y ahi los importes van en unidad mayor y las tasas en porcentaje.
#
# Se detecta por referencia: si algun *Controller.cs nombra al command, es de cara al cliente.
rest_commands() {
  local file command_name

  while IFS= read -r file; do
    [ -z "$file" ] && continue
    command_name=$(basename "$file" .cs)

    if grep -rqlF "$command_name" --include='*Controller.cs' "$ROOT" 2>/dev/null; then
      printf '%s\n' "$file"
    fi
  done < <(find_cs -name '*Command.cs' -path '*/Commands/*')
}

# Busca un patron solo dentro de los commands expuestos por REST.
grep_rest_commands() {
  local pattern="$1"
  local files

  files=$(rest_commands)
  [ -z "$files" ] && return 0

  printf '%s\n' "$files" | tr '\n' '\0' | xargs -0 grep -nE "$pattern" 2>/dev/null
}

echo "Guardrails de dinero — $(cd "$ROOT" && pwd)"
echo

# ─── 1. La conversion ocurre al escribir, nunca al leer ──────────────────────────
#
# Un query handler que convierte esta devolviendo unidades mayores, cuando el contrato de lectura
# es en unidades menores. Ademas mete una llamada gRPC en el camino mas frecuente de todos.
report "Money.FromDecimal en Queries" \
  "La conversion pertenece a la escritura. Los queries devuelven unidades menores tal cual." \
  "$(find_cs -path '*/Queries/*' -print0 | xargs -0 grep -n 'Money\.FromDecimal(' 2>/dev/null)"

# El inverso: un command que convierte a unidad mayor esta deshaciendo lo que acaba de recibir.
report "ToDecimal en Commands" \
  "Un command no devuelve importes al cliente. Si un tercero los necesita en unidad mayor, eso va en su adaptador." \
  "$(find_cs -path '*/Commands/*' -print0 | xargs -0 grep -n '\.ToDecimal(' 2>/dev/null)"

# ─── 2. La forma del contrato de escritura ───────────────────────────────────────
#
# Todo lo que sigue aplica solo a commands expuestos por REST, por la razon explicada arriba.

# Un *Dto es forma de lectura: sus importes vienen en unidad menor. Usarlo como entrada obliga
# al cliente a enviar centavos y reintroduce el bug de factor 100 en cada pantalla de edicion.
dto_hits=""
while IFS= read -r hit; do
  [ -z "$hit" ] && continue

  dto_type=$(printf '%s' "$hit" | grep -oE '[A-Za-z]+Dto' | head -1)

  if carries_money "$dto_type"; then
    dto_hits+="$hit"$'\n'
  fi
done < <(grep_rest_commands '^\s*(List<)?[A-Za-z]+Dto[>?]*\s+[A-Za-z]')

report "Tipos *Dto con dinero en un command expuesto por REST" \
  "Los *Dto son contrato de lectura y sus importes vienen en unidad menor. Para escritura use un *Input." \
  "$dto_hits"

# Los VOs de dominio guardan unidades menores y puntos base: imposible enviarles decimales.
#
# El patron no puede anclarse a inicio de linea. Un record posicional de una sola linea
#   public record Comando(Guid Id, List<UnitAssessment>? Unidades = null) : IRequest;
# empieza por "public", asi que el ancla no casaba y dejaba pasar CUALQUIER VO escrito de esa forma, no
# solo este. Se busca el tipo alla donde este, precedido de un delimitador de parametro o de propiedad,
# que es lo que distingue "List<Money> X" de un "MoneyInput" perfectamente valido.
VO_TYPES='Money|TaxDefinition|WithholdingDefinition|PenaltyRule|LeaseTerms|UnitAssessment'

report "VOs de dominio en un command expuesto por REST" \
  "Estos commands los llena una persona: deben recibir *Input en unidad mayor y porcentaje." \
  "$(grep_rest_commands "(^|[(,[:space:]])(List<)?($VO_TYPES)[>?]*[[:space:]]+[A-Za-z]")"

# Un importe long obliga al cliente a hacer la conversion, que es justo lo que no debe hacer.
report "Importes long en un command expuesto por REST" \
  "Un importe que teclea el usuario viaja en unidad mayor dentro de un MoneyInput." \
  "$(grep_rest_commands '^\s*long\??\s+[A-Za-z]*(Amount|Price|Salary|Fee|Cost|Total|Balance|MinimumBase)')"

# Las tasas siguen la misma asimetria: el usuario escribe 19, el backend guarda 1900.
report "Puntos base en un command expuesto por REST" \
  "El usuario escribe porcentaje. Use decimal *Percentage y convierta con BasisPoints.FromPercentage." \
  "$(grep_rest_commands '^\s*int\??\s+[A-Za-z]*BasisPoints')"

# ─── 3. Ninguna moneda quemada en el codigo ──────────────────────────────────────
#
# La moneda sale del tenant, del documento o del template. Un literal la fija para siempre y no
# lanza nada: simplemente cobra en la divisa equivocada. Se ignoran los comentarios de
# documentacion, que la usan como ejemplo.
report "Codigo de moneda literal" \
  "La moneda se resuelve del tenant o del agregado. Nunca se escribe en el codigo." \
  "$(find_cs -path '*/src/*' -print0 \
      | xargs -0 grep -nE '"(COP|USD|EUR|MXN|BRL|PEN|ARS|CLP)"' 2>/dev/null \
      | grep -vE '///|e\.g\.|ej\.')"

echo
if [ "$FAILED" -ne 0 ]; then
  printf '%sHay violaciones del estandar de dinero. El porque de cada regla esta en Microservices/rules/.%s\n' "$RED" "$RESET"
  exit 1
fi

printf '%sSin violaciones del estandar de dinero.%s\n' "$GREEN" "$RESET"
