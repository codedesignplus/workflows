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
excepcion que sobrevive a su motivo es como se pudre un guardarrail.

    python concurrency-guardrails.py [raiz]
"""
import glob, os, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, RESET = "\033[0;31m", "\033[0;32m", "\033[0m"
FICHERO = os.path.join(RAIZ, ".concurrency-guardrails")

if not os.path.exists(FICHERO):
    print(f"{VERDE}  OK  Concurrencia optimista: el micro aun no la adopto (sin .concurrency-guardrails){RESET}")
    sys.exit(0)

RE_CLASE = re.compile(r"\bclass\s+(\w+)\s*(?:<[^>{]*>)?\s*(?:\([^)]*\))?\s*:\s*([^{]+)\{", re.S)

agregados = {}
for path in glob.glob(f"{RAIZ}/**/src/**/*.Domain/**/*.cs", recursive=True):
    p = path.replace("\\", "/")
    if "/obj/" in p or "/bin/" in p:
        continue
    txt = open(path, encoding="utf-8-sig", errors="ignore").read()
    for clase, bases in RE_CLASE.findall(txt):
        nombres = {b.strip().split("<")[0].split("(")[0].strip() for b in bases.split(",")}
        if nombres & {"AggregateRoot", "AggregateRootBase"}:
            agregados[clase] = ("IVersionedAggregate" in nombres, p)

hallazgos = []
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
          f" UpdateWithRetryAsync); si no hace falta, se declara por que: regla 59 de rules/.{RESET}")
    sys.exit(1)

versionados = sum(1 for v, _ in agregados.values() if v)
print(f"{VERDE}  OK  Concurrencia optimista: {versionados} agregado(s) versionado(s), {len(excepciones)} excepcion(es) declarada(s){RESET}")
