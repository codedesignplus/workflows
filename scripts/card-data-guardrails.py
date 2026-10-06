#!/usr/bin/env python3
"""Guardrail: el codigo de seguridad de una tarjeta no se guarda en ninguna base.

PCI DSS (requisito 3.2) prohibe conservar el CVV despues de la autorizacion, incluso cifrado. Se guardaba en claro en
ms-payments, ms-licenses y ms-commonareas porque el objeto de valor del SDK que llevan sus agregados
(CodeDesignPlus.Net.ValueObjects.Payment.CreditCard) trae el SecurityCode para que llegue a la pasarela (pendings/322).

El script falla si:
- un proyecto de dominio declara una propiedad SecurityCode, Cvv o Cvc: el dominio es lo que se persiste;
- un proyecto de dominio guarda el PaymentMethod o el CreditCard del SDK y el micro no registra el mapa de Mongo que
  quita el SecurityCode (UnmapProperty(... SecurityCode)).

    python card-data-guardrails.py [root]
"""
import glob
import re
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
RED, GREEN, RESET = "\033[0;31m", "\033[0;32m", "\033[0m"

SECRET_PROPERTY = re.compile(r"\b(?:public|private|protected|internal)\s+[\w<>?.]+\s+(SecurityCode|Cvv2?|Cvc2?)\s*\{")
SDK_PAYMENT_NAMESPACE = re.compile(r"\busing\s+CodeDesignPlus\.Net\.ValueObjects\.Payment\s*;")
SDK_CARD_PROPERTY = re.compile(r"\bpublic\s+(?:CodeDesignPlus\.Net\.ValueObjects\.Payment\.)?(PaymentMethod|CreditCard)\??\s+\w+\s*\{\s*get")
UNMAP_SECURITY_CODE = re.compile(r"UnmapProperty\s*\(\s*\w+\s*=>\s*\w+\.SecurityCode\s*\)")


def source_files():
    for path in glob.glob(f"{ROOT}/**/src/**/*.cs", recursive=True):
        normalized = path.replace("\\", "/")
        if "/obj/" in normalized or "/bin/" in normalized:
            continue
        yield normalized, open(path, encoding="utf-8-sig", errors="ignore").read()


def is_domain(path):
    return any(part.endswith(".Domain") for part in path.split("/"))


files = list(source_files())
domain_files = [(path, text) for path, text in files if is_domain(path)]

# El using puede ser global (Usings.cs) o del propio archivo: basta con que el proyecto de dominio lo importe.
domain_imports_sdk_payment = any(SDK_PAYMENT_NAMESPACE.search(text) for _, text in domain_files)
has_unmap = any(UNMAP_SECURITY_CODE.search(text) for _, text in files)

findings = []

for path, text in domain_files:
    for number, line in enumerate(text.splitlines(), start=1):
        match = SECRET_PROPERTY.search(line)
        if match:
            findings.append(f"{path}:{number}: el dominio declara {match.group(1)}, que no puede guardarse")

if domain_imports_sdk_payment and not has_unmap:
    for path, text in domain_files:
        for number, line in enumerate(text.splitlines(), start=1):
            match = SDK_CARD_PROPERTY.search(line)
            if match:
                findings.append(
                    f"{path}:{number}: guarda el {match.group(1)} del SDK y el micro no registra el mapa de Mongo que quita"
                    f" el SecurityCode")

if findings:
    for finding in findings:
        print(f"{RED}  FALLA {finding}{RESET}")
    print(f"\n{RED}El CVV solo viaja hasta la pasarela. Un agregado que guarde el medio de pago del SDK registra en"
          f" Infrastructure un BsonClassMap con UnmapProperty(card => card.SecurityCode): pendings/322.{RESET}")
    sys.exit(1)

print(f"{GREEN}  OK  Ningun dominio guarda el codigo de seguridad de una tarjeta{RESET}")
