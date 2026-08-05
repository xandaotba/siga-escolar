from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    if dictionary is None:
        return ""
    return dictionary.get(key, "")


def _to_decimal(valor):
    if valor is None or valor == "":
        return None

    try:
        texto = str(valor).strip().replace("R$", "").replace(" ", "")

        if not texto:
            return None

        if "," in texto and "." in texto:
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", ".")

        return Decimal(texto)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _formatar_decimal_br(valor, casas=2):
    numero = _to_decimal(valor)

    if numero is None:
        return ""

    quantizador = Decimal("1") if casas == 0 else Decimal("1").scaleb(-casas)
    numero = numero.quantize(quantizador, rounding=ROUND_HALF_UP)

    texto = f"{numero:,.{casas}f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    if casas == 0:
        texto = texto.split(",")[0]

    return texto


@register.filter
def quantidade_br(valor):
    """Exibe quantitativos como números inteiros. Ex.: 201,000 -> 201."""
    return _formatar_decimal_br(valor, casas=0)


@register.filter
def valor_br(valor):
    """Exibe valores com duas casas decimais, sem R$. Ex.: 1530 -> 1.530,00."""
    return _formatar_decimal_br(valor, casas=2)


@register.filter
def moeda_br(valor):
    """Exibe valores monetários com R$ e duas casas decimais."""
    texto = _formatar_decimal_br(valor, casas=2)
    return f"R$ {texto}" if texto else ""


@register.filter
def percentual_br(valor):
    """Exibe percentuais com duas casas decimais."""
    return _formatar_decimal_br(valor, casas=2)
