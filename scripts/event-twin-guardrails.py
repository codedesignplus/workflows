#!/usr/bin/env python3
"""Guardrail: cada gemelo tiene que apuntar a un intercambio que alguien publique de verdad.

El nombre del intercambio es {business}.{appName}.{version}.{entity}.{event}, y `entity` sale del atributo.
Con la forma generica —EventKey<Agregado>— el compilador coge el agregado que este a mano, que en un
consumidor suele ser uno SUYO y no el del microservicio que publica.

El resultado es una cola atada a un intercambio al que nadie publica, y no se ve por ninguna parte: la cola
existe, esta 'running' y tiene consumidor, porque es el propio consumidor quien crea ese intercambio al
suscribirse. En el broker todo parece correcto y el mensaje no llega nunca.

Asi estuvo la regla contable de las areas comunes: al configurar el cobro, facturacion creaba su producto y
contabilidad no creaba nada, con lo que la reserva se cobraba sin llegar al mayor.

    python event-twin-guardrails.py [raiz]
"""
import glob, json, os, re, sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
ROJO, VERDE, AMARILLO, RESET = "\033[0;31m", "\033[0;32m", "\033[0;33m", "\033[0m"

RE_GEN = re.compile(r'\[EventKey<(\w+)>\(\s*(\d+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]*)")?(?:\s*,\s*"([^"]*)")?\s*\)\]')
RE_STR = re.compile(r'\[EventKey\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]*)")?(?:\s*,\s*"([^"]*)")?\s*\)\]')


def opciones(micro):
    """AppName y Business del microservicio, de su appsettings."""
    for ep in glob.glob(f"{micro}/src/entrypoints/*/appsettings.json"):
        p = ep.replace("\\", "/")
        if "/obj/" in p or "/bin/" in p:
            continue
        try:
            core = json.load(open(ep, encoding="utf-8-sig")).get("Core", {})
        except Exception:
            continue
        if core.get("AppName"):
            return core["AppName"].lower(), core.get("Business", "").lower()
    return None


def eventos(micro, app, bus):
    """Todos los [EventKey] del microservicio, con su intercambio resuelto."""
    salida = []
    for path in glob.glob(f"{micro}/src/**/*.cs", recursive=True):
        p = path.replace("\\", "/")
        if "/obj/" in p or "/bin/" in p:
            continue
        txt = open(path, encoding="utf-8", errors="ignore").read()
        for regex, gen in ((RE_GEN, True), (RE_STR, False)):
            for m in regex.finditer(txt):
                ent, ver, ev = (m.group(1), m.group(2), m.group(3)) if gen else (m.group(1), m.group(2), m.group(3))
                a, b = m.group(4), m.group(5)
                salida.append(dict(
                    path=p, linea=txt[:m.start()].count("\n") + 1, generico=gen, entity=ent,
                    exchange=f"{(b or bus)}.{(a or app)}.v{ver}.{ent}.{ev}".lower(),
                    propio="/src/domain/" in p, texto=m.group(0)))
    return salida


micros = {}
for d in sorted(glob.glob(f"{RAIZ}/*.Net.Microservice.*")):
    o = opciones(d)
    if o:
        micros[d] = o

todos = []
for micro, (app, bus) in micros.items():
    todos.extend(eventos(micro, app, bus))

# Un intercambio tiene publicador si el evento se declara en la capa de dominio de algun microservicio:
# los gemelos viven siempre en entrypoints.
publicados = {e["exchange"] for e in todos if e["propio"]}

# Deuda ya archivada en pendings/52: consumidores que esperan eventos que nadie llego a construir. Es un
# problema distinto —ahi el evento no existe, no es que el gemelo lo nombre mal— y esta pendiente de decidir
# si se borra el consumidor o se escribe el publicador. Se listan uno a uno a proposito: una lista explicita
# se revisa, un "ignorar lo de parking" se olvida.
CONOCIDOS = {
    "codedesignplus.ms-tenants.v1.tenantaggregate.tenantprovisioningfailedfororderdomainevent",
    "codedesignplus.ms-users.v1.useraggregate.userprovisioningfailedfororderdomainevent",
    "kappali.ms-deposits.v1.depositleaseaggregate.depositleasechargerequireddomainevent",
    "kappali.ms-parking.v1.parkingleaseaggregate.parkingleasechargerequireddomainevent",
    "kappali.ms-communitylife.v1.communitylifeaggregate.attentioncallcreateddomainevent",
    "kappali.ms-communitylife.v1.communitylifeaggregate.attentioncallresolveddomainevent",
    "kappali.ms-administration.v1.administrationaggregate.debtstatuschangeddomainevent",
}

huerfanos = [e for e in todos if not e["propio"] and e["exchange"] not in publicados]

rotos = [e for e in huerfanos if e["exchange"] not in CONOCIDOS]
conocidos = [e for e in huerfanos if e["exchange"] in CONOCIDOS]

print("Comprobando que cada gemelo apunte a un intercambio con publicador...\n")

for e in rotos:
    print(f"{ROJO}  GEMELO SIN PUBLICADOR{RESET}  {e['path'].split('/src/')[0]}")
    print(f"      {e['path'].split('/')[-1]}:{e['linea']}   {e['texto']}")
    print(f"{AMARILLO}      espera: {e['exchange']}")
    if e["generico"]:
        print(f"      la forma generica cogio '{e['entity']}'. Nombre el agregado del publicador con la")
        print(f"      forma de texto: [EventKey(\"AgregadoDelPublicador\", ...)]{RESET}\n")
    else:
        print(f"      no hay ningun evento de dominio que resuelva a ese nombre.{RESET}\n")

if conocidos:
    print(f"{AMARILLO}deuda conocida, ya archivada en pendings/52 ({len(conocidos)} gemelos):{RESET}")
    for e in sorted(conocidos, key=lambda x: x["exchange"]):
        print(f"    {e['exchange']}")
    print()

print(f"gemelos revisados: {sum(1 for e in todos if not e['propio'])}   eventos publicados: {len(publicados)}")

if rotos:
    print(f"\n{ROJO}{len(rotos)} gemelo(s) atados a un intercambio al que nadie publica.{RESET}")
    print("\nNo se detecta desde RabbitMQ: la cola existe, corre y tiene consumidor. Solo se nota porque el")
    print("mensaje no llega nunca, y para entonces el despliegue ya se dio por bueno.")
    sys.exit(1)

print(f"\n{VERDE}Todos los gemelos apuntan a un intercambio con publicador.{RESET}")
