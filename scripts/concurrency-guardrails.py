#!/usr/bin/env python3
"""Guardrail: en un micro que lo adopto, todo agregado se versiona o declara por que no hace falta.

Dos consumidores que leen el mismo documento, lo cambian en memoria y lo reemplazan entero se borran el uno al otro sin
un error: asi perdio el panel 88 de las 240 cuotas de octubre de Cuatro Vientos y la T2-601 perdio el numero de su
cuenta de cobro (pendings/346, regla 59 de rules/). La cura es IVersionedAggregate del SDK, que hace que el segundo
choque y se reintente en vez de borrar.

La regla entra micro a micro: aplica a los repos que tienen el fichero `.concurrency-guardrails` en la raiz. Ese
fichero lista los agregados que NO se versionan, uno por linea y con su motivo detras de `#`:

    ChargeRunAggregate  # una por corrida; solo la escribe la corrida, en un unico proceso

El script exige, para cada clase del dominio que hereda de AggregateRoot o AggregateRootBase:
- que implemente IVersionedAggregate, o
- que figure en el fichero con un motivo.
Y falla tambien si una excepcion no tiene motivo, nombra un agregado que no existe o uno que ya se versiona: una
excepcion que sobrevive a su motivo es como se pudre un guardarrail. Un agregado que hereda de otra clase del dominio
cuenta como agregado; una clase abstracta no, porque no se guarda: se clasifican las concretas.

En todo micro, haya adoptado la regla o no, cada clase que implementa IVersionedAggregate (ella o una de sus bases)
declara en su propio cuerpo `long Version { get; private set; }`. El repositorio del SDK mueve la version por ese
setter privado buscandolo en el tipo concreto, y el `private set` de una clase base no se ve desde la derivada: compila,
pero cada escritura lanza «implements IVersionedAggregate but has no 'long Version { get; private set; }' property» y el
mensaje acaba en el descartado. Asi quedaron sin aplicarse las fotos de la UVT y del salario minimo en ms-communitylife
(pendings/514).

    python concurrency-guardrails.py [raiz]
"""
import glob, os, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, RESET = "\033[0;31m", "\033[0;32m", "\033[0m"
FICHERO = os.path.join(RAIZ, ".concurrency-guardrails")

RE_MODIFICADORES = r"((?:(?:public|internal|protected|private|sealed|abstract|static|partial|file)\s+)*)"
RE_CLASE = re.compile(r"\b" + RE_MODIFICADORES + r"class\s+(\w+)\s*(?:<[^>{]*>)?\s*(?:\([^)]*\))?\s*:\s*([^{]+)\{", re.S)
RE_VERSION = re.compile(r"\blong\s+Version\s*\{\s*get\s*;\s*private\s+set\s*;\s*\}")


def cuerpo(txt, inicio):
    """El cuerpo de la clase cuya llave abre en `inicio`."""
    nivel = 0
    for i in range(inicio, len(txt)):
        if txt[i] == "{":
            nivel += 1
        elif txt[i] == "}":
            nivel -= 1
            if nivel == 0:
                return txt[inicio:i + 1]
    return txt[inicio:]


def sin_comentarios(txt):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", txt, flags=re.S))


# Todas las clases de src/: nombre -> (bases, abstracta, declara su Version, ruta, es del dominio)
clases = {}
for path in glob.glob(f"{RAIZ}/**/src/**/*.cs", recursive=True):
    p = path.replace("\\", "/")
    if "/obj/" in p or "/bin/" in p:
        continue
    txt = sin_comentarios(open(path, encoding="utf-8-sig", errors="ignore").read())
    for m in RE_CLASE.finditer(txt):
        nombres = {b.strip().split("<")[0].split("(")[0].strip() for b in m.group(3).split(",")}
        declara = bool(RE_VERSION.search(cuerpo(txt, m.end() - 1)))
        clases[m.group(2)] = (nombres, "abstract" in m.group(1).split(), declara, p, ".Domain/" in p)


def hereda(nombre, objetivos, vistos=()):
    """Si la clase, o una de sus bases que estan en src/, nombra alguno de `objetivos` entre sus bases."""
    if nombre not in clases or nombre in vistos:
        return False
    bases = clases[nombre][0]
    return bool(bases & objetivos) or any(hereda(b, objetivos, vistos + (nombre,)) for b in bases)


hallazgos = []
for nombre, (_, _, declara, path, _) in sorted(clases.items()):
    if hereda(nombre, {"IVersionedAggregate"}) and not declara:
        hallazgos.append(f"{path}: {nombre} implementa IVersionedAggregate pero no declara en su cuerpo"
                         f" 'long Version {{ get; private set; }}': el repositorio del SDK mueve la version por ese setter y lo busca en el tipo concreto, donde el de una base no se ve")

if not os.path.exists(FICHERO):
    if hallazgos:
        for h in hallazgos:
            print(f"{ROJO}  FALLA {h}{RESET}")
        print(f"\n{ROJO}Cada agregado versionado declara su propia version; si varios comparten una base, la interfaz y la"
              f" version van en cada clase concreta: regla 59 de rules/ y pendings/514.{RESET}")
        sys.exit(1)
    print(f"{VERDE}  OK  Concurrencia optimista: el micro aun no la adopto (sin .concurrency-guardrails){RESET}")
    sys.exit(0)

# Los agregados que se guardan: concretos, del dominio y que heredan, directa o indirectamente, de la raiz del SDK.
agregados = {
    nombre: (hereda(nombre, {"IVersionedAggregate"}), path)
    for nombre, (_, abstracta, _, path, dominio) in clases.items()
    if dominio and not abstracta and hereda(nombre, {"AggregateRoot", "AggregateRootBase"})
}
excepciones = {}
for numero, linea in enumerate(open(FICHERO, encoding="utf-8-sig"), start=1):
    contenido = linea.strip()
    if not contenido or contenido.startswith("#"):
        continue
    nombre, _, motivo = contenido.partition("#")
    nombre, motivo = nombre.strip(), motivo.strip()
    if not motivo:
        hallazgos.append(f".concurrency-guardrails:{numero}: {nombre} no dice por que no se versiona")
    excepciones[nombre] = motivo

for nombre in sorted(excepciones):
    if nombre not in agregados:
        hallazgos.append(f".concurrency-guardrails: {nombre} no es un agregado del dominio; borre la excepcion")
    elif agregados[nombre][0]:
        hallazgos.append(f".concurrency-guardrails: {nombre} ya implementa IVersionedAggregate; borre la excepcion")

for nombre, (versionado, path) in sorted(agregados.items()):
    if not versionado and nombre not in excepciones:
        hallazgos.append(f"{path}: {nombre} no implementa IVersionedAggregate ni esta en .concurrency-guardrails con su motivo")

if hallazgos:
    for h in hallazgos:
        print(f"{ROJO}  FALLA {h}{RESET}")
    print(f"\n{ROJO}Un documento que escriben dos procesos se versiona y se reintenta (IVersionedAggregate y"
          f" UpdateWithRetryAsync); si no hace falta, se declara por que. Y cada clase versionada declara su propia version: regla 59 de rules/.{RESET}")
    sys.exit(1)

versionados = sum(1 for v, _ in agregados.values() if v)
print(f"{VERDE}  OK  Concurrencia optimista: {versionados} agregado(s) versionado(s), {len(excepciones)} excepcion(es) declarada(s){RESET}")
