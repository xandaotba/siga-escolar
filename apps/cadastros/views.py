import json
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .forms import EscolaForm, FornecedorForm, ItemForm, MunicipioForm
from .models import Escola, Fornecedor, Item, Municipio, UnidadeMedida, validar_cnpj



def paginar_queryset(request, queryset, por_pagina=25):
    paginator = Paginator(queryset, por_pagina)
    numero_pagina = request.GET.get("page")
    return paginator.get_page(numero_pagina)



def normalizar_codigo_unidade(texto):
    texto = (texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = "".join(ch if ch.isalnum() else "_" for ch in texto)
    texto = "_".join(parte for parte in texto.split("_") if parte)
    return texto[:50]


def garantir_unidades_medida_padrao():
    unidades_padrao = [
        ("kg", "Quilograma", "KG"),
        ("litro", "Litro", "L"),
        ("unidade", "Unidade", "UND"),
        ("pacote", "Pacote", "PCT"),
        ("caixa", "Caixa", "CX"),
        ("duzia", "Dúzia", "DZ"),
    ]

    for codigo, nome, sigla in unidades_padrao:
        UnidadeMedida.objects.get_or_create(
            codigo=codigo,
            defaults={
                "nome": nome,
                "sigla": sigla,
                "ativo": True,
            },
        )


def unidades_medida(request):
    garantir_unidades_medida_padrao()

    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()
        sigla = request.POST.get("sigla", "").strip().upper()
        codigo = request.POST.get("codigo", "").strip()

        if not codigo:
            codigo = normalizar_codigo_unidade(sigla or nome)
        else:
            codigo = normalizar_codigo_unidade(codigo)

        if not nome:
            messages.error(request, "Informe o nome da unidade de medida.")
            return redirect("cadastros:unidades_medida")

        if not codigo:
            messages.error(request, "Não foi possível gerar o código interno da unidade.")
            return redirect("cadastros:unidades_medida")

        if UnidadeMedida.objects.filter(codigo=codigo).exists():
            messages.error(request, "Já existe uma unidade de medida com este código interno.")
            return redirect("cadastros:unidades_medida")

        UnidadeMedida.objects.create(
            nome=nome,
            sigla=sigla,
            codigo=codigo,
        )

        messages.success(request, "Unidade de medida cadastrada com sucesso.")
        return redirect("cadastros:unidades_medida")

    unidades_lista = UnidadeMedida.objects.all().order_by("nome")

    return render(
        request,
        "cadastros/unidades_medida.html",
        {
            "unidades": unidades_lista,
            "modo_edicao": False,
            "unidade_edicao": None,
        },
    )


def editar_unidade_medida(request, unidade_id):
    garantir_unidades_medida_padrao()

    unidade = get_object_or_404(UnidadeMedida, id=unidade_id)

    if request.method == "POST":
        nome = request.POST.get("nome", "").strip()
        sigla = request.POST.get("sigla", "").strip().upper()
        codigo = request.POST.get("codigo", "").strip()

        if not codigo:
            codigo = unidade.codigo
        else:
            codigo = normalizar_codigo_unidade(codigo)

        if not nome:
            messages.error(request, "Informe o nome da unidade de medida.")
            return redirect("cadastros:editar_unidade_medida", unidade_id=unidade.id)

        if UnidadeMedida.objects.filter(codigo=codigo).exclude(id=unidade.id).exists():
            messages.error(request, "Já existe outra unidade de medida com este código interno.")
            return redirect("cadastros:editar_unidade_medida", unidade_id=unidade.id)

        unidade.nome = nome
        unidade.sigla = sigla
        unidade.codigo = codigo
        unidade.save(update_fields=["nome", "sigla", "codigo", "atualizado_em"])

        messages.success(request, "Unidade de medida atualizada com sucesso.")
        return redirect("cadastros:unidades_medida")

    unidades_lista = UnidadeMedida.objects.all().order_by("nome")

    return render(
        request,
        "cadastros/unidades_medida.html",
        {
            "unidades": unidades_lista,
            "modo_edicao": True,
            "unidade_edicao": unidade,
        },
    )


def alternar_status_unidade_medida(request, unidade_id):
    unidade = get_object_or_404(UnidadeMedida, id=unidade_id)

    unidade.ativo = not unidade.ativo
    unidade.save(update_fields=["ativo", "atualizado_em"])

    if unidade.ativo:
        messages.success(request, "Unidade de medida ativada com sucesso.")
    else:
        messages.success(request, "Unidade de medida inativada com sucesso.")

    return redirect("cadastros:unidades_medida")

def municipios(request):
    if request.method == "POST":
        form = MunicipioForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Município cadastrado com sucesso.")
            return redirect("cadastros:municipios")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = MunicipioForm()

    municipios_lista = Municipio.objects.all().order_by("nome")

    return render(
        request,
        "cadastros/municipios.html",
        {
            "form": form,
            "municipios": municipios_lista,
            "modo_edicao": False,
            "municipio_edicao": None,
        },
    )


def editar_municipio(request, municipio_id):
    municipio = get_object_or_404(Municipio, id=municipio_id)

    if request.method == "POST":
        form = MunicipioForm(request.POST, instance=municipio)

        if form.is_valid():
            form.save()
            messages.success(request, "Município atualizado com sucesso.")
            return redirect("cadastros:municipios")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = MunicipioForm(instance=municipio)

    municipios_lista = Municipio.objects.all().order_by("nome")

    return render(
        request,
        "cadastros/municipios.html",
        {
            "form": form,
            "municipios": municipios_lista,
            "modo_edicao": True,
            "municipio_edicao": municipio,
        },
    )


def alternar_status_municipio(request, municipio_id):
    municipio = get_object_or_404(Municipio, id=municipio_id)

    municipio.ativo = not municipio.ativo
    municipio.save(update_fields=["ativo", "atualizado_em"])

    if municipio.ativo:
        messages.success(request, "Município ativado com sucesso.")
    else:
        messages.success(request, "Município inativado com sucesso.")

    return redirect("cadastros:municipios")


def _somente_digitos_cnpj(valor):
    return re.sub(r"\D", "", str(valor or ""))


def _formatar_cnpj(valor):
    cnpj = _somente_digitos_cnpj(valor)
    if len(cnpj) != 14:
        return valor or ""

    return (
        f"{cnpj[0:2]}.{cnpj[2:5]}.{cnpj[5:8]}/"
        f"{cnpj[8:12]}-{cnpj[12:14]}"
    )


def _formatar_cep(valor):
    cep = re.sub(r"\D", "", str(valor or ""))
    if len(cep) == 8:
        return f"{cep[:5]}-{cep[5:]}"
    return str(valor or "").strip()


def _formatar_telefone_cnpj(valor):
    telefone = re.sub(r"\D", "", str(valor or ""))

    if len(telefone) == 11:
        return f"({telefone[:2]}) {telefone[2:7]}-{telefone[7:]}"

    if len(telefone) == 10:
        return f"({telefone[:2]}) {telefone[2:6]}-{telefone[6:]}"

    return str(valor or "").strip()


def _texto_endereco_cnpj(dados):
    tipo_logradouro = str(dados.get("descricao_tipo_de_logradouro") or "").strip()
    logradouro = str(dados.get("logradouro") or "").strip()

    if tipo_logradouro and logradouro:
        if not logradouro.casefold().startswith(tipo_logradouro.casefold()):
            logradouro = f"{tipo_logradouro} {logradouro}"
    elif tipo_logradouro and not logradouro:
        logradouro = tipo_logradouro

    numero = str(dados.get("numero") or "").strip()
    complemento = str(dados.get("complemento") or "").strip()
    bairro = str(dados.get("bairro") or "").strip()
    municipio = str(dados.get("municipio") or "").strip()
    uf = str(dados.get("uf") or "").strip().upper()
    cep = _formatar_cep(dados.get("cep"))

    partes = []

    if logradouro:
        partes.append(logradouro)

    if numero:
        partes.append(numero)

    if complemento:
        partes.append(complemento)

    if bairro:
        partes.append(bairro)

    if municipio and uf:
        partes.append(f"{municipio} - {uf}")
    elif municipio:
        partes.append(municipio)
    elif uf:
        partes.append(uf)

    if cep:
        partes.append(cep)

    return ", ".join(partes)[:255]


def _formatar_data_iso_br(valor):
    valor = str(valor or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        ano, mes, dia = valor.split("-")
        return f"{dia}/{mes}/{ano}"
    return valor


@login_required
@require_GET
def consultar_cnpj_fornecedor(request):
    """
    Consulta cadastral de CNPJ via BrasilAPI.

    A rota devolve somente os dados necessários à tela de fornecedores.
    Se a API externa estiver indisponível, o cadastro manual continua funcionando.
    """
    cnpj = _somente_digitos_cnpj(request.GET.get("cnpj"))

    if len(cnpj) != 14:
        return JsonResponse(
            {
                "ok": False,
                "mensagem": "Informe um CNPJ com 14 dígitos.",
            },
            status=400,
        )

    try:
        validar_cnpj(cnpj)
    except ValidationError:
        return JsonResponse(
            {
                "ok": False,
                "mensagem": "CNPJ inválido. Confira os números informados.",
            },
            status=400,
        )

    url = f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
    requisicao = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SIGA-Escolar/1.0",
        },
    )

    try:
        with urlopen(requisicao, timeout=8) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except HTTPError as erro:
        if erro.code == 404:
            mensagem = "CNPJ não encontrado na base de consulta."
            status = 404
        elif erro.code == 429:
            mensagem = (
                "O serviço de consulta recebeu muitas solicitações. "
                "Aguarde alguns instantes e tente novamente."
            )
            status = 429
        else:
            mensagem = "Não foi possível consultar o CNPJ neste momento."
            status = 502

        return JsonResponse({"ok": False, "mensagem": mensagem}, status=status)
    except (URLError, TimeoutError, json.JSONDecodeError):
        return JsonResponse(
            {
                "ok": False,
                "mensagem": (
                    "Serviço de consulta de CNPJ temporariamente indisponível. "
                    "Você pode preencher os dados manualmente."
                ),
            },
            status=503,
        )

    telefone = (
        dados.get("ddd_telefone_1")
        or dados.get("ddd_telefone_2")
        or ""
    )

    porte = str(
        dados.get("porte")
        or dados.get("descricao_porte")
        or ""
    ).strip()

    porte_normalizado = unicodedata.normalize("NFKD", porte.upper())
    porte_normalizado = "".join(
        ch for ch in porte_normalizado
        if not unicodedata.combining(ch)
    )

    eh_me_epp = porte_normalizado in {
        "MICRO EMPRESA",
        "EMPRESA DE PEQUENO PORTE",
    }

    situacao = str(
        dados.get("descricao_situacao_cadastral") or ""
    ).strip().upper()

    return JsonResponse(
        {
            "ok": True,
            "dados": {
                "cnpj": _formatar_cnpj(dados.get("cnpj") or cnpj),
                "razao_social": str(dados.get("razao_social") or "").strip(),
                "nome_fantasia": str(dados.get("nome_fantasia") or "").strip(),
                "endereco": _texto_endereco_cnpj(dados),
                "telefone": _formatar_telefone_cnpj(telefone),
                "email": str(dados.get("email") or "").strip().lower(),
                "situacao_cadastral": situacao,
                "porte": porte,
                "fornecedor_me_epp": eh_me_epp,
                "natureza_juridica": str(
                    dados.get("natureza_juridica") or ""
                ).strip(),
                "cnae_principal": str(
                    dados.get("cnae_fiscal_descricao") or ""
                ).strip(),
                "data_abertura": _formatar_data_iso_br(
                    dados.get("data_inicio_atividade")
                ),
                "matriz_filial": str(
                    dados.get("descricao_identificador_matriz_filial") or ""
                ).strip(),
            },
        }
    )


def fornecedores(request):
    if request.method == "POST":
        form = FornecedorForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Fornecedor cadastrado com sucesso.")
            return redirect("cadastros:fornecedores")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = FornecedorForm()

    fornecedores_queryset = Fornecedor.objects.all().order_by("razao_social")
    fornecedores_pagina = paginar_queryset(request, fornecedores_queryset)

    return render(
        request,
        "cadastros/fornecedores.html",
        {
            "form": form,
            "fornecedores": fornecedores_pagina,
            "modo_edicao": False,
            "fornecedor_edicao": None,
        },
    )


def editar_fornecedor(request, fornecedor_id):
    fornecedor = get_object_or_404(Fornecedor, id=fornecedor_id)

    if request.method == "POST":
        form = FornecedorForm(request.POST, instance=fornecedor)

        if form.is_valid():
            form.save()
            messages.success(request, "Fornecedor atualizado com sucesso.")
            return redirect("cadastros:fornecedores")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = FornecedorForm(instance=fornecedor)

    fornecedores_queryset = Fornecedor.objects.all().order_by("razao_social")
    fornecedores_pagina = paginar_queryset(request, fornecedores_queryset)

    return render(
        request,
        "cadastros/fornecedores.html",
        {
            "form": form,
            "fornecedores": fornecedores_pagina,
            "modo_edicao": True,
            "fornecedor_edicao": fornecedor,
        },
    )


def alternar_status_fornecedor(request, fornecedor_id):
    fornecedor = get_object_or_404(Fornecedor, id=fornecedor_id)

    fornecedor.ativo = not fornecedor.ativo
    fornecedor.save(update_fields=["ativo", "atualizado_em"])

    if fornecedor.ativo:
        messages.success(request, "Fornecedor ativado com sucesso.")
    else:
        messages.success(request, "Fornecedor inativado com sucesso.")

    return redirect("cadastros:fornecedores")


def escolas(request):
    if request.method == "POST":
        form = EscolaForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Escola cadastrada com sucesso.")
            return redirect("cadastros:escolas")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = EscolaForm()

    escolas_queryset = Escola.objects.all().select_related("municipio").order_by("nome_escola")
    escolas_pagina = paginar_queryset(request, escolas_queryset)

    return render(
        request,
        "cadastros/escolas.html",
        {
            "form": form,
            "escolas": escolas_pagina,
            "modo_edicao": False,
            "escola_edicao": None,
        },
    )


def editar_escola(request, escola_id):
    escola = get_object_or_404(Escola, id=escola_id)

    if request.method == "POST":
        form = EscolaForm(request.POST, instance=escola)

        if form.is_valid():
            form.save()
            messages.success(request, "Escola atualizada com sucesso.")
            return redirect("cadastros:escolas")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = EscolaForm(instance=escola)

    escolas_queryset = Escola.objects.all().select_related("municipio").order_by("nome_escola")
    escolas_pagina = paginar_queryset(request, escolas_queryset)

    return render(
        request,
        "cadastros/escolas.html",
        {
            "form": form,
            "escolas": escolas_pagina,
            "modo_edicao": True,
            "escola_edicao": escola,
        },
    )


def alternar_status_escola(request, escola_id):
    escola = get_object_or_404(Escola, id=escola_id)

    escola.ativo = not escola.ativo
    escola.save(update_fields=["ativo", "atualizado_em"])

    if escola.ativo:
        messages.success(request, "Escola ativada com sucesso.")
    else:
        messages.success(request, "Escola inativada com sucesso.")

    return redirect("cadastros:escolas")


def itens(request):
    garantir_unidades_medida_padrao()

    if request.method == "POST":
        form = ItemForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Item cadastrado com sucesso.")
            return redirect("cadastros:itens")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = ItemForm()

    itens_queryset = Item.objects.all().order_by("nome_item")
    itens_pagina = paginar_queryset(request, itens_queryset)
    unidades_medida_lista = UnidadeMedida.objects.filter(ativo=True).order_by("nome")

    return render(
        request,
        "cadastros/itens.html",
        {
            "form": form,
            "itens": itens_pagina,
            "unidades_medida": unidades_medida_lista,
            "modo_edicao": False,
            "item_edicao": None,
        },
    )


def editar_item(request, item_id):
    garantir_unidades_medida_padrao()

    item = get_object_or_404(Item, id=item_id)

    if request.method == "POST":
        form = ItemForm(request.POST, instance=item)

        if form.is_valid():
            form.save()
            messages.success(request, "Item atualizado com sucesso.")
            return redirect("cadastros:itens")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = ItemForm(instance=item)

    itens_lista = Item.objects.all().order_by("nome_item")
    unidades_medida_lista = UnidadeMedida.objects.filter(ativo=True).order_by("nome")

    unidade_atual = None
    if item.unidade_medida:
        unidade_atual = UnidadeMedida.objects.filter(codigo=item.unidade_medida).first()

    if unidade_atual and not unidade_atual.ativo:
        unidades_medida_lista = list(unidades_medida_lista) + [unidade_atual]

    return render(
        request,
        "cadastros/itens.html",
        {
            "form": form,
            "itens": itens_lista,
            "unidades_medida": unidades_medida_lista,
            "modo_edicao": True,
            "item_edicao": item,
        },
    )


def alternar_status_item(request, item_id):
    item = get_object_or_404(Item, id=item_id)

    item.ativo = not item.ativo
    item.save(update_fields=["ativo", "atualizado_em"])

    if item.ativo:
        messages.success(request, "Item ativado com sucesso.")
    else:
        messages.success(request, "Item inativado com sucesso.")

    return redirect("cadastros:itens")