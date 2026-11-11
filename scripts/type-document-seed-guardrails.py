#!/usr/bin/env python3
"""Guardrail: la copia de los tipos de documento del pais es identica a la siembra de ms-catalogs.

Los tipos de documento son un catalogo del pais (en Colombia, la tabla de la DIAN). ms-catalogs los siembra desde
`seed-<pais>-type-documents.json` y ms-microsoftgraph resuelve el codigo que llega del registro de Entra External ID
contra una copia de ese mismo fichero, embebida en el micro (Kappali pendings/663, regla 73). El usuario guarda el id del
tipo, asi que una copia con otro id, otro codigo u otro nombre deja usuarios apuntando a un tipo que el catalogo no
tiene, y la factura electronica sale con un tipo que no existe. No falla nada al compilar ni al arrancar.

Se comparan, pais por pais, todas las copias contra la de ms-catalogs: el mismo conjunto de tipos, con el mismo id, el
mismo codigo, el mismo nombre y la misma abreviatura (la que muestran las pantallas: CC, NIT).

Se corre desde la raiz del espacio de trabajo, donde se ven todos los micros. Con la raiz de un solo micro no hay nada
que comparar y el script no da veredicto.

    python workflows/scripts/type-document-seed-guardrails.py .
"""
import glob, json, os, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, AMARILLO, RESET = "\033[0;31m", "\033[0;32m", "\033[0;33m", "\033[0m"
DUENO = "CodeDesignPlus.Net.Microservice.Catalogs"
RE_FICHERO = re.compile(r"seed-([a-z]{2})-type-documents\.json$")


def micro_de(ruta):
    partes = ruta.replace("\\", "/").split("/")
    return next((p for p in partes if p.startswith(("CodeDesignPlus.Net.Microservice.", "Kappali.Net.Microservice."))), ruta)


def tipos(ruta):
    datos = json.load(open(ruta, encoding="utf-8-sig"))
    return {
        str(t["id"]).lower(): (str(t["code"]).strip().upper(), str(t["name"]).strip(), str(t.get("abbreviation") or "").strip())
        for t in datos
    }


ficheros = {}
for ruta in glob.glob(os.path.join(RAIZ, "*", "src", "**", "seed-*-type-documents.json"), recursive=True):
    normal = ruta.replace("\\", "/")
    if "/bin/" in normal or "/obj/" in normal:
        continue
    m = RE_FICHERO.search(normal)
    if m:
        ficheros.setdefault(m.group(1), []).append(normal)

print("Comparando las copias de los tipos de documento del pais con la siembra de ms-catalogs...\n")

dueno_visto = any(micro_de(r) == DUENO for rutas in ficheros.values() for r in rutas)
copias_vistas = any(micro_de(r) != DUENO for rutas in ficheros.values() for r in rutas)

if not dueno_visto or not copias_vistas:
    print(f"{AMARILLO}Sin veredicto: en esta vista no estan la siembra de ms-catalogs y alguna copia a la vez.{RESET}")
    print("Correlo desde la raiz del espacio de trabajo.")
    sys.exit(0)

fallos = []
for pais, rutas in sorted(ficheros.items()):
    origen = next((r for r in rutas if micro_de(r) == DUENO), None)
    if origen is None:
        for r in rutas:
            fallos.append(f"{micro_de(r)}: copia de '{pais}' sin siembra en ms-catalogs ({r})")
        continue

    esperado = tipos(origen)
    for r in rutas:
        if r == origen:
            continue
        copia = tipos(r)
        faltan = sorted(set(esperado) - set(copia))
        sobran = sorted(set(copia) - set(esperado))
        distintos = sorted(i for i in set(esperado) & set(copia) if esperado[i] != copia[i])
        for i in faltan:
            fallos.append(f"{micro_de(r)} ({pais}): falta {i} {esperado[i]}")
        for i in sobran:
            fallos.append(f"{micro_de(r)} ({pais}): sobra {i} {copia[i]}, que ms-catalogs no siembra")
        for i in distintos:
            fallos.append(f"{micro_de(r)} ({pais}): {i} es {copia[i]} y en ms-catalogs es {esperado[i]}")
        print(f"  {pais}: {micro_de(r)} contra ms-catalogs, {len(copia)} tipo(s)")

if fallos:
    print()
    for f in fallos:
        print(f"{ROJO}  FALLA{RESET}  {f}")
    print(f"\n{ROJO}{len(fallos)} diferencia(s) entre las copias y la siembra de ms-catalogs.{RESET}")
    print("El usuario guarda el id del tipo: una copia distinta lo deja apuntando a un tipo que el catalogo no tiene.")
    sys.exit(1)

print(f"\n{VERDE}Las copias de los tipos de documento son iguales a la siembra de ms-catalogs.{RESET}")
