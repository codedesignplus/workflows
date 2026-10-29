#!/usr/bin/env python3
"""Un microservicio habla por un transporte, y solo por uno.

RabbitMQ y Service Bus conviven en el codigo: los dos entrypoints registran las dos
extensiones y quien manda es la configuracion, porque ambas se retiran solas si su
seccion esta apagada. Produccion va por Service Bus; staging, Docker y local siguen
con el RabbitMQ del cluster.

El fallo que caza este guardarrail: dejar los dos encendidos a la vez. Las dos
extensiones registran IMessage con TryAddSingleton, asi que no hay error ni aviso,
simplemente gana la primera que se registro -RabbitMQ- y los mensajes siguen yendo
al transporte viejo. En el pod todo parece correcto.

Se comprueba el valor EFECTIVO, no cada fichero por separado: la configuracion se
superpone (appsettings.json debajo, el del entorno encima), y una seccion puede estar
apagada en la base y encendida arriba, o al reves.

Un entrypoint que no usa mensajeria no tiene transporte que comprobar y se salta: es
el que no nombra RabbitMQ ni Service Bus en ningun sitio, ni en su configuracion (una
seccion RabbitMQ o ServiceBus), ni en su .csproj (el paquete), ni en su codigo (la
extension AddRabbitMQ o AddServiceBus). Asi pasa Kappali.Leads, una API minima sin
eventos, sin una lista de nombres a mano (pendings/700). Al que nombra alguno de los dos
se le exige uno encendido en cada entorno.

Sirve con la raiz de un micro (como lo corre el CI) o con la del espacio de trabajo.
Sin ningun entrypoint con mensajeria no hay nada que comparar y no da veredicto: sale
con 2, que no es ni verde ni rojo (como el de gemelos, pendings/695).

Uso:  python transport-guardrails.py [raiz]
"""
import glob
import io
import json
import os
import re
import sys

ENTORNOS = ['Development', 'Staging', 'Docker', 'Production']
SECCIONES = ('RabbitMQ', 'ServiceBus')
RE_CODIGO = re.compile(r'\bAdd(RabbitMQ|ServiceBus)\b|CodeDesignPlus\.Net\.(RabbitMQ|ServiceBus)\b')
VERDE = '\033[0;32m'
ROJO = '\033[0;31m'
AMARILLO = '\033[0;33m'
FIN = '\033[0m'


def norm(p):
    return p.replace(chr(92), '/')


def fuera_de_compilacion(p):
    return '/obj/' in norm(p) or '/bin/' in norm(p)


def cargar(p):
    try:
        return json.load(io.open(p, encoding='utf-8-sig'))
    except Exception:
        return {}


def habilitado(d, seccion):
    """Devuelve True, False o None si la seccion no dice nada."""
    s = d.get(seccion)
    if not isinstance(s, dict) or 'Enable' not in s:
        return None
    return bool(s['Enable'])


def efectivo(base, entorno, seccion):
    """El de arriba gana; si no dice nada, hereda el de la base."""
    v = habilitado(entorno, seccion)
    return habilitado(base, seccion) if v is None else v


def usa_mensajeria(d):
    """True si el entrypoint nombra RabbitMQ o Service Bus en su configuracion, su .csproj o su codigo."""
    for p in glob.glob(d + '/appsettings*.json'):
        cfg = cargar(p)
        if isinstance(cfg, dict) and any(s in cfg for s in SECCIONES):
            return True
    for p in glob.glob(d + '/**/*.cs', recursive=True) + glob.glob(d + '/*.csproj'):
        if fuera_de_compilacion(p):
            continue
        if RE_CODIGO.search(io.open(p, encoding='utf-8-sig', errors='ignore').read()):
            return True
    return False


def main():
    raiz = sys.argv[1] if len(sys.argv) > 1 else '.'
    os.chdir(raiz)

    # La raiz de un micro tiene src/ arriba; la del espacio de trabajo, un nivel mas abajo.
    entrypoints = sorted({norm(os.path.dirname(p))
                          for patron in ('src/entrypoints/*/appsettings.json',
                                         '*/src/entrypoints/*/appsettings.json')
                          for p in glob.glob(patron)
                          if not fuera_de_compilacion(p)})

    con_mensajeria = [d for d in entrypoints if usa_mensajeria(d)]
    sin_mensajeria = [d for d in entrypoints if d not in con_mensajeria]

    if not con_mensajeria:
        print()
        print('%sNada que comparar en \'%s\': %d entrypoint(s), ninguno con mensajeria.%s'
              % (AMARILLO, raiz, len(entrypoints), FIN))
        print('Este guardarrail se corre con la raiz de un micro que usa eventos o con la del espacio de trabajo.')
        sys.exit(2)

    rotos = []
    mudos = []
    revisados = 0

    for d in con_mensajeria:
        base = cargar(d + '/appsettings.json')
        for entorno in ENTORNOS:
            fichero = d + '/appsettings.%s.json' % entorno
            # Produccion no tiene fichero propio a proposito: carga solo la base.
            cfg = cargar(fichero) if os.path.exists(fichero) else {}
            rabbit = efectivo(base, cfg, 'RabbitMQ')
            bus = efectivo(base, cfg, 'ServiceBus')
            revisados += 1

            if rabbit and bus:
                rotos.append((d, entorno))
            elif not rabbit and not bus:
                mudos.append((d, entorno))

    print()
    if rotos:
        print('%sDOS TRANSPORTES ENCENDIDOS A LA VEZ%s' % (ROJO, FIN))
        print('Gana RabbitMQ en silencio y los mensajes no llegan a Service Bus.')
        print()
        for d, entorno in rotos:
            print('  %s  [%s]' % (d, entorno))
        print()

    if mudos:
        print('%sSIN NINGUN TRANSPORTE%s' % (ROJO, FIN))
        print('El microservicio usa mensajeria pero en este entorno arranca sin publicar ni consumir nada.')
        print()
        for d, entorno in mudos:
            print('  %s  [%s]' % (d, entorno))
        print()

    if sin_mensajeria:
        print('entrypoints sin mensajeria, sin transporte que comprobar (%d):' % len(sin_mensajeria))
        for d in sin_mensajeria:
            print('    %s' % d)
        print()

    print('entrypoints revisados: %d   combinaciones entorno/entrypoint: %d'
          % (len(con_mensajeria), revisados))

    if rotos or mudos:
        sys.exit(1)

    print('%sCada entrypoint con mensajeria habla por un solo transporte en cada entorno.%s' % (VERDE, FIN))


if __name__ == '__main__':
    main()
