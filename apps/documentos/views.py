

from decimal import Decimal, InvalidOperation
from datetime import datetime
from collections import defaultdict

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db.models import Sum, Max, Count
from django.db import models
from django.db import transaction
from django.utils import timezone

from apps.cadastros.models import Escola, Fornecedor, Item
from apps.pregoes.models import (
    Lance,
    Pregao,
    PregaoItem,
    ResultadoItem,
    PropostaInicialItem,
    QuantitativoEscola,
    QuantitativoPregao,
)

from .models import (
    ContratoGerado,
    ContratoItemGerado,
    DistratoContrato,
    DistratoContratoItem,
    RealinhamentoPreco,
    RealinhamentoPrecoItem,
    TrocaMarca,
    TrocaMarcaItem,
    ProjetoVenda,
    ProjetoVendaItem,
    ResultadoChamadaPublicaItem,
)

from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.shared import Pt, Cm, Inches
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from num2words import num2words
import re
import os
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED


# ============================================================
# CONTROLE DE VISUALIZAÇÃO POR ESCOLA - PERFIL CONSULTA/ESCOLA
# ============================================================

def usuario_eh_consulta_escola(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False

    if request.user.is_superuser:
        return False

    perfil = getattr(request.user, "perfil_acesso", None)

    return bool(perfil and perfil.perfil == "consulta_escola")


def escola_vinculada_usuario(request):
    perfil = getattr(request.user, "perfil_acesso", None)

    if not perfil:
        return None

    return perfil.escola


def aplicar_restricao_escola_contratos(request, queryset):
    """
    Para usuários Consulta/Escola, limita contratos somente à escola
    vinculada no cadastro do usuário.
    """
    if not usuario_eh_consulta_escola(request):
        return queryset

    escola = escola_vinculada_usuario(request)

    if not escola:
        return queryset.none()

    return queryset.filter(escola=escola)


def aplicar_restricao_escola_pregoes(request, queryset):
    """
    Para usuários Consulta/Escola, mostra apenas pregões que possuem
    quantitativo ou contrato da escola vinculada ao usuário.
    """
    if not usuario_eh_consulta_escola(request):
        return queryset

    escola = escola_vinculada_usuario(request)

    if not escola:
        return queryset.none()

    return queryset.filter(
        models.Q(contratos_gerados__escola=escola)
        | models.Q(quantitativos_escola__escola=escola)
    ).distinct()


def usuario_pode_acessar_contrato(request, contrato):
    if not usuario_eh_consulta_escola(request):
        return True

    escola = escola_vinculada_usuario(request)

    if not escola:
        messages.error(
            request,
            "Seu usuário Consulta/Escola não possui escola vinculada. Solicite o vínculo ao administrador.",
        )
        return False

    if contrato.escola_id != escola.id:
        messages.error(
            request,
            "Você não tem permissão para visualizar documentos de outra escola.",
        )
        return False

    return True


def usuario_pode_acessar_pregao_documentos(request, pregao):
    if not usuario_eh_consulta_escola(request):
        return True

    escola = escola_vinculada_usuario(request)

    if not escola:
        messages.error(
            request,
            "Seu usuário Consulta/Escola não possui escola vinculada. Solicite o vínculo ao administrador.",
        )
        return False

    possui_contrato = ContratoGerado.objects.filter(
        pregao=pregao,
        escola=escola,
    ).exists()

    possui_quantitativo = QuantitativoEscola.objects.filter(
        pregao=pregao,
        escola=escola,
    ).exists()

    if not possui_contrato and not possui_quantitativo:
        messages.error(
            request,
            "Você não tem permissão para visualizar documentos deste pregão.",
        )
        return False

    return True


def pregoes_finalizados(request):
    pregoes = Pregao.objects.filter(
        status=Pregao.STATUS_FINALIZADO
    ).order_by("-ano", "-numero")

    pregoes = aplicar_restricao_escola_pregoes(request, pregoes)

    return render(
        request,
        "documentos/pregoes_finalizados.html",
        {
            "pregoes": pregoes,
        },
    )

def selecionar_lances_para_planilha(lances):
    """
    A planilha oficial possui espaço para apenas 10 lances.

    Regra:
    - Até 10 lances: mostra todos.
    - Mais de 10 lances: mostra os 7 primeiros e os 3 últimos.
    """

    lances = list(lances)

    if len(lances) <= 10:
        return lances

    return lances[:7] + lances[-3:]

def planilha_lances(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if not usuario_pode_acessar_pregao_documentos(request, pregao):
        return redirect("documentos:pregoes_finalizados")

    itens = (
        PregaoItem.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("ordem")
    )

    relatorio = []

    for item_pregao in itens:
        linhas_fornecedores = []

        # Itens desertos aparecem mesmo sem propostas iniciais.
        if item_pregao.status == PregaoItem.STATUS_DESERTO:
            linhas_fornecedores.append(
                {
                    "fornecedor": None,
                    "fornecedor_nome": "DESERTO",
                    "marca": "",
                    "preco_inicial": "",
                    "lances": [""] * 10,
                    "tem_lance": False,
                    "status_especial": "DESERTO",
                }
            )

            relatorio.append(
                {
                    "item_pregao": item_pregao,
                    "linhas_fornecedores": linhas_fornecedores,
                }
            )

            continue

        # Itens fracassados aparecem mesmo sem propostas iniciais.
        if item_pregao.status == PregaoItem.STATUS_FRACASSADO:
            linhas_fornecedores.append(
                {
                    "fornecedor": None,
                    "fornecedor_nome": "FRACASSADO",
                    "marca": "",
                    "preco_inicial": "",
                    "lances": [""] * 10,
                    "tem_lance": False,
                    "status_especial": "FRACASSADO",
                }
            )

            relatorio.append(
                {
                    "item_pregao": item_pregao,
                    "linhas_fornecedores": linhas_fornecedores,
                }
            )

            continue

        propostas_do_item = (
            PropostaInicialItem.objects.filter(
                pregao=pregao,
                pregao_item=item_pregao,
            )
            .select_related("fornecedor")
            .order_by("fornecedor__razao_social")
        )

        for proposta in propostas_do_item:
            fornecedor = proposta.fornecedor

            # Se não tiver marca nem preço inicial, não aparece na planilha.
            if not proposta.marca and proposta.preco_inicial is None:
                continue

            lances_fornecedor = list(
                Lance.objects.filter(
                    pregao=pregao,
                    pregao_item=item_pregao,
                    fornecedor=fornecedor,
                )
                .order_by("ordem_lance")
                .values_list("valor_lance", flat=True)
            )

            lances_10 = selecionar_lances_para_planilha(lances_fornecedor)

            while len(lances_10) < 10:
                lances_10.append("")

            tem_lance = any(lances_fornecedor)

            linhas_fornecedores.append(
                {
                    "fornecedor": fornecedor,
                    "fornecedor_nome": fornecedor.razao_social,
                    "marca": proposta.marca,
                    "preco_inicial": proposta.preco_inicial,
                    "lances": lances_10,
                    "tem_lance": tem_lance,
                    "status_especial": "",
                }
            )

        if linhas_fornecedores:
            relatorio.append(
                {
                    "item_pregao": item_pregao,
                    "linhas_fornecedores": linhas_fornecedores,
                }
            )

    return render(
        request,
        "documentos/planilha_lances.html",
        {
            "pregao": pregao,
            "relatorio": relatorio,
        },
    )

def fornecedores_vencedores(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if not usuario_pode_acessar_pregao_documentos(request, pregao):
        return redirect("documentos:pregoes_finalizados")

    itens = (
        PregaoItem.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("ordem")
    )

    resultados = {
        resultado.pregao_item_id: resultado
        for resultado in ResultadoItem.objects.filter(
            pregao=pregao
        ).select_related(
            "primeiro_fornecedor",
            "segundo_fornecedor",
            "terceiro_fornecedor",
        )
    }

    propostas = {
        (proposta.pregao_item_id, proposta.fornecedor_id): proposta
        for proposta in PropostaInicialItem.objects.filter(
            pregao=pregao
        ).select_related("fornecedor", "pregao_item")
    }

    relatorio = []

    for item_pregao in itens:
        resultado = resultados.get(item_pregao.id)

        primeiro = {
            "fornecedor": "",
            "marca": "",
            "preco": "",
        }

        segundo = {
            "fornecedor": "",
            "marca": "",
            "preco": "",
        }

        terceiro = {
            "fornecedor": "",
            "marca": "",
            "preco": "",
        }

        if item_pregao.status == PregaoItem.STATUS_DESERTO:
            primeiro["fornecedor"] = "DESERTO -"
            segundo["fornecedor"] = "DESERTO -"
            terceiro["fornecedor"] = "DESERTO -"

        elif item_pregao.status == PregaoItem.STATUS_FRACASSADO:
            primeiro["fornecedor"] = "FRACASSADO -"
            segundo["fornecedor"] = "FRACASSADO -"
            terceiro["fornecedor"] = "FRACASSADO -"

        elif resultado:
            if resultado.primeiro_fornecedor:
                proposta = propostas.get(
                    (item_pregao.id, resultado.primeiro_fornecedor_id)
                )

                primeiro = {
                    "fornecedor": f"{resultado.primeiro_fornecedor.razao_social} - {resultado.primeiro_fornecedor.cnpj}",
                    "marca": proposta.marca if proposta else "",
                    "preco": resultado.primeiro_valor,
                }

            if resultado.segundo_fornecedor:
                proposta = propostas.get(
                    (item_pregao.id, resultado.segundo_fornecedor_id)
                )

                segundo = {
                    "fornecedor": f"{resultado.segundo_fornecedor.razao_social} - {resultado.segundo_fornecedor.cnpj}",
                    "marca": proposta.marca if proposta else "",
                    "preco": resultado.segundo_valor,
                }
            

            if resultado.terceiro_fornecedor:
                proposta = propostas.get(
                    (item_pregao.id, resultado.terceiro_fornecedor_id)
                )

                terceiro = {
                    "fornecedor": f"{resultado.terceiro_fornecedor.razao_social} - {resultado.terceiro_fornecedor.cnpj}",
                    "marca": proposta.marca if proposta else "",
                    "preco": resultado.terceiro_valor,
                }
            

        relatorio.append(
            {
                "ordem": item_pregao.ordem,
                "item": item_pregao.item,
                "unidade": item_pregao.item.get_unidade_medida_display(),
                "quantidade": item_pregao.quantidade_total,
                "primeiro": primeiro,
                "segundo": segundo,
                "terceiro": terceiro,
            }
        )

    municipios = pregao.municipios.all().order_by("nome")

    return render(
        request,
        "documentos/fornecedores_vencedores.html",
        {
            "pregao": pregao,
            "municipios": municipios,
            "relatorio": relatorio,
        },
    )



def montar_contratos_possiveis_lote(pregao, escola_id=None, fornecedor_id=None, escolher_apenas_primeiro_substituto=True):
    """
    Monta uma lista plana de contratos possíveis para geração em lote.

    Regras:
    - usa a mesma lógica de saldo da Base para Geração de Contratos;
    - considera distratos parciais/totais;
    - por padrão, quando houver 2º e 3º colocados disponíveis por distrato,
      seleciona apenas o primeiro substituto disponível para evitar gerar contratos duplicados.
    """
    if isinstance(escola_id, str) and not escola_id.strip():
        escola_id = None

    if isinstance(fornecedor_id, str) and not fornecedor_id.strip():
        fornecedor_id = None

    escola_id = int(escola_id) if escola_id else None
    fornecedor_id = int(fornecedor_id) if fornecedor_id else None

    itens_pregao = {
        pregao_item.item_id: pregao_item
        for pregao_item in PregaoItem.objects.filter(
            pregao=pregao
        ).select_related("item")
    }

    resultados = {
        resultado.pregao_item_id: resultado
        for resultado in ResultadoItem.objects.filter(
            pregao=pregao,
            primeiro_fornecedor__isnull=False,
        ).select_related(
            "pregao_item",
            "pregao_item__item",
            "primeiro_fornecedor",
            "segundo_fornecedor",
            "terceiro_fornecedor",
        )
    }

    propostas = {
        (proposta.pregao_item_id, proposta.fornecedor_id): proposta
        for proposta in PropostaInicialItem.objects.filter(
            pregao=pregao
        ).select_related("fornecedor", "pregao_item")
    }

    quantitativos_query = QuantitativoEscola.objects.filter(
        pregao=pregao,
        quantidade__gt=0,
    )

    if escola_id:
        quantitativos_query = quantitativos_query.filter(escola_id=escola_id)

    quantitativos_escolas = (
        quantitativos_query
        .select_related(
            "escola",
            "escola__municipio",
            "item",
        )
        .order_by(
            "escola__nome_escola",
            "item__nome_item",
        )
    )

    contratos_dict = {}

    for quantitativo in quantitativos_escolas:
        escola = quantitativo.escola
        item = quantitativo.item
        quantidade_escola = quantitativo.quantidade or Decimal("0")

        item_pregao = itens_pregao.get(item.id)

        if not item_pregao:
            continue

        resultado = resultados.get(item_pregao.id)

        if not resultado or not resultado.primeiro_fornecedor:
            continue

        quantidade_ja_contratada = obter_quantidade_ja_contratada(quantitativo)
        saldo_disponivel = quantidade_escola - quantidade_ja_contratada

        if saldo_disponivel <= 0:
            continue

        quantidade_distratada_primeiro = obter_quantidade_distratada_primeiro_colocado(
            quantitativo,
            resultado.primeiro_fornecedor,
        )
        quantidade_substituta_ja_contratada = obter_quantidade_ja_contratada_substituta(
            quantitativo,
            resultado.primeiro_fornecedor,
        )
        saldo_substituicao_disponivel = quantidade_distratada_primeiro - quantidade_substituta_ja_contratada

        if saldo_substituicao_disponivel < 0:
            saldo_substituicao_disponivel = Decimal("0")

        opcoes_fornecedores = obter_opcoes_fornecedores_para_contrato(
            resultado,
            quantitativo,
        )

        if fornecedor_id:
            opcoes_fornecedores = [
                opcao for opcao in opcoes_fornecedores
                if opcao["fornecedor"].id == fornecedor_id
            ]
        elif escolher_apenas_primeiro_substituto and len(opcoes_fornecedores) > 1:
            # Em contratação por distrato, a lista pode trazer 2º e 3º colocados.
            # Na geração em lote usamos apenas o primeiro substituto disponível,
            # normalmente o 2º colocado, evitando contratar o mesmo saldo duas vezes.
            opcoes_fornecedores = opcoes_fornecedores[:1]

        if not opcoes_fornecedores:
            continue

        for opcao in opcoes_fornecedores:
            fornecedor = opcao["fornecedor"]
            valor_unitario = opcao["valor_unitario"]

            if opcao["contratacao_substituta"]:
                saldo_opcao = min(saldo_disponivel, saldo_substituicao_disponivel)
            else:
                saldo_opcao = saldo_disponivel

            if saldo_opcao <= 0:
                continue

            valor_total_disponivel_item = saldo_opcao * valor_unitario
            proposta = propostas.get((item_pregao.id, fornecedor.id))
            marca = proposta.marca if proposta else ""

            chave = (escola.id, fornecedor.id)

            if chave not in contratos_dict:
                contratos_dict[chave] = {
                    "escola": escola,
                    "fornecedor": fornecedor,
                    "itens": [],
                    "valor_total_contrato": Decimal("0"),
                    "tem_substituicao": False,
                }

            contrato_grupo = contratos_dict[chave]

            if opcao["contratacao_substituta"]:
                contrato_grupo["tem_substituicao"] = True

            contrato_grupo["itens"].append(
                {
                    "quantitativo": quantitativo,
                    "quantitativo_escola_id": quantitativo.id,
                    "ordem": item_pregao.ordem,
                    "item": item,
                    "marca": marca,
                    "unidade": item.get_unidade_medida_display(),
                    "quantidade_contratar": saldo_opcao,
                    "valor_unitario": valor_unitario,
                    "valor_total_item": valor_total_disponivel_item,
                    "posicao_fornecedor": opcao["posicao_rotulo"],
                    "contratacao_substituta": opcao["contratacao_substituta"],
                }
            )

            contrato_grupo["valor_total_contrato"] += valor_total_disponivel_item

    contratos_lote = list(contratos_dict.values())
    contratos_lote.sort(
        key=lambda grupo: (
            grupo["escola"].nome_escola,
            grupo["fornecedor"].razao_social,
        )
    )

    return contratos_lote


def registrar_contrato_lote_item(pregao, escola, fornecedor, itens_para_salvar):
    valor_total_contrato = sum(
        (item["valor_total_item"] for item in itens_para_salvar),
        Decimal("0"),
    )

    numero_sequencial, ano_contrato, numero_contrato = gerar_numero_contrato_por_escola_ano(
        escola,
        pregao.ano,
    )

    contrato = ContratoGerado.objects.create(
        pregao=pregao,
        escola=escola,
        fornecedor=fornecedor,
        numero_contrato=numero_contrato,
        numero_sequencial=numero_sequencial,
        ano_contrato=ano_contrato,
        valor_total=valor_total_contrato,
        status=ContratoGerado.STATUS_GERADO,
    )

    for item in itens_para_salvar:
        ContratoItemGerado.objects.create(
            contrato=contrato,
            quantitativo_escola=item["quantitativo"],
            item=item["item"],
            marca=item["marca"],
            unidade=item["unidade"],
            quantidade_contratada=item["quantidade_contratar"],
            valor_unitario=item["valor_unitario"],
            valor_total=item["valor_total_item"],
        )

    return contrato


def montar_contratos_lote_por_selecao(pregao, post_data, escola_id=None, fornecedor_id=None):
    """
    Monta contratos em lote usando exatamente os itens marcados e as quantidades
    informadas na tela Base para Geração de Contratos.

    Cada item selecionado vem no formato:
    escola_id|fornecedor_id|quantitativo_escola_id
    """
    selecionados = post_data.getlist("lote_item")

    if not selecionados:
        raise ValueError("Selecione pelo menos um item na Base para Geração de Contratos.")

    contratos_possiveis = montar_contratos_possiveis_lote(
        pregao,
        escola_id=escola_id,
        fornecedor_id=fornecedor_id,
        escolher_apenas_primeiro_substituto=False,
    )

    itens_possiveis = {}

    for grupo in contratos_possiveis:
        escola = grupo["escola"]
        fornecedor = grupo["fornecedor"]

        for item in grupo["itens"]:
            chave = (
                str(escola.id),
                str(fornecedor.id),
                str(item["quantitativo_escola_id"]),
            )

            itens_possiveis[chave] = {
                "grupo": grupo,
                "item": item,
            }

    contratos_dict = {}
    erros = []
    quantidade_por_quantitativo = defaultdict(Decimal)
    limite_por_quantitativo = {}

    for valor_selecionado in selecionados:
        partes = str(valor_selecionado or "").split("|")

        if len(partes) != 3:
            erros.append("Um dos itens selecionados possui identificação inválida.")
            continue

        escola_id_item, fornecedor_id_item, quantitativo_id = partes
        chave = (escola_id_item, fornecedor_id_item, quantitativo_id)
        dados = itens_possiveis.get(chave)

        if not dados:
            erros.append("Um dos itens selecionados não possui mais saldo disponível.")
            continue

        grupo_original = dados["grupo"]
        item_original = dados["item"]

        campo_quantidade = f"lote_quantidade_{escola_id_item}_{fornecedor_id_item}_{quantitativo_id}"
        quantidade_texto = (post_data.get(campo_quantidade) or "").strip().replace(",", ".")

        if not quantidade_texto:
            erros.append(f"Informe a quantidade do item {item_original['item'].nome_item}.")
            continue

        try:
            quantidade = Decimal(quantidade_texto)
        except InvalidOperation:
            erros.append(f"A quantidade informada para o item {item_original['item'].nome_item} é inválida.")
            continue

        if quantidade <= 0:
            erros.append(f"A quantidade do item {item_original['item'].nome_item} deve ser maior que zero.")
            continue

        saldo_disponivel = item_original["quantidade_contratar"]

        if quantidade > saldo_disponivel:
            erros.append(
                f"A quantidade do item {item_original['item'].nome_item} ultrapassa o saldo disponível. "
                f"Saldo disponível: {saldo_disponivel}."
            )
            continue

        # Evita que o mesmo saldo seja selecionado para mais de um fornecedor
        # quando houver 2º e 3º colocados disponíveis por distrato.
        quantidade_por_quantitativo[quantitativo_id] += quantidade
        limite_por_quantitativo[quantitativo_id] = max(
            limite_por_quantitativo.get(quantitativo_id, Decimal("0")),
            saldo_disponivel,
        )

        if quantidade_por_quantitativo[quantitativo_id] > limite_por_quantitativo[quantitativo_id]:
            erros.append(
                f"A quantidade total selecionada para o item {item_original['item'].nome_item} "
                "ultrapassa o saldo disponível para contratação."
            )
            continue

        item_ajustado = dict(item_original)
        item_ajustado["quantidade_contratar"] = quantidade
        item_ajustado["valor_total_item"] = quantidade * item_original["valor_unitario"]

        chave_contrato = (grupo_original["escola"].id, grupo_original["fornecedor"].id)

        if chave_contrato not in contratos_dict:
            contratos_dict[chave_contrato] = {
                "escola": grupo_original["escola"],
                "fornecedor": grupo_original["fornecedor"],
                "itens": [],
                "valor_total_contrato": Decimal("0"),
                "tem_substituicao": grupo_original.get("tem_substituicao", False),
            }

        contratos_dict[chave_contrato]["itens"].append(item_ajustado)
        contratos_dict[chave_contrato]["valor_total_contrato"] += item_ajustado["valor_total_item"]

    if erros:
        raise ValueError("\n".join(erros))

    contratos_lote = list(contratos_dict.values())
    contratos_lote.sort(
        key=lambda grupo: (
            grupo["escola"].nome_escola,
            grupo["fornecedor"].razao_social,
        )
    )

    if not contratos_lote:
        raise ValueError("Nenhum item válido foi selecionado para geração em lote.")

    return contratos_lote


def contratos_lote(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if usuario_eh_consulta_escola(request):
        messages.error(request, "Seu perfil permite apenas consultar contratos e documentos da escola vinculada.")
        return redirect(f"{reverse('documentos:contratos_gerados')}?pregao={pregao.id}")

    escola_id = request.GET.get("escola") or request.POST.get("escola")
    fornecedor_id = request.GET.get("fornecedor") or request.POST.get("fornecedor")

    municipios_ids = pregao.municipios.values_list("id", flat=True)

    escolas = (
        Escola.objects.filter(
            ativo=True,
            municipio_id__in=municipios_ids,
        )
        .select_related("municipio")
        .order_by("nome_escola")
    )

    fornecedores = pregao.fornecedores.filter(ativo=True).order_by("razao_social")

    if request.method == "POST":
        contratos_gerados = []
        itens_selecionados_tela_base = request.POST.getlist("lote_item")

        try:
            with transaction.atomic():
                if itens_selecionados_tela_base:
                    contratos_possiveis = montar_contratos_lote_por_selecao(
                        pregao,
                        request.POST,
                        escola_id=escola_id,
                        fornecedor_id=fornecedor_id,
                    )

                    for grupo in contratos_possiveis:
                        contrato = registrar_contrato_lote_item(
                            pregao,
                            grupo["escola"],
                            grupo["fornecedor"],
                            grupo["itens"],
                        )
                        contratos_gerados.append(contrato)

                else:
                    # Modo antigo: gera todos os contratos possíveis da tela de lote.
                    # Recalcula contrato por contrato dentro da transação para respeitar
                    # saldos já consumidos durante a própria geração em lote.
                    while True:
                        contratos_possiveis = montar_contratos_possiveis_lote(
                            pregao,
                            escola_id=escola_id,
                            fornecedor_id=fornecedor_id,
                        )

                        if not contratos_possiveis:
                            break

                        grupo = contratos_possiveis[0]
                        contrato = registrar_contrato_lote_item(
                            pregao,
                            grupo["escola"],
                            grupo["fornecedor"],
                            grupo["itens"],
                        )
                        contratos_gerados.append(contrato)

                        # Evita laço infinito em caso de inconsistência inesperada.
                        if len(contratos_gerados) > 500:
                            raise ValueError(
                                "A geração em lote foi interrompida por segurança. Verifique os saldos dos contratos."
                            )
        except ValueError as erro:
            for linha in str(erro).splitlines():
                if linha.strip():
                    messages.error(request, linha.strip())
            return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

        if contratos_gerados:
            messages.success(
                request,
                f"{len(contratos_gerados)} contrato(s) gerado(s) em lote com sucesso.",
            )
            return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

        messages.warning(
            request,
            "Nenhum contrato com saldo disponível foi encontrado para geração em lote.",
        )
        return redirect("documentos:contratos_lote", pregao_id=pregao.id)

    contratos_possiveis = montar_contratos_possiveis_lote(
        pregao,
        escola_id=escola_id,
        fornecedor_id=fornecedor_id,
    )

    total_itens = sum(len(grupo["itens"]) for grupo in contratos_possiveis)
    valor_total = sum(
        (grupo["valor_total_contrato"] for grupo in contratos_possiveis),
        Decimal("0"),
    )

    return render(
        request,
        "documentos/contratos_lote.html",
        {
            "pregao": pregao,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "contratos_possiveis": contratos_possiveis,
            "total_contratos": len(contratos_possiveis),
            "total_itens": total_itens,
            "valor_total": valor_total,
            "filtros": {
                "escola": escola_id,
                "fornecedor": fornecedor_id,
            },
        },
    )

def limpar_nome_arquivo(texto):
    texto = str(texto or "").strip()
    texto = re.sub(r"[\\/:*?\"<>|]+", "-", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto[:120] or "arquivo"


def nome_arquivo_contrato(contrato, extensao):
    nome = (
        f"Contrato_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
    )
    return f"{limpar_nome_arquivo(nome)}.{extensao}"


def filtrar_contratos_para_download_lote(request):
    contratos = (
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        )
        .filter(
            status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ]
        )
        .order_by("escola__nome_escola", "numero_sequencial", "fornecedor__razao_social")
    )

    pregao_id = request.GET.get("pregao")
    escola_id = request.GET.get("escola")
    fornecedor_id = request.GET.get("fornecedor")
    status = request.GET.get("status")
    data_inicio = request.GET.get("data_inicio")
    data_fim = request.GET.get("data_fim")

    if pregao_id:
        contratos = contratos.filter(pregao_id=pregao_id)

    if escola_id:
        contratos = contratos.filter(escola_id=escola_id)

    if fornecedor_id:
        contratos = contratos.filter(fornecedor_id=fornecedor_id)

    if status:
        contratos = contratos.filter(status=status)

    if data_inicio:
        contratos = contratos.filter(criado_em__date__gte=data_inicio)

    if data_fim:
        contratos = contratos.filter(criado_em__date__lte=data_fim)

    return contratos


def baixar_contratos_lote(request, formato):
    formato = (formato or "").lower()

    if formato not in ["word", "pdf"]:
        messages.error(request, "Formato inválido para download em lote.")
        return redirect("documentos:contratos_gerados")

    contratos = list(filtrar_contratos_para_download_lote(request))

    if not contratos:
        messages.error(
            request,
            "Nenhum contrato ativo ou parcialmente distratado foi encontrado para download em lote.",
        )
        return redirect("documentos:contratos_gerados")

    if formato == "pdf":
        libreoffice = encontrar_libreoffice()

        if not libreoffice:
            messages.error(
                request,
                "LibreOffice não encontrado. Instale o LibreOffice ou verifique se o comando libreoffice/soffice está disponível no PATH.",
            )
            return redirect("documentos:contratos_gerados")

    zip_buffer = BytesIO()

    with ZipFile(zip_buffer, "w", ZIP_DEFLATED) as zip_file:
        with tempfile.TemporaryDirectory() as pasta_temp:
            pasta_temp_path = Path(pasta_temp)

            for contrato in contratos:
                try:
                    documento = montar_documento_contrato_word(contrato)
                except Exception as erro:
                    # Inclui um arquivo de erro no ZIP para o usuário saber qual contrato falhou,
                    # sem interromper todos os demais.
                    pasta_escola = limpar_nome_arquivo(contrato.escola.nome_escola)
                    nome_erro = limpar_nome_arquivo(
                        f"ERRO_Contrato_{contrato.numero_contrato}_{contrato.fornecedor.razao_social}.txt"
                    )
                    zip_file.writestr(
                        f"{pasta_escola}/{nome_erro}",
                        str(erro),
                    )
                    continue

                pasta_escola = limpar_nome_arquivo(contrato.escola.nome_escola)

                if formato == "word":
                    arquivo_saida = BytesIO()
                    documento.save(arquivo_saida)
                    arquivo_saida.seek(0)

                    zip_file.writestr(
                        f"{pasta_escola}/{nome_arquivo_contrato(contrato, 'docx')}",
                        arquivo_saida.getvalue(),
                    )
                else:
                    nome_base = limpar_nome_arquivo(
                        f"Contrato_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
                    )
                    caminho_docx = pasta_temp_path / f"{nome_base}.docx"
                    caminho_pdf = pasta_temp_path / f"{nome_base}.pdf"

                    documento.save(caminho_docx)

                    comando = [
                        libreoffice,
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(pasta_temp_path),
                        str(caminho_docx),
                    ]

                    resultado = subprocess.run(
                        comando,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=60,
                    )

                    if resultado.returncode != 0 or not caminho_pdf.exists():
                        pasta_escola = limpar_nome_arquivo(contrato.escola.nome_escola)
                        nome_erro = limpar_nome_arquivo(
                            f"ERRO_Contrato_{contrato.numero_contrato}_{contrato.fornecedor.razao_social}.txt"
                        )
                        zip_file.writestr(
                            f"{pasta_escola}/{nome_erro}",
                            resultado.stderr or resultado.stdout or "PDF não gerado pelo LibreOffice.",
                        )
                        continue

                    zip_file.write(
                        caminho_pdf,
                        f"{pasta_escola}/{nome_arquivo_contrato(contrato, 'pdf')}",
                    )

                    try:
                        caminho_docx.unlink(missing_ok=True)
                        caminho_pdf.unlink(missing_ok=True)
                    except TypeError:
                        if caminho_docx.exists():
                            caminho_docx.unlink()
                        if caminho_pdf.exists():
                            caminho_pdf.unlink()

    zip_buffer.seek(0)

    sufixo = "Word" if formato == "word" else "PDF"
    nome_zip = f"Contratos_em_Lote_{sufixo}.zip"

    response = HttpResponse(
        zip_buffer.getvalue(),
        content_type="application/zip",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_zip}"'

    return response


def formatar_moeda_br(valor):
    if valor is None:
        valor = Decimal("0")

    valor = Decimal(valor)

    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    return f"R$ {texto}"


def formatar_numero_br(valor):
    if valor is None:
        return ""

    texto = f"{Decimal(valor):,.3f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    if texto.endswith(",000"):
        texto = texto[:-4]

    return texto


def resultado_final(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if not usuario_pode_acessar_pregao_documentos(request, pregao):
        return redirect("documentos:pregoes_finalizados")

    itens = (
        PregaoItem.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("ordem")
    )

    resultados = {
        resultado.pregao_item_id: resultado
        for resultado in ResultadoItem.objects.filter(
            pregao=pregao
        ).select_related("primeiro_fornecedor")
    }

    propostas = {
        (proposta.pregao_item_id, proposta.fornecedor_id): proposta
        for proposta in PropostaInicialItem.objects.filter(
            pregao=pregao
        ).select_related("fornecedor", "pregao_item")
    }

    relatorio = []
    valor_total_pregao = Decimal("0")

    for item_pregao in itens:
        resultado = resultados.get(item_pregao.id)

        marca = ""
        fornecedor = ""
        valor_unitario = Decimal("0")
        valor_total = Decimal("0")

        if item_pregao.status == PregaoItem.STATUS_DESERTO:
            fornecedor = "DESERTO -"

        elif item_pregao.status == PregaoItem.STATUS_FRACASSADO:
            fornecedor = "FRACASSADO -"

        elif resultado and resultado.primeiro_fornecedor:
            proposta = propostas.get(
                (item_pregao.id, resultado.primeiro_fornecedor_id)
            )

            marca = proposta.marca if proposta else ""

            fornecedor = (
                f"{resultado.primeiro_fornecedor.razao_social} - "
                f"{resultado.primeiro_fornecedor.cnpj}"
            )

            valor_unitario = resultado.primeiro_valor or Decimal("0")
            valor_total = item_pregao.quantidade_total * valor_unitario

        valor_total_pregao += valor_total

        relatorio.append(
            {
                "ordem": item_pregao.ordem,
                "genero": item_pregao.item.nome_item,
                "unidade": item_pregao.item.get_unidade_medida_display(),
                "quantidade": formatar_numero_br(item_pregao.quantidade_total),
                "marca": marca,
                "fornecedor": fornecedor,
                "valor_unitario": formatar_moeda_br(valor_unitario),
                "valor_total": formatar_moeda_br(valor_total),
                "status": item_pregao.status,
            }
        )

    municipios = pregao.municipios.all().order_by("nome")
    municipios_texto = ", ".join(
        [
            f"{municipio.nome}/{municipio.uf}" if getattr(municipio, "uf", "") else municipio.nome
            for municipio in municipios
        ]
    )

    valor_total_formatado = formatar_moeda_br(valor_total_pregao)

    return render(
        request,
        "documentos/resultado_final.html",
        {
            "pregao": pregao,
            "municipios": municipios,
            "municipios_texto": municipios_texto,
            "relatorio": relatorio,
            "valor_total_pregao": valor_total_formatado,
        },
    )

def obter_quantidade_distratada_contrato_item(contrato_item):
    total = (
        DistratoContratoItem.objects.filter(
            contrato_item=contrato_item,
        )
        .aggregate(total=Sum("quantidade_distratada"))
        .get("total")
    )

    if total is None:
        return Decimal("0")

    return total


def obter_saldo_ativo_contrato_item(contrato_item):
    quantidade_distratada = obter_quantidade_distratada_contrato_item(contrato_item)
    saldo = contrato_item.quantidade_contratada - quantidade_distratada

    if saldo < 0:
        return Decimal("0")

    return saldo


def contrato_possui_saldo_ativo(contrato):
    itens = ContratoItemGerado.objects.filter(contrato=contrato)

    for item in itens:
        if obter_saldo_ativo_contrato_item(item) > 0:
            return True

    return False


def contrato_totalmente_distratado(contrato):
    itens = ContratoItemGerado.objects.filter(contrato=contrato)

    if not itens.exists():
        return False

    for item in itens:
        if obter_saldo_ativo_contrato_item(item) > 0:
            return False

    return True


def obter_quantidade_ja_contratada(quantitativo_escola):
    """
    Quantidade contratada líquida:
    soma os contratos ativos e parcialmente distratados,
    descontando as quantidades já distratadas em cada item.
    """
    itens_contratados = (
        ContratoItemGerado.objects.filter(
            quantitativo_escola=quantitativo_escola,
            contrato__status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ],
        )
        .select_related("contrato", "item")
    )

    total = Decimal("0")

    for contrato_item in itens_contratados:
        total += obter_saldo_ativo_contrato_item(contrato_item)

    return total


def obter_quantidade_distratada_primeiro_colocado(quantitativo_escola, fornecedor_primeiro):
    if not fornecedor_primeiro:
        return Decimal("0")

    total = (
        DistratoContratoItem.objects.filter(
            contrato_item__quantitativo_escola=quantitativo_escola,
            contrato_item__contrato__fornecedor=fornecedor_primeiro,
        )
        .aggregate(total=Sum("quantidade_distratada"))
        .get("total")
    )

    if total is None:
        return Decimal("0")

    return total



def obter_quantidade_ja_contratada_substituta(quantitativo_escola, fornecedor_primeiro):
    """
    Soma quantidades ativas já contratadas com fornecedores substitutos
    (2º/3º colocados), para não permitir substituir quantidade maior
    do que a quantidade efetivamente distratada do 1º colocado.
    """
    itens_contratados = (
        ContratoItemGerado.objects.filter(
            quantitativo_escola=quantitativo_escola,
            contrato__status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ],
        )
        .exclude(contrato__fornecedor=fornecedor_primeiro)
        .select_related("contrato", "item")
    )

    total = Decimal("0")

    for contrato_item in itens_contratados:
        total += obter_saldo_ativo_contrato_item(contrato_item)

    return total


def existe_distrato_primeiro_colocado(quantitativo_escola, fornecedor_primeiro):
    """
    Verifica se já houve distrato total ou parcial do fornecedor 1º colocado
    para o item/escola. Quando existir distrato, a base libera 2º e 3º colocados
    para contratação substituta da quantidade devolvida.
    """
    if not fornecedor_primeiro:
        return False

    return DistratoContratoItem.objects.filter(
        contrato_item__quantitativo_escola=quantitativo_escola,
        contrato_item__contrato__fornecedor=fornecedor_primeiro,
    ).exists()


def obter_dados_fornecedor_resultado(resultado, fornecedor):
    """
    Retorna a posição e o valor adjudicado do fornecedor no resultado do item.
    Aceita 1º, 2º ou 3º colocado.
    """
    if not resultado or not fornecedor:
        return None

    if resultado.primeiro_fornecedor_id == fornecedor.id:
        return {
            "posicao_codigo": "1",
            "posicao_rotulo": "1º colocado",
            "valor_unitario": resultado.primeiro_valor or Decimal("0"),
        }

    if resultado.segundo_fornecedor_id == fornecedor.id:
        return {
            "posicao_codigo": "2",
            "posicao_rotulo": "2º colocado",
            "valor_unitario": resultado.segundo_valor or Decimal("0"),
        }

    if resultado.terceiro_fornecedor_id == fornecedor.id:
        return {
            "posicao_codigo": "3",
            "posicao_rotulo": "3º colocado",
            "valor_unitario": resultado.terceiro_valor or Decimal("0"),
        }

    return None


def obter_opcoes_fornecedores_para_contrato(resultado, quantitativo_escola):
    """
    Regra de contratação:
    - Sem distrato: libera somente o 1º colocado.
    - Com distrato do 1º colocado: libera 2º e 3º colocados, se existirem.
    """
    if not resultado or not resultado.primeiro_fornecedor:
        return []

    houve_distrato = existe_distrato_primeiro_colocado(
        quantitativo_escola,
        resultado.primeiro_fornecedor,
    )

    opcoes = []

    if houve_distrato:
        if resultado.segundo_fornecedor:
            opcoes.append(
                {
                    "fornecedor": resultado.segundo_fornecedor,
                    "posicao_codigo": "2",
                    "posicao_rotulo": "2º colocado por distrato",
                    "valor_unitario": resultado.segundo_valor or Decimal("0"),
                    "contratacao_substituta": True,
                }
            )

        if resultado.terceiro_fornecedor:
            opcoes.append(
                {
                    "fornecedor": resultado.terceiro_fornecedor,
                    "posicao_codigo": "3",
                    "posicao_rotulo": "3º colocado por distrato",
                    "valor_unitario": resultado.terceiro_valor or Decimal("0"),
                    "contratacao_substituta": True,
                }
            )
    else:
        opcoes.append(
            {
                "fornecedor": resultado.primeiro_fornecedor,
                "posicao_codigo": "1",
                "posicao_rotulo": "1º colocado",
                "valor_unitario": resultado.primeiro_valor or Decimal("0"),
                "contratacao_substituta": False,
            }
        )

    return opcoes

def contratos_pregao(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if usuario_eh_consulta_escola(request):
        if not usuario_pode_acessar_pregao_documentos(request, pregao):
            return redirect("documentos:pregoes_finalizados")

        return redirect(f"{reverse('documentos:contratos_gerados')}?pregao={pregao.id}")

    itens_pregao = {
        pregao_item.item_id: pregao_item
        for pregao_item in PregaoItem.objects.filter(
            pregao=pregao
        ).select_related("item")
    }

    resultados = {
        resultado.pregao_item_id: resultado
        for resultado in ResultadoItem.objects.filter(
            pregao=pregao,
            primeiro_fornecedor__isnull=False,
        ).select_related(
            "pregao_item",
            "pregao_item__item",
            "primeiro_fornecedor",
            "segundo_fornecedor",
            "terceiro_fornecedor",
        )
    }

    propostas = {
        (proposta.pregao_item_id, proposta.fornecedor_id): proposta
        for proposta in PropostaInicialItem.objects.filter(
            pregao=pregao
        ).select_related("fornecedor", "pregao_item")
    }

    quantitativos_escolas = (
        QuantitativoEscola.objects.filter(
            pregao=pregao,
            quantidade__gt=0,
        )
        .select_related(
            "escola",
            "escola__municipio",
            "item",
        )
        .order_by(
            "escola__nome_escola",
            "item__nome_item",
        )
    )

    escolas_dict = {}

    valor_total_geral_disponivel = Decimal("0")
    total_contratos = 0
    total_itens_disponiveis = 0

    for quantitativo in quantitativos_escolas:
        escola = quantitativo.escola
        item = quantitativo.item
        quantidade_escola = quantitativo.quantidade or Decimal("0")

        item_pregao = itens_pregao.get(item.id)

        if not item_pregao:
            continue

        resultado = resultados.get(item_pregao.id)

        if not resultado or not resultado.primeiro_fornecedor:
            continue

        quantidade_ja_contratada = obter_quantidade_ja_contratada(quantitativo)
        saldo_disponivel = quantidade_escola - quantidade_ja_contratada

        if saldo_disponivel <= 0:
            continue

        quantidade_distratada_primeiro = obter_quantidade_distratada_primeiro_colocado(
            quantitativo,
            resultado.primeiro_fornecedor,
        )
        quantidade_substituta_ja_contratada = obter_quantidade_ja_contratada_substituta(
            quantitativo,
            resultado.primeiro_fornecedor,
        )
        saldo_substituicao_disponivel = quantidade_distratada_primeiro - quantidade_substituta_ja_contratada

        if saldo_substituicao_disponivel < 0:
            saldo_substituicao_disponivel = Decimal("0")

        opcoes_fornecedores = obter_opcoes_fornecedores_para_contrato(
            resultado,
            quantitativo,
        )

        if not opcoes_fornecedores:
            continue

        for opcao in opcoes_fornecedores:
            fornecedor = opcao["fornecedor"]
            valor_unitario = opcao["valor_unitario"]

            if opcao["contratacao_substituta"]:
                saldo_opcao = min(saldo_disponivel, saldo_substituicao_disponivel)
            else:
                saldo_opcao = saldo_disponivel

            if saldo_opcao <= 0:
                continue

            valor_total_disponivel_item = saldo_opcao * valor_unitario

            proposta = propostas.get((item_pregao.id, fornecedor.id))
            marca = proposta.marca if proposta else ""

            if escola.id not in escolas_dict:
                escolas_dict[escola.id] = {
                    "escola": escola,
                    "fornecedores": {},
                    "valor_total_escola": Decimal("0"),
                }

            escola_grupo = escolas_dict[escola.id]

            chave_fornecedor = fornecedor.id

            if chave_fornecedor not in escola_grupo["fornecedores"]:
                escola_grupo["fornecedores"][chave_fornecedor] = {
                    "fornecedor": fornecedor,
                    "itens": [],
                    "valor_total_contrato": Decimal("0"),
                    "tem_substituicao": False,
                }
                total_contratos += 1

            fornecedor_grupo = escola_grupo["fornecedores"][chave_fornecedor]

            if opcao["contratacao_substituta"]:
                fornecedor_grupo["tem_substituicao"] = True

            fornecedor_grupo["itens"].append(
                {
                    "quantitativo_escola_id": quantitativo.id,
                    "ordem": item_pregao.ordem,
                    "item": item,
                    "marca": marca,
                    "quantidade_escola": quantidade_escola,
                    "quantidade_ja_contratada": quantidade_ja_contratada,
                    "saldo_disponivel": saldo_opcao,
                    "unidade": item.get_unidade_medida_display(),
                    "valor_unitario": valor_unitario,
                    "valor_total_disponivel_item": valor_total_disponivel_item,
                    "posicao_fornecedor": opcao["posicao_rotulo"],
                    "contratacao_substituta": opcao["contratacao_substituta"],
                }
            )

            fornecedor_grupo["valor_total_contrato"] += valor_total_disponivel_item
            escola_grupo["valor_total_escola"] += valor_total_disponivel_item
            valor_total_geral_disponivel += valor_total_disponivel_item
            total_itens_disponiveis += 1

    escolas_contratos = []

    for escola_grupo in escolas_dict.values():
        fornecedores_lista = sorted(
            escola_grupo["fornecedores"].values(),
            key=lambda grupo: grupo["fornecedor"].razao_social,
        )

        escolas_contratos.append(
            {
                "escola": escola_grupo["escola"],
                "fornecedores": fornecedores_lista,
                "valor_total_escola": escola_grupo["valor_total_escola"],
            }
        )

    escolas_contratos = sorted(
        escolas_contratos,
        key=lambda grupo: grupo["escola"].nome_escola,
    )

    municipios = pregao.municipios.all().order_by("nome")

    contratos_gerados = (
        ContratoGerado.objects.filter(pregao=pregao)
        .select_related("escola", "fornecedor")
        .order_by("-criado_em")
    )

    return render(
        request,
        "documentos/contratos_pregao.html",
        {
            "pregao": pregao,
            "municipios": municipios,
            "escolas_contratos": escolas_contratos,
            "total_contratos": total_contratos,
            "total_itens_disponiveis": total_itens_disponiveis,
            "valor_total_geral": valor_total_geral_disponivel,
            "contratos_gerados": contratos_gerados,
        },
    )

def previa_contrato(request, pregao_id, escola_id, fornecedor_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)
    escola = get_object_or_404(Escola, id=escola_id)
    fornecedor = get_object_or_404(Fornecedor, id=fornecedor_id)

    if request.method != "POST":
        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    selecionados = request.POST.getlist("selecionar_item")

    if not selecionados:
        messages.error(request, "Selecione pelo menos um item para gerar a prévia do contrato.")
        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    itens_pregao = {
        pregao_item.item_id: pregao_item
        for pregao_item in PregaoItem.objects.filter(
            pregao=pregao
        ).select_related("item")
    }

    resultados = {
        resultado.pregao_item_id: resultado
        for resultado in ResultadoItem.objects.filter(
            pregao=pregao,
        ).select_related(
            "pregao_item",
            "pregao_item__item",
            "primeiro_fornecedor",
            "segundo_fornecedor",
            "terceiro_fornecedor",
        )
    }

    propostas = {
        (proposta.pregao_item_id, proposta.fornecedor_id): proposta
        for proposta in PropostaInicialItem.objects.filter(
            pregao=pregao,
            fornecedor=fornecedor,
        )
    }

    quantitativos = (
        QuantitativoEscola.objects.filter(
            id__in=selecionados,
            pregao=pregao,
            escola=escola,
            quantidade__gt=0,
        )
        .select_related("item", "escola", "escola__municipio")
        .order_by("item__nome_item")
    )

    itens_contrato = []
    valor_total_contrato = Decimal("0")
    erros = []

    for quantitativo in quantitativos:
        item = quantitativo.item
        item_pregao = itens_pregao.get(item.id)

        if not item_pregao:
            continue

        resultado = resultados.get(item_pregao.id)
        dados_fornecedor = obter_dados_fornecedor_resultado(resultado, fornecedor)

        if not resultado or not dados_fornecedor:
            continue

        houve_distrato = existe_distrato_primeiro_colocado(
            quantitativo,
            resultado.primeiro_fornecedor,
        )

        if dados_fornecedor["posicao_codigo"] == "1" and houve_distrato:
            continue

        if dados_fornecedor["posicao_codigo"] in ["2", "3"] and not houve_distrato:
            continue

        quantidade_ja_contratada = obter_quantidade_ja_contratada(quantitativo)
        saldo_disponivel = quantitativo.quantidade - quantidade_ja_contratada

        if dados_fornecedor["posicao_codigo"] in ["2", "3"]:
            quantidade_distratada_primeiro = obter_quantidade_distratada_primeiro_colocado(
                quantitativo,
                resultado.primeiro_fornecedor,
            )
            quantidade_substituta_ja_contratada = obter_quantidade_ja_contratada_substituta(
                quantitativo,
                resultado.primeiro_fornecedor,
            )
            saldo_substituicao_disponivel = quantidade_distratada_primeiro - quantidade_substituta_ja_contratada

            if saldo_substituicao_disponivel < 0:
                saldo_substituicao_disponivel = Decimal("0")

            saldo_disponivel = min(saldo_disponivel, saldo_substituicao_disponivel)

        if saldo_disponivel <= 0:
            erros.append(f"O item {item.nome_item} não possui saldo disponível para contratação.")
            continue

        quantidade_texto = request.POST.get(
            f"quantidade_contratar_{quantitativo.id}",
            "",
        ).strip()

        if not quantidade_texto:
            erros.append(f"Informe a quantidade a contratar do item {item.nome_item}.")
            continue

        quantidade_texto = quantidade_texto.replace(",", ".")

        try:
            quantidade_contratar = Decimal(quantidade_texto)
        except InvalidOperation:
            erros.append(f"A quantidade informada para o item {item.nome_item} é inválida.")
            continue

        if quantidade_contratar <= 0:
            erros.append(f"A quantidade do item {item.nome_item} deve ser maior que zero.")
            continue

        if quantidade_contratar > saldo_disponivel:
            erros.append(
                f"A quantidade do item {item.nome_item} ultrapassa o saldo disponível. "
                f"Saldo disponível: {saldo_disponivel}."
            )
            continue

        valor_unitario = dados_fornecedor["valor_unitario"]
        valor_total_item = quantidade_contratar * valor_unitario

        proposta = propostas.get((item_pregao.id, fornecedor.id))
        marca = proposta.marca if proposta else ""

        itens_contrato.append(
            {
                "quantitativo_escola_id": quantitativo.id,
                "ordem": item_pregao.ordem,
                "item": item,
                "marca": marca,
                "unidade": item.get_unidade_medida_display(),
                "quantidade_total_escola": quantitativo.quantidade,
                "quantidade_ja_contratada": quantidade_ja_contratada,
                "saldo_disponivel": saldo_disponivel,
                "quantidade_contratar": quantidade_contratar,
                "saldo_apos_contrato": saldo_disponivel - quantidade_contratar,
                "valor_unitario": valor_unitario,
                "valor_total_item": valor_total_item,
                "posicao_fornecedor": dados_fornecedor["posicao_rotulo"],
                "contratacao_substituta": dados_fornecedor["posicao_codigo"] in ["2", "3"],
            }
        )

        valor_total_contrato += valor_total_item

    if erros:
        for erro in erros:
            messages.error(request, erro)

        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    if not itens_contrato:
        messages.error(
            request,
            "Nenhum item válido foi encontrado para esta escola e fornecedor.",
        )
        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    return render(
        request,
        "documentos/previa_contrato.html",
        {
            "pregao": pregao,
            "escola": escola,
            "fornecedor": fornecedor,
            "itens_contrato": itens_contrato,
            "valor_total_contrato": valor_total_contrato,
        },
    )


def gerar_numero_contrato_por_escola_ano(escola, ano):
    """
    Gera a numeração do contrato por escola e por ano.

    Regra:
    - cada escola possui sequência própria;
    - a sequência reinicia a cada ano;
    - contratos cancelados, distratados e parcialmente distratados continuam contando,
      para evitar reaproveitamento de número já emitido.
    """

    ano = int(ano)

    maior_sequencial = (
        ContratoGerado.objects.select_for_update()
        .filter(
            escola=escola,
            ano_contrato=ano,
            numero_sequencial__isnull=False,
        )
        .aggregate(maior=Max("numero_sequencial"))
        .get("maior")
    ) or 0

    # Compatibilidade com contratos criados antes da inclusão dos campos
    # numero_sequencial/ano_contrato. Isso evita gerar número repetido para
    # uma mesma escola no mesmo ano caso já existam contratos antigos.
    numeros_antigos = (
        ContratoGerado.objects.select_for_update()
        .filter(
            escola=escola,
            numero_contrato__endswith=f"/{ano}",
        )
        .values_list("numero_contrato", flat=True)
    )

    for numero_antigo in numeros_antigos:
        match = re.match(r"^\s*(\d+)\s*/\s*(\d{4})\s*$", numero_antigo or "")

        if not match:
            continue

        ano_antigo = int(match.group(2))

        if ano_antigo != ano:
            continue

        sequencial_antigo = int(match.group(1))

        if sequencial_antigo > maior_sequencial:
            maior_sequencial = sequencial_antigo

    proximo_sequencial = maior_sequencial + 1
    numero_contrato = f"{proximo_sequencial:03d}/{ano}"

    return proximo_sequencial, ano, numero_contrato

def registrar_contrato(request, pregao_id, escola_id, fornecedor_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)
    escola = get_object_or_404(Escola, id=escola_id)
    fornecedor = get_object_or_404(Fornecedor, id=fornecedor_id)

    if request.method != "POST":
        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    quantitativo_ids = request.POST.getlist("quantitativo_escola_id")

    if not quantitativo_ids:
        messages.error(request, "Nenhum item foi enviado para gerar contrato.")
        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    itens_pregao = {
        pregao_item.item_id: pregao_item
        for pregao_item in PregaoItem.objects.filter(
            pregao=pregao
        ).select_related("item")
    }

    resultados = {
        resultado.pregao_item_id: resultado
        for resultado in ResultadoItem.objects.filter(
            pregao=pregao,
        ).select_related(
            "pregao_item",
            "pregao_item__item",
            "primeiro_fornecedor",
            "segundo_fornecedor",
            "terceiro_fornecedor",
        )
    }

    propostas = {
        (proposta.pregao_item_id, proposta.fornecedor_id): proposta
        for proposta in PropostaInicialItem.objects.filter(
            pregao=pregao,
            fornecedor=fornecedor,
        )
    }

    quantitativos = (
        QuantitativoEscola.objects.filter(
            id__in=quantitativo_ids,
            pregao=pregao,
            escola=escola,
            quantidade__gt=0,
        )
        .select_related("item")
        .order_by("item__nome_item")
    )

    itens_para_salvar = []
    valor_total_contrato = Decimal("0")
    erros = []

    for quantitativo in quantitativos:
        item = quantitativo.item
        item_pregao = itens_pregao.get(item.id)

        if not item_pregao:
            continue

        resultado = resultados.get(item_pregao.id)
        dados_fornecedor = obter_dados_fornecedor_resultado(resultado, fornecedor)

        if not resultado or not dados_fornecedor:
            continue

        houve_distrato = existe_distrato_primeiro_colocado(
            quantitativo,
            resultado.primeiro_fornecedor,
        )

        if dados_fornecedor["posicao_codigo"] == "1" and houve_distrato:
            continue

        if dados_fornecedor["posicao_codigo"] in ["2", "3"] and not houve_distrato:
            continue

        quantidade_ja_contratada = obter_quantidade_ja_contratada(quantitativo)
        saldo_disponivel = quantitativo.quantidade - quantidade_ja_contratada

        if dados_fornecedor["posicao_codigo"] in ["2", "3"]:
            quantidade_distratada_primeiro = obter_quantidade_distratada_primeiro_colocado(
                quantitativo,
                resultado.primeiro_fornecedor,
            )
            quantidade_substituta_ja_contratada = obter_quantidade_ja_contratada_substituta(
                quantitativo,
                resultado.primeiro_fornecedor,
            )
            saldo_substituicao_disponivel = quantidade_distratada_primeiro - quantidade_substituta_ja_contratada

            if saldo_substituicao_disponivel < 0:
                saldo_substituicao_disponivel = Decimal("0")

            saldo_disponivel = min(saldo_disponivel, saldo_substituicao_disponivel)

        quantidade_texto = request.POST.get(
            f"quantidade_contratar_{quantitativo.id}",
            "",
        ).strip()

        if not quantidade_texto:
            erros.append(f"Informe a quantidade do item {item.nome_item}.")
            continue

        quantidade_texto = quantidade_texto.replace(",", ".")

        try:
            quantidade_contratar = Decimal(quantidade_texto)
        except InvalidOperation:
            erros.append(f"Quantidade inválida para o item {item.nome_item}.")
            continue

        if quantidade_contratar <= 0:
            erros.append(f"A quantidade do item {item.nome_item} deve ser maior que zero.")
            continue

        if quantidade_contratar > saldo_disponivel:
            erros.append(
                f"O item {item.nome_item} não possui saldo suficiente. "
                f"Saldo disponível: {saldo_disponivel}."
            )
            continue

        valor_unitario = dados_fornecedor["valor_unitario"]
        valor_total_item = quantidade_contratar * valor_unitario

        proposta = propostas.get((item_pregao.id, fornecedor.id))
        marca = proposta.marca if proposta else ""

        itens_para_salvar.append(
            {
                "quantitativo": quantitativo,
                "item": item,
                "marca": marca,
                "unidade": item.get_unidade_medida_display(),
                "quantidade_contratar": quantidade_contratar,
                "valor_unitario": valor_unitario,
                "valor_total_item": valor_total_item,
                "posicao_fornecedor": dados_fornecedor["posicao_rotulo"],
                "contratacao_substituta": dados_fornecedor["posicao_codigo"] in ["2", "3"],
            }
        )

        valor_total_contrato += valor_total_item

    if erros:
        for erro in erros:
            messages.error(request, erro)

        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    if not itens_para_salvar:
        messages.error(request, "Nenhum item válido para registrar contrato.")
        return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

    with transaction.atomic():
        numero_sequencial, ano_contrato, numero_contrato = gerar_numero_contrato_por_escola_ano(
            escola,
            pregao.ano,
        )

        contrato = ContratoGerado.objects.create(
            pregao=pregao,
            escola=escola,
            fornecedor=fornecedor,
            numero_contrato=numero_contrato,
            numero_sequencial=numero_sequencial,
            ano_contrato=ano_contrato,
            valor_total=valor_total_contrato,
            status=ContratoGerado.STATUS_GERADO,
        )

        for item in itens_para_salvar:
            ContratoItemGerado.objects.create(
                contrato=contrato,
                quantitativo_escola=item["quantitativo"],
                item=item["item"],
                marca=item["marca"],
                unidade=item["unidade"],
                quantidade_contratada=item["quantidade_contratar"],
                valor_unitario=item["valor_unitario"],
                valor_total=item["valor_total_item"],
            )

    messages.success(
        request,
        f"Contrato {contrato.numero_contrato} registrado com sucesso.",
    )

    return redirect("documentos:contratos_pregao", pregao_id=pregao.id)

def formatar_moeda_br(valor):
    if valor is None:
        valor = Decimal("0")

    valor = Decimal(valor)

    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    return f"R$ {texto}"


def formatar_decimal_br(valor):
    if valor is None:
        return ""

    valor = Decimal(valor)

    texto = f"{valor:,.3f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    if texto.endswith(",000"):
        texto = texto[:-4]

    return texto



def converter_decimal_brasileiro(valor):
    """
    Converte números digitados no padrão brasileiro para Decimal.

    Exemplos aceitos:
    - 1220      -> 1220
    - 1.220     -> 1220
    - 1.220,50  -> 1220.50
    - 1220,50   -> 1220.50
    - 1220.50   -> 1220.50
    """
    texto = str(valor or "").strip()

    if not texto:
        raise InvalidOperation

    texto = (
        texto.replace("R$", "")
        .replace(" ", "")
        .replace("\xa0", "")
        .strip()
    )

    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "." in texto:
        partes = texto.split(".")

        # Quando vem do filtro quantidade_br, valores inteiros grandes
        # chegam como 1.220, 12.000, 1.221 etc. Nesses casos o ponto
        # é separador de milhar, não casa decimal.
        if len(partes) > 1 and all(len(parte) == 3 for parte in partes[1:]) and all(parte.isdigit() for parte in partes):
            texto = "".join(partes)

    return Decimal(texto)


def data_por_extenso(data):
    meses = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]

    return f"{data.day} de {meses[data.month - 1]} de {data.year}"


def valor_por_extenso(valor):
    valor = Decimal(valor or 0).quantize(Decimal("0.01"))

    reais = int(valor)
    centavos = int((valor - Decimal(reais)) * 100)

    partes = []

    if reais == 1:
        partes.append("um real")
    elif reais > 1:
        partes.append(f"{num2words(reais, lang='pt_BR')} reais")
    else:
        partes.append("zero real")

    if centavos == 1:
        partes.append("um centavo")
    elif centavos > 1:
        partes.append(f"{num2words(centavos, lang='pt_BR')} centavos")

    return " e ".join(partes)


def substituir_texto_em_paragrafo(paragrafo, substituicoes):
    # Primeiro tenta substituir preservando os runs.
    for run in paragrafo.runs:
        for chave, valor in substituicoes.items():
            if chave in run.text:
                run.text = run.text.replace(chave, valor)

    # Se o Word tiver quebrado o placeholder entre runs, reconstrói o parágrafo.
    texto_completo = paragrafo.text

    if any(chave in texto_completo for chave in substituicoes):
        novo_texto = texto_completo

        for chave, valor in substituicoes.items():
            novo_texto = novo_texto.replace(chave, valor)

        if paragrafo.runs:
            paragrafo.runs[0].text = novo_texto

            for run in paragrafo.runs[1:]:
                run.text = ""


def substituir_placeholders_documento(documento, substituicoes):
    for paragrafo in documento.paragraphs:
        substituir_texto_em_paragrafo(paragrafo, substituicoes)

    for tabela in documento.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for paragrafo in celula.paragraphs:
                    substituir_texto_em_paragrafo(paragrafo, substituicoes)

    for secao in documento.sections:
        for paragrafo in secao.header.paragraphs:
            substituir_texto_em_paragrafo(paragrafo, substituicoes)

        for paragrafo in secao.footer.paragraphs:
            substituir_texto_em_paragrafo(paragrafo, substituicoes)


def encontrar_paragrafo_placeholder(documento, placeholder):
    for paragrafo in documento.paragraphs:
        if placeholder in paragrafo.text:
            return paragrafo

    for tabela in documento.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for paragrafo in celula.paragraphs:
                    if placeholder in paragrafo.text:
                        return paragrafo

    return None

def aplicar_bordas_tabela(tabela):
    tbl = tabela._tbl
    tbl_pr = tbl.tblPr

    tbl_borders = tbl_pr.find(qn("w:tblBorders"))
    if tbl_borders is None:
        tbl_borders = OxmlElement("w:tblBorders")
        tbl_pr.append(tbl_borders)

    for border_name in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        border = tbl_borders.find(qn(f"w:{border_name}"))

        if border is None:
            border = OxmlElement(f"w:{border_name}")
            tbl_borders.append(border)

        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "8")
        border.set(qn("w:space"), "0")
        border.set(qn("w:color"), "000000")

def definir_largura_coluna(celula, largura):
    tc = celula._tc
    tc_pr = tc.get_or_add_tcPr()

    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)

    tc_w.set(qn("w:w"), str(largura))
    tc_w.set(qn("w:type"), "dxa")


def aplicar_fundo_celula(celula, cor_hex):
    tc_pr = celula._tc.get_or_add_tcPr()

    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)

    shading.set(qn("w:fill"), cor_hex)





def formatar_celula_tabela(
    celula,
    negrito=False,
    fundo=None,
    alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
    tamanho_fonte=9,
):
    celula.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    if fundo:
        aplicar_fundo_celula(celula, fundo)

    for paragrafo in celula.paragraphs:
        paragrafo.alignment = alinhamento

        for run in paragrafo.runs:
            run.font.name = "Arial"
            run.font.size = Pt(tamanho_fonte)
            run.bold = negrito

def inserir_tabela_itens_no_documento(documento, placeholder, itens):
    paragrafo = encontrar_paragrafo_placeholder(documento, placeholder)

    if not paragrafo:
        return

    substituir_texto_em_paragrafo(paragrafo, {placeholder: ""})

    dados_tabela = [
        [
            "Item",
            "Marca",
            "Quantidade",
            "Unid.",
            "Fornecedor/CNPJ",
            "Valor Unitário",
            "Valor Total",
        ]
    ]

    for item in itens:
        dados_tabela.append(
            [
                item.item.nome_item,
                item.marca,
                formatar_decimal_br(item.quantidade_contratada),
                item.unidade,
                f"{item.contrato.fornecedor.razao_social} - {obter_cpf_cnpj_fornecedor_chamada(item.contrato.fornecedor)}",
                formatar_moeda_br(item.valor_unitario),
                formatar_moeda_br(item.valor_total),
            ]
        )

    tabela = documento.add_table(rows=0, cols=7)

    try:
        tabela.style = "Table Grid"
    except KeyError:
        pass

    tabela.alignment = WD_TABLE_ALIGNMENT.CENTER
    aplicar_bordas_tabela(tabela)

    # Larguras aproximadas em twips.
    # 1 polegada = 1440 twips.
    larguras = [
        1900,  # Item
        1100,  # Marca
        1100,  # Quantidade
        900,   # Unidade
        2300,  # Fornecedor/CNPJ
        1100,  # Valor Unitário
        1100,  # Valor Total
    ]

    for indice_linha, linha_dados in enumerate(dados_tabela):
        linha = tabela.add_row()

        for indice_coluna, valor in enumerate(linha_dados):
            celula = linha.cells[indice_coluna]
            celula.text = str(valor)

            definir_largura_coluna(celula, larguras[indice_coluna])

            if indice_linha == 0:
                formatar_celula_tabela(
                    celula,
                    negrito=True,
                    fundo="D9D9D9",
                    alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
                    tamanho_fonte=9,
                )
            else:
                if indice_coluna in [0, 4]:
                    alinhamento = WD_ALIGN_PARAGRAPH.LEFT
                else:
                    alinhamento = WD_ALIGN_PARAGRAPH.CENTER

                formatar_celula_tabela(
                    celula,
                    negrito=False,
                    fundo=None,
                    alinhamento=alinhamento,
                    tamanho_fonte=9,
                )

    # Insere a tabela logo depois do parágrafo onde estava o placeholder.
    paragrafo._p.addnext(tabela._tbl)

def visualizar_contrato(request, contrato_id):
    contrato = get_object_or_404(
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        ),
        id=contrato_id,
    )

    if not usuario_pode_acessar_contrato(request, contrato):
        return redirect("documentos:contratos_gerados")


    itens = (
        ContratoItemGerado.objects.filter(contrato=contrato)
        .select_related("item")
        .order_by("item__nome_item")
    )

    realinhamentos = (
        RealinhamentoPreco.objects.filter(
            contrato=contrato,
            status=RealinhamentoPreco.STATUS_REGISTRADO,
        )
        .prefetch_related("itens", "itens__contrato_item", "itens__contrato_item__item")
        .order_by("-data_realinhamento", "-criado_em")
    )


    trocas_marca = (
        TrocaMarca.objects.filter(
            contrato=contrato,
            status=TrocaMarca.STATUS_REGISTRADO,
        )
        .prefetch_related("itens", "itens__contrato_item", "itens__contrato_item__item")
        .order_by("-data_troca_marca", "-criado_em")
    )

    return render(
        request,
        "documentos/visualizar_contrato.html",
        {
            "contrato": contrato,
            "pregao": contrato.pregao,
            "escola": contrato.escola,
            "fornecedor": contrato.fornecedor,
            "itens": itens,
            "realinhamentos": realinhamentos,
            "trocas_marca": trocas_marca,
        },
    )

def corrigir_margens_documento(documento):
    """
    Corrige margens inválidas vindas do modelo Word.

    Alguns arquivos .docx podem trazer margens com valor decimal no XML,
    como 1115.6692913385832. O python-docx espera inteiro em twips e quebra
    ao tentar calcular a largura disponível para inserir tabela.
    """
    for secao in documento.sections:
        secao.top_margin = Cm(2.5)
        secao.bottom_margin = Cm(2.5)
        secao.left_margin = Cm(2.5)
        secao.right_margin = Cm(2.5)

def encontrar_placeholders_pendentes_documento(documento):
    """
    Procura no documento Word qualquer texto no formato {{ALGUMA_COISA}}
    que ainda não tenha sido substituído.
    """
    padrao = re.compile(r"\{\{[^{}]+\}\}")
    pendentes = set()

    def verificar_texto(texto):
        encontrados = padrao.findall(texto or "")
        for item in encontrados:
            pendentes.add(item)

    for paragrafo in documento.paragraphs:
        verificar_texto(paragrafo.text)

    for tabela in documento.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for paragrafo in celula.paragraphs:
                    verificar_texto(paragrafo.text)

    for secao in documento.sections:
        for paragrafo in secao.header.paragraphs:
            verificar_texto(paragrafo.text)

        for paragrafo in secao.footer.paragraphs:
            verificar_texto(paragrafo.text)

    return sorted(pendentes)

def encontrar_libreoffice():
    """
    Localiza o executável do LibreOffice em Windows ou Linux.
    """

    caminhos_possiveis = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "libreoffice",
        "soffice",
    ]

    for caminho in caminhos_possiveis:
        if os.path.exists(caminho):
            return caminho

        encontrado = shutil.which(caminho)

        if encontrado:
            return encontrado

    return None


def remover_paragrafo(paragrafo):
    elemento = paragrafo._element
    elemento.getparent().remove(elemento)
    paragrafo._p = paragrafo._element = None


def remover_tabelas_documento(documento):
    for tabela in list(documento.tables):
        elemento = tabela._element
        elemento.getparent().remove(elemento)


def inserir_tabela_rescisao_no_documento(documento, contrato, itens):
    dados_tabela = [
        [
            "Escola",
            "CNPJ da Escola",
            "Nº do Contrato",
            "Item Rescindido",
            "Quantidade",
            "Valor do Item",
        ]
    ]

    for item in itens:
        if isinstance(item, DistratoContratoItem):
            contrato_item = item.contrato_item
            nome_item = contrato_item.item.nome_item
            quantidade = formatar_decimal_br(item.quantidade_distratada)
            valor_total = item.valor_total
        else:
            contrato_item = item
            nome_item = contrato_item.item.nome_item
            quantidade = formatar_decimal_br(contrato_item.quantidade_contratada)
            valor_total = contrato_item.valor_total

        dados_tabela.append(
            [
                contrato.escola.nome_escola or "",
                contrato.escola.cnpj or "",
                contrato.numero_contrato or "",
                nome_item or "",
                quantidade,
                formatar_moeda_br(valor_total),
            ]
        )

    tabela = documento.add_table(rows=0, cols=6)

    try:
        tabela.style = "Table Grid"
    except KeyError:
        pass

    tabela.alignment = WD_TABLE_ALIGNMENT.CENTER
    aplicar_bordas_tabela(tabela)

    larguras = [
        2100,  # Escola
        1400,  # CNPJ da Escola
        1200,  # Nº do Contrato
        2400,  # Item Rescindido
        1100,  # Quantidade
        1300,  # Valor do Item
    ]

    for indice_linha, linha_dados in enumerate(dados_tabela):
        linha = tabela.add_row()

        for indice_coluna, valor in enumerate(linha_dados):
            celula = linha.cells[indice_coluna]
            celula.text = str(valor)
            definir_largura_coluna(celula, larguras[indice_coluna])

            if indice_linha == 0:
                formatar_celula_tabela(
                    celula,
                    negrito=True,
                    fundo="D9D9D9",
                    alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
                    tamanho_fonte=9,
                )
            else:
                alinhamento = WD_ALIGN_PARAGRAPH.LEFT if indice_coluna in [0, 3] else WD_ALIGN_PARAGRAPH.CENTER
                formatar_celula_tabela(
                    celula,
                    negrito=False,
                    fundo=None,
                    alinhamento=alinhamento,
                    tamanho_fonte=9,
                )

    paragrafo_anexo = encontrar_paragrafo_placeholder(documento, "ANEXO I")

    if paragrafo_anexo:
        paragrafo_anexo._p.addnext(tabela._tbl)
    else:
        documento.add_paragraph("ANEXO I - RELAÇÃO DE CONTRATOS E ITENS")
        documento.paragraphs[-1]._p.addnext(tabela._tbl)


def obter_ultimo_distrato_contrato(contrato):
    return (
        DistratoContrato.objects.filter(contrato=contrato)
        .prefetch_related("itens", "itens__contrato_item", "itens__contrato_item__item")
        .order_by("-criado_em")
        .first()
    )


def montar_documento_rescisao_total_word(contrato):
    contrato = ContratoGerado.objects.select_related(
        "pregao",
        "escola",
        "escola__municipio",
        "fornecedor",
    ).get(id=contrato.id)

    if contrato.status not in [
        ContratoGerado.STATUS_DISTRATADO,
        ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
    ]:
        raise ValueError("O termo de rescisão só pode ser gerado para contrato distratado ou parcialmente distratado.")

    ultimo_distrato = obter_ultimo_distrato_contrato(contrato)

    if ultimo_distrato:
        itens = (
            DistratoContratoItem.objects.filter(distrato=ultimo_distrato)
            .select_related("contrato_item", "contrato_item__item")
            .order_by("contrato_item__item__nome_item")
        )
        data_distrato = ultimo_distrato.data_distrato
        motivo_distrato = ultimo_distrato.motivo_distrato
        tipo_distrato = ultimo_distrato.tipo
    else:
        # Compatibilidade com distratos totais registrados antes da criação
        # dos modelos DistratoContrato/DistratoContratoItem.
        itens = (
            ContratoItemGerado.objects.filter(contrato=contrato)
            .select_related("item", "contrato", "contrato__fornecedor")
            .order_by("item__nome_item")
        )
        data_distrato = contrato.data_distrato or timezone.localdate()
        motivo_distrato = contrato.motivo_distrato or "motivo registrado no sistema"
        tipo_distrato = DistratoContrato.TIPO_TOTAL

    pregao = contrato.pregao
    escola = contrato.escola
    fornecedor = contrato.fornecedor
    municipio = escola.municipio
    municipio_uf = f"{municipio.nome}/{municipio.uf}" if municipio and municipio.uf else (municipio.nome if municipio else "")

    if tipo_distrato == DistratoContrato.TIPO_PARCIAL:
        modelo_nome = "modelo_termo_rescisao_parcial.docx"
    else:
        modelo_nome = "modelo_termo_rescisao_total.docx"

    modelo_path = (
        Path(settings.BASE_DIR)
        / "modelos"
        / "distratos"
        / modelo_nome
    )

    if not modelo_path.exists():
        raise FileNotFoundError(
            f"Modelo de termo de rescisão não encontrado em modelos/distratos/{modelo_nome}"
        )

    documento = Document(modelo_path)
    corrigir_margens_documento(documento)

    numero_pregao = f"{pregao.numero}/{pregao.ano}"
    data_extenso = data_por_extenso(data_distrato)
    lista_escolas = f"{escola.nome_escola} (CNPJ {escola.cnpj})"
    endereco_escola = f"{escola.endereco}"
    if getattr(escola, "numero", ""):
        endereco_escola += f", nº {escola.numero}"
    if getattr(escola, "bairro", ""):
        endereco_escola += f", {escola.bairro}"
    if escola.municipio:
        endereco_escola += f", {municipio_uf}"

    substituicoes = {
        "[000/2026]": numero_pregao,
        "[000/202X]": numero_pregao,
        "[NOME DA EMPRESA]": fornecedor.razao_social or "",
        "[00.000.000/0000-00]": fornecedor.cnpj or "",
        "[Data]": data_extenso,
        "[motivo: ex: impossibilidade de manutenção dos preços/logística]": motivo_distrato or "motivo registrado no sistema",
        "[Município - UF]": municipio_uf,
        "[Dia] de [Mês] de 2026": data_extenso,
        "[Dia]": str(data_distrato.day),
        "[Mês]": data_distrato.strftime("%B"),
        "[Listar todas as escolas envolvidas]": lista_escolas,
        "Escola A (CNPJ...);": lista_escolas + ";",
        "Escola B (CNPJ...);": "",
        "Escola C (CNPJ...);": "",
        "[Estadual/Municipal]": "Estadual",

        "___/202__": contrato.numero_contrato or "",
        "___/2025": contrato.numero_contrato or "",
        "___/202__": contrato.numero_contrato or "",
        "Pregão Presencial nº ___/202__": f"Pregão Presencial nº {numero_pregao}",
        "Processo Administrativo nº ___/202__": f"Processo Administrativo nº {numero_pregao}",
        "(Nome da Escola/Unidade Executora)": escola.nome_escola or "",
        "CNPJ nº __________": f"CNPJ nº {escola.cnpj or ''}",
        "com sede à __________________________": f"com sede à {endereco_escola}",
        "Sr.(a) ________________________": f"Sr.(a) {escola.presidente_cdce or ''}",
        "ALIMEX LTDA": fornecedor.razao_social or "",
        "inscrita no CNPJ nº __________": f"inscrita no CNPJ nº {fornecedor.cnpj or ''}",
        "neste ato representada por ____________________________": f"neste ato representada por {fornecedor.representante_legal or ''}",
        "Contrato nº ___/2025": f"Contrato nº {contrato.numero_contrato or ''}",
        "CONTRATO Nº ___/202__": f"CONTRATO Nº {contrato.numero_contrato or ''}",
        "ITEM ___": "item(ns) relacionado(s) no Anexo I",
        "item ___": "item(ns) relacionado(s) no Anexo I",
        "R$ _________": formatar_moeda_br(ultimo_distrato.valor_total if ultimo_distrato else contrato.valor_total),
        "Local e data.": f"{municipio_uf}, {data_extenso}.",
        "Nome do(a) Diretor(a)": escola.presidente_cdce or "Nome do(a) Diretor(a)",
        "Representante Legal": fornecedor.representante_legal or "Representante Legal",
    }

    substituir_placeholders_documento(documento, substituicoes)

    # Remove exemplos de escolas e tabelas originais do modelo para evitar dados fictícios no termo gerado.
    for paragrafo in list(documento.paragraphs):
        texto = paragrafo.text or ""
        if "Escola B" in texto or "Escola C" in texto:
            remover_paragrafo(paragrafo)

    remover_tabelas_documento(documento)
    inserir_tabela_rescisao_no_documento(documento, contrato, itens)

    placeholders_pendentes = encontrar_placeholders_pendentes_documento(documento)

    if placeholders_pendentes:
        lista_pendentes = ", ".join(placeholders_pendentes)
        raise ValueError(
            f"O termo de rescisão ainda possui campos não preenchidos: {lista_pendentes}."
        )

    return documento


def gerar_termo_rescisao_word(request, contrato_id):
    contrato = get_object_or_404(
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        ),
        id=contrato_id,
    )

    if not usuario_pode_acessar_contrato(request, contrato):
        return redirect("documentos:contratos_gerados")


    try:
        documento = montar_documento_rescisao_total_word(contrato)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_gerados")
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_gerados")

    arquivo_saida = BytesIO()
    documento.save(arquivo_saida)
    arquivo_saida.seek(0)

    nome_arquivo = (
        f"Termo_Rescisao_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    response = HttpResponse(
        arquivo_saida.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}.docx"'

    return response


def gerar_termo_rescisao_pdf(request, contrato_id):
    contrato = get_object_or_404(
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        ),
        id=contrato_id,
    )

    if not usuario_pode_acessar_contrato(request, contrato):
        return redirect("documentos:contratos_gerados")


    libreoffice = encontrar_libreoffice()

    if not libreoffice:
        messages.error(
            request,
            "LibreOffice não encontrado. Instale o LibreOffice ou verifique se o comando libreoffice/soffice está disponível no PATH.",
        )
        return redirect("documentos:contratos_gerados")

    try:
        documento = montar_documento_rescisao_total_word(contrato)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_gerados")
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_gerados")

    nome_base = (
        f"Termo_Rescisao_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    with tempfile.TemporaryDirectory() as pasta_temp:
        pasta_temp_path = Path(pasta_temp)

        caminho_docx = pasta_temp_path / f"{nome_base}.docx"
        caminho_pdf = pasta_temp_path / f"{nome_base}.pdf"

        documento.save(caminho_docx)

        comando = [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pasta_temp_path),
            str(caminho_docx),
        ]

        try:
            resultado = subprocess.run(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            messages.error(
                request,
                "O LibreOffice demorou muito para converter o termo de rescisão em PDF.",
            )
            return redirect("documentos:contratos_gerados")

        if resultado.returncode != 0:
            messages.error(
                request,
                f"Erro ao converter termo de rescisão para PDF pelo LibreOffice: {resultado.stderr or resultado.stdout}",
            )
            return redirect("documentos:contratos_gerados")

        if not caminho_pdf.exists():
            arquivos_pdf = list(pasta_temp_path.glob("*.pdf"))

            if arquivos_pdf:
                caminho_pdf = arquivos_pdf[0]
            else:
                messages.error(
                    request,
                    "O LibreOffice não gerou o arquivo PDF esperado.",
                )
                return redirect("documentos:contratos_gerados")

        pdf_bytes = caminho_pdf.read_bytes()

    response = HttpResponse(
        pdf_bytes,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_base}.pdf"'

    return response


# ============================================================
# CHAMADA PÚBLICA - CONTRATOS POR ESCOLA E FORNECEDOR
# ============================================================

def obter_cpf_cnpj_fornecedor_chamada(fornecedor):
    if not fornecedor:
        return ""

    tipo = getattr(fornecedor, "tipo_fornecedor_chamada", "")

    if tipo == getattr(Fornecedor, "TIPO_INDIVIDUAL", "individual"):
        return (
            getattr(fornecedor, "cpf_fornecedor_individual", "")
            or getattr(fornecedor, "cpf_representante", "")
            or getattr(fornecedor, "cnpj", "")
            or ""
        )

    return (
        getattr(fornecedor, "cnpj", "")
        or getattr(fornecedor, "cpf_fornecedor_individual", "")
        or getattr(fornecedor, "cpf_representante", "")
        or ""
    )


def obter_representante_fornecedor_chamada(fornecedor):
    return (
        getattr(fornecedor, "representante_legal", "")
        or getattr(fornecedor, "razao_social", "")
        or ""
    )


def obter_rg_fornecedor_chamada(fornecedor):
    return getattr(fornecedor, "rg_representante", "") or ""


def obter_cpf_representante_fornecedor_chamada(fornecedor):
    return (
        getattr(fornecedor, "cpf_representante", "")
        or getattr(fornecedor, "cpf_fornecedor_individual", "")
        or ""
    )


def obter_saldo_resultado_chamada_para_contrato(resultado):
    total_contratado = (
        ContratoItemGerado.objects.filter(
            contrato__pregao=resultado.pregao,
            contrato__fornecedor=resultado.fornecedor,
            contrato__status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ],
            item=resultado.item,
        )
        .aggregate(total=Sum("quantidade_contratada"))
        .get("total")
    ) or Decimal("0")

    saldo = resultado.quantidade_adjudicada - total_contratado

    if saldo < 0:
        return Decimal("0")

    return saldo


def obter_saldo_quantitativo_escola_chamada(quantitativo):
    total_contratado = (
        ContratoItemGerado.objects.filter(
            quantitativo_escola=quantitativo,
            contrato__status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ],
        )
        .aggregate(total=Sum("quantidade_contratada"))
        .get("total")
    ) or Decimal("0")

    saldo = quantitativo.quantidade - total_contratado

    if saldo < 0:
        return Decimal("0")

    return saldo


def montar_contratos_chamada_publica_possiveis(pregao):
    resultados = list(
        ResultadoChamadaPublicaItem.objects.filter(
            pregao=pregao,
            status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
        )
        .select_related("fornecedor", "item", "pregao_item", "projeto_item")
        .order_by("fornecedor__razao_social", "pregao_item__ordem", "item__nome_item")
    )

    resultados_por_item = {}

    for resultado in resultados:
        saldo_resultado = obter_saldo_resultado_chamada_para_contrato(resultado)

        if saldo_resultado <= 0:
            continue

        resultados_por_item.setdefault(resultado.item_id, []).append(
            {
                "resultado": resultado,
                "saldo_disponivel": saldo_resultado,
            }
        )

    quantitativos = (
        QuantitativoEscola.objects.filter(
            pregao=pregao,
            quantidade__gt=0,
        )
        .select_related("escola", "escola__municipio", "item")
        .order_by("escola__nome_escola", "item__nome_item")
    )

    escolas_dict = {}
    total_contratos = 0
    total_itens = 0
    valor_total_geral = Decimal("0")

    for quantitativo in quantitativos:
        saldo_escola = obter_saldo_quantitativo_escola_chamada(quantitativo)

        if saldo_escola <= 0:
            continue

        opcoes_resultado = resultados_por_item.get(quantitativo.item_id, [])

        for opcao in opcoes_resultado:
            if saldo_escola <= 0:
                break

            resultado = opcao["resultado"]
            saldo_resultado = opcao["saldo_disponivel"]

            if saldo_resultado <= 0:
                continue

            quantidade_contratar = min(saldo_escola, saldo_resultado)

            if quantidade_contratar <= 0:
                continue

            valor_total_item = quantidade_contratar * resultado.valor_unitario
            escola = quantitativo.escola
            fornecedor = resultado.fornecedor

            if escola.id not in escolas_dict:
                escolas_dict[escola.id] = {
                    "escola": escola,
                    "fornecedores": {},
                    "valor_total_escola": Decimal("0"),
                }

            escola_grupo = escolas_dict[escola.id]

            if fornecedor.id not in escola_grupo["fornecedores"]:
                escola_grupo["fornecedores"][fornecedor.id] = {
                    "fornecedor": fornecedor,
                    "itens": [],
                    "valor_total_contrato": Decimal("0"),
                }
                total_contratos += 1

            fornecedor_grupo = escola_grupo["fornecedores"][fornecedor.id]

            fornecedor_grupo["itens"].append(
                {
                    "resultado_id": resultado.id,
                    "quantitativo_escola_id": quantitativo.id,
                    "item": quantitativo.item,
                    "marca": resultado.projeto_item.marca or "",
                    "unidade": resultado.item.get_unidade_medida_display(),
                    "quantidade_contratar": quantidade_contratar,
                    "quantidade_escola": quantitativo.quantidade,
                    "saldo_escola": saldo_escola,
                    "saldo_resultado": saldo_resultado,
                    "valor_unitario": resultado.valor_unitario,
                    "valor_total_item": valor_total_item,
                }
            )

            fornecedor_grupo["valor_total_contrato"] += valor_total_item
            escola_grupo["valor_total_escola"] += valor_total_item
            valor_total_geral += valor_total_item
            total_itens += 1

            saldo_escola -= quantidade_contratar
            opcao["saldo_disponivel"] -= quantidade_contratar

    escolas_contratos = []

    for escola_grupo in escolas_dict.values():
        fornecedores_lista = sorted(
            escola_grupo["fornecedores"].values(),
            key=lambda grupo: grupo["fornecedor"].razao_social,
        )

        escolas_contratos.append(
            {
                "escola": escola_grupo["escola"],
                "fornecedores": fornecedores_lista,
                "valor_total_escola": escola_grupo["valor_total_escola"],
            }
        )

    escolas_contratos = sorted(
        escolas_contratos,
        key=lambda grupo: grupo["escola"].nome_escola,
    )

    return {
        "escolas_contratos": escolas_contratos,
        "total_contratos": total_contratos,
        "total_itens": total_itens,
        "valor_total_geral": valor_total_geral,
    }


def contratos_chamada_publica(request):
    if usuario_eh_consulta_escola(request):
        messages.error(request, "Seu perfil permite apenas consultar contratos e documentos. A geração de contratos da Chamada Pública não está disponível para usuários Consulta/Escola.")
        return redirect("documentos:resultado_chamada_publica")

    chamadas = (
        Pregao.objects.filter(tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)
        .order_by("-ano", "-numero")
    )

    pregao_id = request.GET.get("pregao")

    if pregao_id:
        return redirect("documentos:contratos_chamada_publica_pregao", pregao_id=pregao_id)

    return render(
        request,
        "documentos/contratos_chamada_publica.html",
        {
            "chamadas": chamadas,
            "pregao": None,
            "escolas_contratos": [],
            "total_contratos": 0,
            "total_itens": 0,
            "valor_total_geral": Decimal("0"),
        },
    )


def contratos_chamada_publica_pregao(request, pregao_id):
    if usuario_eh_consulta_escola(request):
        messages.error(request, "Seu perfil permite apenas consultar contratos e documentos. A geração de contratos da Chamada Pública não está disponível para usuários Consulta/Escola.")
        return redirect("documentos:resultado_chamada_publica")

    pregao = get_object_or_404(Pregao, id=pregao_id, tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)

    if request.method == "POST":
        selecionados = request.POST.getlist("selecionar_item")

        if not selecionados:
            messages.error(request, "Selecione pelo menos um item para gerar o contrato.")
            return redirect("documentos:contratos_chamada_publica_pregao", pregao_id=pregao.id)

        escola_id = request.POST.get("escola_id")
        fornecedor_id = request.POST.get("fornecedor_id")

        escola = get_object_or_404(Escola, id=escola_id)
        fornecedor = get_object_or_404(Fornecedor, id=fornecedor_id)

        itens_para_salvar = []
        erros = []

        with transaction.atomic():
            for selecionado in selecionados:
                partes = str(selecionado or "").split("|")

                if len(partes) != 2:
                    erros.append("Um dos itens selecionados possui identificação inválida.")
                    continue

                resultado_id, quantitativo_id = partes

                resultado = get_object_or_404(
                    ResultadoChamadaPublicaItem.objects.select_related(
                        "pregao",
                        "fornecedor",
                        "item",
                        "projeto_item",
                    ),
                    id=resultado_id,
                    pregao=pregao,
                    fornecedor=fornecedor,
                    status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
                )

                quantitativo = get_object_or_404(
                    QuantitativoEscola.objects.select_related("item", "escola"),
                    id=quantitativo_id,
                    pregao=pregao,
                    escola=escola,
                    item=resultado.item,
                )

                campo_quantidade = f"quantidade_{resultado_id}_{quantitativo_id}"
                quantidade_texto = (request.POST.get(campo_quantidade) or "").strip()

                if not quantidade_texto:
                    erros.append(f"Informe a quantidade do item {resultado.item.nome_item}.")
                    continue

                try:
                    quantidade = converter_decimal_brasileiro(quantidade_texto)
                except InvalidOperation:
                    erros.append(f"A quantidade do item {resultado.item.nome_item} é inválida.")
                    continue

                if quantidade <= 0:
                    erros.append(f"A quantidade do item {resultado.item.nome_item} deve ser maior que zero.")
                    continue

                saldo_escola = obter_saldo_quantitativo_escola_chamada(quantitativo)
                saldo_resultado = obter_saldo_resultado_chamada_para_contrato(resultado)
                saldo_disponivel = min(saldo_escola, saldo_resultado)

                if quantidade > saldo_disponivel:
                    erros.append(
                        f"A quantidade do item {resultado.item.nome_item} ultrapassa o saldo disponível. "
                        f"Saldo disponível: {formatar_decimal_br(saldo_disponivel)}."
                    )
                    continue

                itens_para_salvar.append(
                    {
                        "resultado": resultado,
                        "quantitativo": quantitativo,
                        "item": resultado.item,
                        "marca": resultado.projeto_item.marca or "",
                        "unidade": resultado.item.get_unidade_medida_display(),
                        "quantidade": quantidade,
                        "valor_unitario": resultado.valor_unitario,
                        "valor_total": quantidade * resultado.valor_unitario,
                    }
                )

            if erros:
                for erro in erros:
                    messages.error(request, erro)

                return redirect("documentos:contratos_chamada_publica_pregao", pregao_id=pregao.id)

            if not itens_para_salvar:
                messages.error(request, "Nenhum item válido foi selecionado para gerar contrato.")
                return redirect("documentos:contratos_chamada_publica_pregao", pregao_id=pregao.id)

            valor_total_contrato = sum(
                (item["valor_total"] for item in itens_para_salvar),
                Decimal("0"),
            )

            numero_sequencial, ano_contrato, numero_contrato = gerar_numero_contrato_por_escola_ano(
                escola,
                pregao.ano,
            )

            contrato = ContratoGerado.objects.create(
                pregao=pregao,
                escola=escola,
                fornecedor=fornecedor,
                numero_contrato=numero_contrato,
                numero_sequencial=numero_sequencial,
                ano_contrato=ano_contrato,
                valor_total=valor_total_contrato,
                status=ContratoGerado.STATUS_GERADO,
            )

            for item in itens_para_salvar:
                ContratoItemGerado.objects.create(
                    contrato=contrato,
                    quantitativo_escola=item["quantitativo"],
                    item=item["item"],
                    marca=item["marca"],
                    unidade=item["unidade"],
                    quantidade_contratada=item["quantidade"],
                    valor_unitario=item["valor_unitario"],
                    valor_total=item["valor_total"],
                )

        messages.success(request, "Contrato da Chamada Pública gerado com sucesso.")
        return redirect("documentos:contratos_gerados")

    chamadas = (
        Pregao.objects.filter(tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)
        .order_by("-ano", "-numero")
    )

    dados = montar_contratos_chamada_publica_possiveis(pregao)

    return render(
        request,
        "documentos/contratos_chamada_publica.html",
        {
            "chamadas": chamadas,
            "pregao": pregao,
            "escolas_contratos": dados["escolas_contratos"],
            "total_contratos": dados["total_contratos"],
            "total_itens": dados["total_itens"],
            "valor_total_geral": dados["valor_total_geral"],
        },
    )



def montar_documento_contrato_word(contrato):
    contrato = ContratoGerado.objects.select_related(
        "pregao",
        "escola",
        "escola__municipio",
        "fornecedor",
    ).get(id=contrato.id)

    itens = (
        ContratoItemGerado.objects.filter(contrato=contrato)
        .select_related("item", "contrato", "contrato__fornecedor")
        .order_by("item__nome_item")
    )

    pregao = contrato.pregao
    escola = contrato.escola
    fornecedor = contrato.fornecedor

    if getattr(pregao, "tipo_certame", "") == getattr(Pregao, "TIPO_CHAMADA_PUBLICA", "chamada_publica"):
        nome_modelo = "modelo_contrato_chamada_publica.docx"
    else:
        nome_modelo = "modelo_contrato_pregao.docx"

    modelo_path = (
        Path(settings.BASE_DIR)
        / "modelos"
        / "contratos"
        / nome_modelo
    )

    if not modelo_path.exists():
        raise FileNotFoundError(
            f"Modelo de contrato não encontrado em modelos/contratos/{nome_modelo}"
        )

    documento = Document(modelo_path)
    corrigir_margens_documento(documento)

    data_formatada = data_por_extenso(contrato.criado_em.date())

    numero_pregao = f"{pregao.numero}/{pregao.ano}"
    numero_contrato = contrato.numero_contrato or f"{contrato.id:04d}/{pregao.ano}"

    municipio_uf = escola.municipio.uf or "MT"

    substituicoes = {
        "0XX/202X": numero_contrato,
        "XX/202X": numero_contrato,

        "{{NÚMERO_PREGÃO}}": numero_pregao,
        "{{RAZAO_SOCIAL}}": fornecedor.razao_social or "",
        "{{CNPJ}}": obter_cpf_cnpj_fornecedor_chamada(fornecedor),
        "{{ENDERECO}}": fornecedor.endereco or "",
        "{{TELEFONE}}": fornecedor.telefone or "",
        "{{REPRESENTANTE}}": obter_representante_fornecedor_chamada(fornecedor),
        "{{RG}}": obter_rg_fornecedor_chamada(fornecedor),
        "{{ORGAO_EXPEDIDOR}}": fornecedor.orgao_expedidor_representante or "",
        "{{CPF}}": obter_cpf_representante_fornecedor_chamada(fornecedor),
        "{{E-MAIL}}": fornecedor.email or "",
        "{{BANCO_FORNECEDOR}}": fornecedor.nome_banco or "",
        "{{AGENCIA_FORNECEDOR}}": fornecedor.agencia or "",
        "{{CONTA_FORNECEDOR}}": fornecedor.conta_corrente or "",
        "{{PIX_FORNECEDOR}}": fornecedor.pix or "",

        "{{ESCOLA_NOME}}": escola.nome_escola or "",
        "{{ESCOLA_CNPJ}}": escola.cnpj or "",
        "{{ESCOLA_ENDERECO}}": escola.endereco or "",
        "{{ESCOLA_NUMERO}}": escola.numero or "",
        "{{BAIRRO_ESCOLA}}": escola.bairro or "",
        "{{ESCOLA_MUNICIPIO}}": f"{escola.municipio.nome}/{municipio_uf}",
        "{{PRESIDENTE}}": escola.presidente_cdce or "",
        "{{CDCE}}": escola.presidente_cdce or "",
        "{{RG_PRESIDENTE}}": escola.rg_presidente or "",
        "{{CPF_PRESIDENTE}}": escola.cpf_presidente or "",

        "{{DATA_FORMATADA}}": data_formatada,
        "{{VALOR_CONTRATO}}": formatar_moeda_br(contrato.valor_total),
        "{{VALOR_CONTRATO_EXTENSO}}": valor_por_extenso(contrato.valor_total),
    }

    substituir_placeholders_documento(documento, substituicoes)
    inserir_tabela_itens_no_documento(documento, "{{TABELA_ITENS}}", itens)

    placeholders_pendentes = encontrar_placeholders_pendentes_documento(documento)

    if placeholders_pendentes:
        lista_pendentes = ", ".join(placeholders_pendentes)
        raise ValueError(
            f"O contrato ainda possui campos não preenchidos: {lista_pendentes}."
        )

    return documento

def gerar_contrato_word(request, contrato_id):
    contrato = get_object_or_404(
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "fornecedor",
        ),
        id=contrato_id,
    )

    if not usuario_pode_acessar_contrato(request, contrato):
        return redirect("documentos:contratos_gerados")

    if contrato.status in [
        ContratoGerado.STATUS_CANCELADO,
        ContratoGerado.STATUS_DISTRATADO,
    ]:
        messages.error(
            request,
            "Não é possível baixar Word de contrato cancelado ou distratado.",
        )
        return redirect("documentos:contratos_gerados")
    

    try:
        documento = montar_documento_contrato_word(contrato)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)

    arquivo_saida = BytesIO()
    documento.save(arquivo_saida)
    arquivo_saida.seek(0)

    nome_arquivo = (
        f"Contrato_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    response = HttpResponse(
        arquivo_saida.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}.docx"'

    return response

def gerar_contrato_pdf(request, contrato_id):
    contrato = get_object_or_404(
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "fornecedor",
        ),
        id=contrato_id,
    )

    if not usuario_pode_acessar_contrato(request, contrato):
        return redirect("documentos:contratos_gerados")

    if contrato.status in [
        ContratoGerado.STATUS_CANCELADO,
        ContratoGerado.STATUS_DISTRATADO,
    ]:
        messages.error(
            request,
            "Não é possível baixar PDF de contrato cancelado ou distratado.",
        )
        return redirect("documentos:contratos_gerados")

    libreoffice = encontrar_libreoffice()

    if not libreoffice:
        messages.error(
            request,
            "LibreOffice não encontrado. Instale o LibreOffice ou verifique se o comando libreoffice/soffice está disponível no PATH.",
        )
        return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)

    try:
        documento = montar_documento_contrato_word(contrato)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)

    nome_base = (
        f"Contrato_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    with tempfile.TemporaryDirectory() as pasta_temp:
        pasta_temp_path = Path(pasta_temp)

        caminho_docx = pasta_temp_path / f"{nome_base}.docx"
        caminho_pdf = pasta_temp_path / f"{nome_base}.pdf"

        documento.save(caminho_docx)

        comando = [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pasta_temp_path),
            str(caminho_docx),
        ]

        try:
            resultado = subprocess.run(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            messages.error(
                request,
                "O LibreOffice demorou muito para converter o contrato em PDF.",
            )
            return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)

        if resultado.returncode != 0:
            messages.error(
                request,
                f"Erro ao converter contrato para PDF pelo LibreOffice: {resultado.stderr or resultado.stdout}",
            )
            return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)

        if not caminho_pdf.exists():
            arquivos_pdf = list(pasta_temp_path.glob("*.pdf"))

            if arquivos_pdf:
                caminho_pdf = arquivos_pdf[0]
            else:
                messages.error(
                    request,
                    "O LibreOffice não gerou o arquivo PDF esperado.",
                )
                return redirect("documentos:contratos_pregao", pregao_id=contrato.pregao.id)

        pdf_bytes = caminho_pdf.read_bytes()

    response = HttpResponse(
        pdf_bytes,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_base}.pdf"'

    return response

def contratos_gerados(request):
    contratos = (
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        )
        .all()
        .order_by("-criado_em")
    )

    contratos = aplicar_restricao_escola_contratos(request, contratos)

    pregao_id = request.GET.get("pregao")
    escola_id = request.GET.get("escola")
    fornecedor_id = request.GET.get("fornecedor")
    status = request.GET.get("status")
    data_inicio = request.GET.get("data_inicio")
    data_fim = request.GET.get("data_fim")

    if pregao_id:
        contratos = contratos.filter(pregao_id=pregao_id)

    # Para Consulta/Escola, não aceita trocar escola pelo parâmetro da URL.
    if escola_id and not usuario_eh_consulta_escola(request):
        contratos = contratos.filter(escola_id=escola_id)

    if fornecedor_id:
        contratos = contratos.filter(fornecedor_id=fornecedor_id)

    if status:
        contratos = contratos.filter(status=status)

    if data_inicio:
        contratos = contratos.filter(criado_em__date__gte=data_inicio)

    if data_fim:
        contratos = contratos.filter(criado_em__date__lte=data_fim)

    pregoes = Pregao.objects.all().order_by("-ano", "-numero")
    pregoes = aplicar_restricao_escola_pregoes(request, pregoes)

    if usuario_eh_consulta_escola(request):
        escola_usuario = escola_vinculada_usuario(request)

        if escola_usuario:
            escolas = Escola.objects.filter(id=escola_usuario.id).select_related("municipio")
            fornecedores = (
                Fornecedor.objects.filter(
                    contratos_gerados__escola=escola_usuario,
                )
                .distinct()
                .order_by("razao_social")
            )
        else:
            escolas = Escola.objects.none()
            fornecedores = Fornecedor.objects.none()
    elif pregao_id:
        pregao_filtro = Pregao.objects.filter(id=pregao_id).first()

        if pregao_filtro:
            municipios_ids = pregao_filtro.municipios.values_list("id", flat=True)

            escolas = (
                Escola.objects.filter(
                    ativo=True,
                    municipio_id__in=municipios_ids,
                )
                .select_related("municipio")
                .order_by("nome_escola")
            )

            fornecedores = (
                pregao_filtro.fornecedores.filter(ativo=True)
                .order_by("razao_social")
            )
        else:
            escolas = Escola.objects.none()
            fornecedores = Fornecedor.objects.none()
    else:
        escolas = Escola.objects.filter(ativo=True).select_related("municipio").order_by("nome_escola")
        fornecedores = Fornecedor.objects.filter(ativo=True).order_by("razao_social")

    total_contratos = contratos.count()

    valor_total = Decimal("0")

    for contrato in contratos:
        valor_total += contrato.valor_total or Decimal("0")

    return render(
        request,
        "documentos/contratos_gerados.html",
        {
            "contratos": contratos,
            "pregoes": pregoes,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "status_choices": ContratoGerado.STATUS_CHOICES,
            "total_contratos": total_contratos,
            "valor_total": valor_total,
            "filtros": {
                "pregao": pregao_id,
                "escola": escola_vinculada_usuario(request).id if usuario_eh_consulta_escola(request) and escola_vinculada_usuario(request) else escola_id,
                "fornecedor": fornecedor_id,
                "status": status,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
            },
        },
    )

def cancelar_contrato(request, contrato_id):
    contrato = get_object_or_404(
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "fornecedor",
        ),
        id=contrato_id,
    )

    if not usuario_pode_acessar_contrato(request, contrato):
        return redirect("documentos:contratos_gerados")


    if request.method != "POST":
        messages.error(request, "Ação inválida para cancelamento de contrato.")
        return redirect("documentos:contratos_gerados")

    if contrato.status != ContratoGerado.STATUS_GERADO:
        messages.error(
            request,
            "Somente contratos com status Gerado podem ser cancelados.",
        )
        return redirect("documentos:contratos_gerados")

    contrato.status = ContratoGerado.STATUS_CANCELADO
    contrato.save(update_fields=["status", "atualizado_em"])

    messages.success(
        request,
        f"Contrato {contrato.numero_contrato} cancelado com sucesso. "
        "O saldo dos itens voltou a ficar disponível para nova contratação.",
    )

    return redirect("documentos:contratos_gerados")


def registrar_distrato_contrato(request, contrato_id):
    contrato = get_object_or_404(
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        ),
        id=contrato_id,
    )

    if not usuario_pode_acessar_contrato(request, contrato):
        return redirect("documentos:contratos_gerados")


    if contrato.status not in [
        ContratoGerado.STATUS_GERADO,
        ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
    ]:
        messages.error(
            request,
            "Somente contratos com status Gerado ou Parcialmente Distratado podem receber distrato.",
        )
        return redirect("documentos:contratos_gerados")

    itens = (
        ContratoItemGerado.objects.filter(contrato=contrato)
        .select_related("item")
        .order_by("item__nome_item")
    )

    def montar_itens_contexto():
        itens_contexto = []

        for item in itens:
            quantidade_ja_distratada = obter_quantidade_distratada_contrato_item(item)
            saldo_ativo = item.quantidade_contratada - quantidade_ja_distratada

            if saldo_ativo < 0:
                saldo_ativo = Decimal("0")

            itens_contexto.append(
                {
                    "item_contrato": item,
                    "quantidade_ja_distratada": quantidade_ja_distratada,
                    "saldo_ativo": saldo_ativo,
                    "valor_ativo": saldo_ativo * item.valor_unitario,
                }
            )

        return itens_contexto

    itens_contexto = montar_itens_contexto()

    if not any(item["saldo_ativo"] > 0 for item in itens_contexto):
        messages.error(
            request,
            "Este contrato não possui saldo ativo para distratar.",
        )
        return redirect("documentos:contratos_gerados")

    if request.method == "POST":
        data_distrato_texto = request.POST.get("data_distrato", "").strip()
        motivo_distrato = request.POST.get("motivo_distrato", "").strip()
        selecionados = request.POST.getlist("selecionar_item")

        if not data_distrato_texto:
            messages.error(request, "Informe a data do distrato.")
            return render(
                request,
                "documentos/registrar_distrato.html",
                {
                    "contrato": contrato,
                    "itens_contexto": itens_contexto,
                    "data_padrao": timezone.localdate(),
                },
            )

        if not motivo_distrato:
            messages.error(request, "Informe o motivo do distrato.")
            return render(
                request,
                "documentos/registrar_distrato.html",
                {
                    "contrato": contrato,
                    "itens_contexto": itens_contexto,
                    "data_padrao": data_distrato_texto,
                },
            )

        if not selecionados:
            messages.error(request, "Selecione pelo menos um item para distratar.")
            return render(
                request,
                "documentos/registrar_distrato.html",
                {
                    "contrato": contrato,
                    "itens_contexto": itens_contexto,
                    "data_padrao": data_distrato_texto,
                },
            )

        itens_por_id = {
            str(item["item_contrato"].id): item
            for item in itens_contexto
        }

        itens_para_distratar = []
        erros = []

        for item_id in selecionados:
            item_ctx = itens_por_id.get(str(item_id))

            if not item_ctx:
                continue

            item_contrato = item_ctx["item_contrato"]
            saldo_ativo = item_ctx["saldo_ativo"]

            if saldo_ativo <= 0:
                erros.append(
                    f"O item {item_contrato.item.nome_item} não possui saldo ativo para distrato."
                )
                continue

            quantidade_texto = request.POST.get(
                f"quantidade_distratar_{item_contrato.id}",
                "",
            ).strip()

            if not quantidade_texto:
                erros.append(
                    f"Informe a quantidade a distratar do item {item_contrato.item.nome_item}."
                )
                continue

            quantidade_texto = quantidade_texto.replace(",", ".")

            try:
                quantidade_distratar = Decimal(quantidade_texto)
            except InvalidOperation:
                erros.append(
                    f"A quantidade informada para o item {item_contrato.item.nome_item} é inválida."
                )
                continue

            if quantidade_distratar <= 0:
                erros.append(
                    f"A quantidade a distratar do item {item_contrato.item.nome_item} deve ser maior que zero."
                )
                continue

            if quantidade_distratar > saldo_ativo:
                erros.append(
                    f"A quantidade a distratar do item {item_contrato.item.nome_item} ultrapassa o saldo ativo. "
                    f"Saldo ativo: {saldo_ativo}."
                )
                continue

            itens_para_distratar.append(
                {
                    "item_contrato": item_contrato,
                    "quantidade_distratar": quantidade_distratar,
                    "valor_unitario": item_contrato.valor_unitario,
                    "valor_total": quantidade_distratar * item_contrato.valor_unitario,
                    "saldo_ativo_antes": saldo_ativo,
                }
            )

        if erros:
            for erro in erros:
                messages.error(request, erro)

            return render(
                request,
                "documentos/registrar_distrato.html",
                {
                    "contrato": contrato,
                    "itens_contexto": itens_contexto,
                    "data_padrao": data_distrato_texto,
                },
            )

        if not itens_para_distratar:
            messages.error(request, "Nenhum item válido foi selecionado para distrato.")
            return render(
                request,
                "documentos/registrar_distrato.html",
                {
                    "contrato": contrato,
                    "itens_contexto": itens_contexto,
                    "data_padrao": data_distrato_texto,
                },
            )

        valor_total_distrato = sum(
            item["valor_total"] for item in itens_para_distratar
        )

        itens_ativos_antes = [
            item for item in itens_contexto
            if item["saldo_ativo"] > 0
        ]

        distrato_total_no_registro = (
            len(itens_para_distratar) == len(itens_ativos_antes)
            and all(
                item["quantidade_distratar"] == item["saldo_ativo_antes"]
                for item in itens_para_distratar
            )
        )

        distrato = DistratoContrato.objects.create(
            contrato=contrato,
            data_distrato=data_distrato_texto,
            motivo_distrato=motivo_distrato,
            tipo=(
                DistratoContrato.TIPO_TOTAL
                if distrato_total_no_registro
                else DistratoContrato.TIPO_PARCIAL
            ),
            valor_total=valor_total_distrato,
        )

        for item in itens_para_distratar:
            DistratoContratoItem.objects.create(
                distrato=distrato,
                contrato_item=item["item_contrato"],
                quantidade_distratada=item["quantidade_distratar"],
                valor_unitario=item["valor_unitario"],
                valor_total=item["valor_total"],
            )

        contrato.data_distrato = data_distrato_texto
        contrato.motivo_distrato = motivo_distrato

        if contrato_totalmente_distratado(contrato):
            contrato.status = ContratoGerado.STATUS_DISTRATADO
        else:
            contrato.status = ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO

        contrato.save(
            update_fields=[
                "status",
                "data_distrato",
                "motivo_distrato",
                "atualizado_em",
            ]
        )

        messages.success(
            request,
            f"Distrato registrado com sucesso para o contrato {contrato.numero_contrato}. "
            "A quantidade distratada voltou a ficar disponível para contratação futura.",
        )

        return redirect("documentos:contratos_gerados")

    return render(
        request,
        "documentos/registrar_distrato.html",
        {
            "contrato": contrato,
            "itens_contexto": itens_contexto,
            "data_padrao": timezone.localdate(),
        },
    )



def obter_valor_unitario_atual_contrato_item(contrato_item):
    """
    Retorna o valor unitário vigente do item do contrato.
    Se já houver realinhamento registrado, usa o último valor realinhado.
    Caso contrário, usa o valor unitário original do contrato.
    """
    ultimo_item_realinhado = (
        RealinhamentoPrecoItem.objects.filter(
            contrato_item=contrato_item,
            realinhamento__status=RealinhamentoPreco.STATUS_REGISTRADO,
        )
        .select_related("realinhamento")
        .order_by("-realinhamento__data_realinhamento", "-realinhamento__criado_em", "-id")
        .first()
    )

    if ultimo_item_realinhado:
        return ultimo_item_realinhado.valor_unitario_novo

    return contrato_item.valor_unitario



def obter_marca_atual_contrato_item(contrato_item):
    """
    Retorna a marca atual do item considerando a última troca de marca registrada.
    Se não houver troca, retorna a marca original do item do contrato.
    """
    ultima_troca = (
        TrocaMarcaItem.objects.filter(
            contrato_item=contrato_item,
            troca_marca__status=TrocaMarca.STATUS_REGISTRADO,
        )
        .select_related("troca_marca")
        .order_by("-troca_marca__data_troca_marca", "-troca_marca__criado_em", "-criado_em")
        .first()
    )

    if ultima_troca:
        return ultima_troca.marca_nova

    return contrato_item.marca or ""

def realinhamento_precos(request):
    contratos_base = (
        ContratoGerado.objects.filter(
            status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ]
        )
        .select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        )
        .order_by("-criado_em")
    )

    pregoes = Pregao.objects.filter(
        contratos_gerados__status__in=[
            ContratoGerado.STATUS_GERADO,
            ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
        ]
    ).distinct().order_by("-ano", "-numero")

    escolas = Escola.objects.filter(
        contratos_gerados__status__in=[
            ContratoGerado.STATUS_GERADO,
            ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
        ]
    ).distinct().select_related("municipio").order_by("nome_escola")

    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__status__in=[
            ContratoGerado.STATUS_GERADO,
            ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
        ]
    ).distinct().order_by("razao_social")

    filtros = {
        "pregao": request.GET.get("pregao", ""),
        "escola": request.GET.get("escola", ""),
        "fornecedor": request.GET.get("fornecedor", ""),
        "contrato": request.GET.get("contrato", "") or request.POST.get("contrato", ""),
    }

    if filtros["pregao"]:
        contratos_base = contratos_base.filter(pregao_id=filtros["pregao"])
        escolas = escolas.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()
        fornecedores = fornecedores.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()

    if filtros["escola"]:
        contratos_base = contratos_base.filter(escola_id=filtros["escola"])
        fornecedores = fornecedores.filter(contratos_gerados__escola_id=filtros["escola"]).distinct()

    if filtros["fornecedor"]:
        contratos_base = contratos_base.filter(fornecedor_id=filtros["fornecedor"])

    contrato = None
    itens_contexto = []

    if filtros["contrato"]:
        contrato = get_object_or_404(
            ContratoGerado.objects.select_related(
                "pregao",
                "escola",
                "escola__municipio",
                "fornecedor",
            ),
            id=filtros["contrato"],
            status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ],
        )

        itens = (
            ContratoItemGerado.objects.filter(contrato=contrato)
            .select_related("item")
            .order_by("item__nome_item")
        )

        for item in itens:
            quantidade_distratada = obter_quantidade_distratada_contrato_item(item)
            quantidade_ativa = obter_saldo_ativo_contrato_item(item)
            valor_atual = obter_valor_unitario_atual_contrato_item(item)

            if quantidade_ativa <= 0:
                continue

            itens_contexto.append(
                {
                    "item_contrato": item,
                    "quantidade_contratada": item.quantidade_contratada,
                    "quantidade_distratada": quantidade_distratada,
                    "quantidade_ativa": quantidade_ativa,
                    "valor_unitario_original": item.valor_unitario,
                    "valor_unitario_atual": valor_atual,
                    "valor_total_atual": quantidade_ativa * valor_atual,
                }
            )

    if request.method == "POST":
        if not contrato:
            messages.error(request, "Selecione um contrato válido para registrar o realinhamento.")
            return redirect("documentos:realinhamento_precos")

        data_realinhamento_texto = request.POST.get("data_realinhamento", "").strip()
        justificativa = request.POST.get("justificativa", "").strip()
        selecionados = request.POST.getlist("selecionar_item")

        if not data_realinhamento_texto:
            messages.error(request, "Informe a data de vigência do realinhamento.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        if not justificativa:
            messages.error(request, "Informe a justificativa do realinhamento.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        if not selecionados:
            messages.error(request, "Selecione pelo menos um item para realinhar.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        itens_por_id = {
            str(item["item_contrato"].id): item
            for item in itens_contexto
        }

        itens_para_salvar = []
        valor_total_anterior = Decimal("0")
        valor_total_novo = Decimal("0")
        erros = []

        for contrato_item_id in selecionados:
            item_contexto = itens_por_id.get(str(contrato_item_id))

            if not item_contexto:
                erros.append("Um dos itens selecionados não está disponível para realinhamento.")
                continue

            novo_valor_texto = request.POST.get(
                f"valor_novo_{contrato_item_id}",
                "",
            ).strip()

            if not novo_valor_texto:
                erros.append(
                    f"Informe o novo valor unitário do item {item_contexto['item_contrato'].item.nome_item}."
                )
                continue

            if "," in novo_valor_texto:
                novo_valor_texto = novo_valor_texto.replace(".", "").replace(",", ".")

            try:
                valor_unitario_novo = Decimal(novo_valor_texto)
            except InvalidOperation:
                erros.append(
                    f"Valor unitário novo inválido para o item {item_contexto['item_contrato'].item.nome_item}."
                )
                continue

            if valor_unitario_novo <= 0:
                erros.append(
                    f"O novo valor unitário do item {item_contexto['item_contrato'].item.nome_item} deve ser maior que zero."
                )
                continue

            quantidade_ativa = item_contexto["quantidade_ativa"]
            valor_unitario_anterior = item_contexto["valor_unitario_atual"]
            total_anterior_item = quantidade_ativa * valor_unitario_anterior
            total_novo_item = quantidade_ativa * valor_unitario_novo
            diferenca_item = total_novo_item - total_anterior_item

            itens_para_salvar.append(
                {
                    "contrato_item": item_contexto["item_contrato"],
                    "quantidade_ativa": quantidade_ativa,
                    "valor_unitario_anterior": valor_unitario_anterior,
                    "valor_unitario_novo": valor_unitario_novo,
                    "valor_total_anterior": total_anterior_item,
                    "valor_total_novo": total_novo_item,
                    "diferenca_valor": diferenca_item,
                }
            )

            valor_total_anterior += total_anterior_item
            valor_total_novo += total_novo_item

        if erros:
            for erro in erros:
                messages.error(request, erro)
            return redirect(f"{request.path}?contrato={contrato.id}")

        if not itens_para_salvar:
            messages.error(request, "Nenhum item válido foi encontrado para realinhamento.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        realinhamento = RealinhamentoPreco.objects.create(
            contrato=contrato,
            data_realinhamento=data_realinhamento_texto,
            justificativa=justificativa,
            valor_total_anterior=valor_total_anterior,
            valor_total_novo=valor_total_novo,
            diferenca_total=valor_total_novo - valor_total_anterior,
            status=RealinhamentoPreco.STATUS_REGISTRADO,
        )

        for item in itens_para_salvar:
            RealinhamentoPrecoItem.objects.create(
                realinhamento=realinhamento,
                contrato_item=item["contrato_item"],
                quantidade_ativa=item["quantidade_ativa"],
                valor_unitario_anterior=item["valor_unitario_anterior"],
                valor_unitario_novo=item["valor_unitario_novo"],
                valor_total_anterior=item["valor_total_anterior"],
                valor_total_novo=item["valor_total_novo"],
                diferenca_valor=item["diferenca_valor"],
            )

        messages.success(
            request,
            f"Realinhamento de preços registrado com sucesso para o contrato {contrato.numero_contrato}.",
        )

        return redirect(f"{request.path}?contrato={contrato.id}")

    realinhamentos = (
        RealinhamentoPreco.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__fornecedor",
        )
        .prefetch_related("itens", "itens__contrato_item", "itens__contrato_item__item")
        .order_by("-data_realinhamento", "-criado_em")
    )

    if contrato:
        realinhamentos = realinhamentos.filter(contrato=contrato)

    return render(
        request,
        "documentos/realinhamento_precos.html",
        {
            "pregoes": pregoes,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "contratos": contratos_base,
            "contrato": contrato,
            "itens_contexto": itens_contexto,
            "realinhamentos": realinhamentos[:50],
            "filtros": filtros,
            "data_padrao": timezone.localdate(),
        },
    )



def inserir_tabela_termo_aditivo_no_documento(documento, realinhamento):
    """
    Insere a tabela dos itens realinhados no modelo do termo aditivo.
    O modelo original possui uma tabela de exemplo; removemos as tabelas
    do modelo e inserimos uma nova tabela com os dados reais.
    """
    dados_tabela = [
        [
            "Item",
            "Unidade",
            "Marca",
            "Qtd. Ativa",
            "Valor Anterior",
            "Novo Valor",
            "Diferença",
        ]
    ]

    for item in realinhamento.itens.all():
        contrato_item = item.contrato_item
        dados_tabela.append(
            [
                contrato_item.item.nome_item,
                contrato_item.unidade,
                contrato_item.marca,
                formatar_decimal_br(item.quantidade_ativa),
                formatar_moeda_br(item.valor_unitario_anterior),
                formatar_moeda_br(item.valor_unitario_novo),
                formatar_moeda_br(item.diferenca_valor),
            ]
        )

    remover_tabelas_documento(documento)

    tabela = documento.add_table(rows=0, cols=7)

    try:
        tabela.style = "Table Grid"
    except KeyError:
        pass

    tabela.alignment = WD_TABLE_ALIGNMENT.CENTER
    aplicar_bordas_tabela(tabela)

    larguras = [
        2100,  # Item
        1000,  # Unidade
        1300,  # Marca
        1000,  # Qtd. Ativa
        1200,  # Valor Anterior
        1200,  # Novo Valor
        1200,  # Diferença
    ]

    for indice_linha, linha_dados in enumerate(dados_tabela):
        linha = tabela.add_row()

        for indice_coluna, valor in enumerate(linha_dados):
            celula = linha.cells[indice_coluna]
            celula.text = str(valor)
            definir_largura_coluna(celula, larguras[indice_coluna])

            if indice_linha == 0:
                formatar_celula_tabela(
                    celula,
                    negrito=True,
                    fundo="D9D9D9",
                    alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
                    tamanho_fonte=8,
                )
            else:
                alinhamento = WD_ALIGN_PARAGRAPH.LEFT if indice_coluna == 0 else WD_ALIGN_PARAGRAPH.CENTER
                formatar_celula_tabela(
                    celula,
                    negrito=False,
                    fundo=None,
                    alinhamento=alinhamento,
                    tamanho_fonte=8,
                )

    # Tenta inserir a tabela logo após a cláusula primeira; se não encontrar,
    # insere no final do documento.
    paragrafo_alvo = None

    for paragrafo in documento.paragraphs:
        texto = paragrafo.text or ""
        if "CLÁUSULA PRIMERIA" in texto or "CLÁUSULA PRIMEIRA" in texto:
            paragrafo_alvo = paragrafo
            break

    if paragrafo_alvo:
        paragrafo_alvo._p.addnext(tabela._tbl)
    else:
        documento.add_paragraph("Itens realinhados")
        documento.paragraphs[-1]._p.addnext(tabela._tbl)


def montar_documento_termo_aditivo_word(realinhamento):
    realinhamento = RealinhamentoPreco.objects.select_related(
        "contrato",
        "contrato__pregao",
        "contrato__escola",
        "contrato__escola__municipio",
        "contrato__fornecedor",
    ).prefetch_related(
        "itens",
        "itens__contrato_item",
        "itens__contrato_item__item",
    ).get(id=realinhamento.id)

    contrato = realinhamento.contrato

    if contrato.status not in [
        ContratoGerado.STATUS_GERADO,
        ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
    ]:
        raise ValueError("O termo aditivo só pode ser gerado para contratos ativos ou parcialmente distratados.")

    if not realinhamento.numero_termo_aditivo or not realinhamento.data_termo_aditivo:
        raise ValueError("Registre o número e a data do termo aditivo antes de gerar o documento.")

    if not realinhamento.itens.exists():
        raise ValueError("Este realinhamento não possui itens para gerar o termo aditivo.")

    modelo_path = (
        Path(settings.BASE_DIR)
        / "modelos"
        / "aditivos"
        / "modelo_termo_aditivo_valor.docx"
    )

    if not modelo_path.exists():
        raise FileNotFoundError(
            "Modelo de termo aditivo não encontrado em modelos/aditivos/modelo_termo_aditivo_valor.docx"
        )

    documento = Document(modelo_path)
    corrigir_margens_documento(documento)

    pregao = contrato.pregao
    escola = contrato.escola
    fornecedor = contrato.fornecedor
    municipio = escola.municipio

    numero_pregao = f"{pregao.numero}/{pregao.ano}"
    numero_contrato = contrato.numero_contrato or f"{contrato.id:04d}/{pregao.ano}"
    numero_termo = realinhamento.numero_termo_aditivo

    municipio_uf = ""
    if municipio:
        municipio_uf = f"{municipio.nome}/{municipio.uf}" if municipio.uf else municipio.nome

    endereco_escola_partes = []
    if escola.endereco:
        endereco_escola_partes.append(escola.endereco)
    if escola.numero:
        endereco_escola_partes.append(f"n.º {escola.numero}")
    if escola.bairro:
        endereco_escola_partes.append(escola.bairro)
    if municipio_uf:
        endereco_escola_partes.append(municipio_uf)
    endereco_escola = ", ".join(endereco_escola_partes)

    data_vigencia = data_por_extenso(realinhamento.data_realinhamento)
    data_termo = data_por_extenso(realinhamento.data_termo_aditivo)

    substituicoes = {
        "PRIMEIRO TERMO ADITIVO DE VALOR AO CONTRATO Nº_______/2025.": f"TERMO ADITIVO DE VALOR Nº {numero_termo} AO CONTRATO Nº {numero_contrato}.",
        "PREGÃO PRESENCIAL N.º _______/2025": f"PREGÃO PRESENCIAL N.º {numero_pregao}",
        "PREGÃO PRESENCIAL N.º _______/2025 PARA": f"PREGÃO PRESENCIAL N.º {numero_pregao} PARA",
        "ESCOLA ______________": f"ESCOLA {escola.nome_escola or ''}",
        "EMPRESA _________________________": f"EMPRESA {fornecedor.razao_social or ''}",
        "Escola Estadual ________": f"Escola Estadual {escola.nome_escola or ''}",
        "Rua _____________, n.º _____": endereco_escola,
        "____.___/____-__": escola.cnpj or "",
        "__________________ ___________________": escola.presidente_cdce or "",
        "Presidente do Conselho ________": "Presidente do Conselho Deliberativo da Comunidade Escolar",
        "n.º ______ , CPF/MF n.º _______": f"n.º {escola.rg_presidente or ''}, CPF/MF n.º {escola.cpf_presidente or ''}",
        "(empresa/fornecedor) _________": f"(empresa/fornecedor) {fornecedor.razao_social or ''}",
        "com endereço na _____________, n.º____, /UF _______": f"com endereço na {fornecedor.endereco or ''}",
        "CNPJ n.º ________________________": f"CNPJ n.º {fornecedor.cnpj or ''}",
        "Sr. (a) ___________________________": f"Sr. (a) {fornecedor.representante_legal or ''}",
        "nº____________________": f"nº {fornecedor.rg_representante or ''}",
        "CPF/MF sob o nº. ________________________": f"CPF/MF sob o nº. {fornecedor.cpf_representante or ''}",
        "PREGÃO nº. _______/202__": f"PREGÃO nº. {numero_pregao}",
        "data ___/___/2025": f"data {realinhamento.data_realinhamento.strftime('%d/%m/%Y')}",
        "processo nº ___/202__": f"processo nº {numero_pregao}",
        "Local e data: .................": f"{municipio_uf}, {data_termo}.",
        "( Nome do presidente CDCE)": escola.presidente_cdce or "",
        "(CPF do presidente CDCE)": escola.cpf_presidente or "",
        "(Razão Social Fornecedor)": fornecedor.razao_social or "",
        "(CNPJ do fornecedor)": fornecedor.cnpj or "",
        "(nome do representante)": fornecedor.representante_legal or "",
        "(CPF do representante)": fornecedor.cpf_representante or "",
    }

    substituir_placeholders_documento(documento, substituicoes)
    inserir_tabela_termo_aditivo_no_documento(documento, realinhamento)

    return documento


def gerar_termo_aditivo_word(request, realinhamento_id):
    realinhamento = get_object_or_404(
        RealinhamentoPreco.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__fornecedor",
        ),
        id=realinhamento_id,
        status=RealinhamentoPreco.STATUS_REGISTRADO,
    )

    try:
        documento = montar_documento_termo_aditivo_word(realinhamento)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:realinhamento_precos")
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:registrar_termo_aditivo", realinhamento_id=realinhamento.id)

    arquivo_saida = BytesIO()
    documento.save(arquivo_saida)
    arquivo_saida.seek(0)

    contrato = realinhamento.contrato
    nome_arquivo = (
        f"Termo_Aditivo_{realinhamento.numero_termo_aditivo}_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    response = HttpResponse(
        arquivo_saida.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}.docx"'

    return response


def gerar_termo_aditivo_pdf(request, realinhamento_id):
    realinhamento = get_object_or_404(
        RealinhamentoPreco.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__fornecedor",
        ),
        id=realinhamento_id,
        status=RealinhamentoPreco.STATUS_REGISTRADO,
    )

    libreoffice = encontrar_libreoffice()

    if not libreoffice:
        messages.error(
            request,
            "LibreOffice não encontrado. Instale o LibreOffice ou verifique se o comando libreoffice/soffice está disponível no PATH.",
        )
        return redirect("documentos:realinhamento_precos")

    try:
        documento = montar_documento_termo_aditivo_word(realinhamento)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:realinhamento_precos")
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:registrar_termo_aditivo", realinhamento_id=realinhamento.id)

    contrato = realinhamento.contrato
    nome_base = (
        f"Termo_Aditivo_{realinhamento.numero_termo_aditivo}_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    with tempfile.TemporaryDirectory() as pasta_temp:
        pasta_temp_path = Path(pasta_temp)

        caminho_docx = pasta_temp_path / f"{nome_base}.docx"
        caminho_pdf = pasta_temp_path / f"{nome_base}.pdf"

        documento.save(caminho_docx)

        comando = [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pasta_temp_path),
            str(caminho_docx),
        ]

        try:
            resultado = subprocess.run(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            messages.error(
                request,
                "O LibreOffice demorou muito para converter o termo aditivo em PDF.",
            )
            return redirect("documentos:realinhamento_precos")

        if resultado.returncode != 0:
            messages.error(
                request,
                f"Erro ao converter termo aditivo para PDF pelo LibreOffice: {resultado.stderr or resultado.stdout}",
            )
            return redirect("documentos:realinhamento_precos")

        if not caminho_pdf.exists():
            arquivos_pdf = list(pasta_temp_path.glob("*.pdf"))

            if arquivos_pdf:
                caminho_pdf = arquivos_pdf[0]
            else:
                messages.error(
                    request,
                    "O LibreOffice não gerou o arquivo PDF esperado.",
                )
                return redirect("documentos:realinhamento_precos")

        pdf_bytes = caminho_pdf.read_bytes()

    response = HttpResponse(
        pdf_bytes,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_base}.pdf"'

    return response


def troca_marca(request):
    contratos_base = (
        ContratoGerado.objects.filter(
            status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ]
        )
        .select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        )
        .order_by("-criado_em")
    )

    pregoes = Pregao.objects.filter(
        contratos_gerados__status__in=[
            ContratoGerado.STATUS_GERADO,
            ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
        ]
    ).distinct().order_by("-ano", "-numero")

    escolas = Escola.objects.filter(
        contratos_gerados__status__in=[
            ContratoGerado.STATUS_GERADO,
            ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
        ]
    ).distinct().select_related("municipio").order_by("nome_escola")

    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__status__in=[
            ContratoGerado.STATUS_GERADO,
            ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
        ]
    ).distinct().order_by("razao_social")

    filtros = {
        "pregao": request.GET.get("pregao", ""),
        "escola": request.GET.get("escola", ""),
        "fornecedor": request.GET.get("fornecedor", ""),
        "contrato": request.GET.get("contrato", "") or request.POST.get("contrato", ""),
    }

    if filtros["pregao"]:
        contratos_base = contratos_base.filter(pregao_id=filtros["pregao"])
        escolas = escolas.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()
        fornecedores = fornecedores.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()

    if filtros["escola"]:
        contratos_base = contratos_base.filter(escola_id=filtros["escola"])
        fornecedores = fornecedores.filter(contratos_gerados__escola_id=filtros["escola"]).distinct()

    if filtros["fornecedor"]:
        contratos_base = contratos_base.filter(fornecedor_id=filtros["fornecedor"])

    contrato = None
    itens_contexto = []

    if filtros["contrato"]:
        contrato = get_object_or_404(
            ContratoGerado.objects.select_related(
                "pregao",
                "escola",
                "escola__municipio",
                "fornecedor",
            ),
            id=filtros["contrato"],
            status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ],
        )

        itens = (
            ContratoItemGerado.objects.filter(contrato=contrato)
            .select_related("item")
            .order_by("item__nome_item")
        )

        for item in itens:
            quantidade_distratada = obter_quantidade_distratada_contrato_item(item)
            quantidade_ativa = obter_saldo_ativo_contrato_item(item)
            marca_atual = obter_marca_atual_contrato_item(item)

            if quantidade_ativa <= 0:
                continue

            itens_contexto.append(
                {
                    "item_contrato": item,
                    "quantidade_contratada": item.quantidade_contratada,
                    "quantidade_distratada": quantidade_distratada,
                    "quantidade_ativa": quantidade_ativa,
                    "marca_original": item.marca or "",
                    "marca_atual": marca_atual,
                }
            )

    if request.method == "POST":
        if not contrato:
            messages.error(request, "Selecione um contrato válido para registrar a troca de marca.")
            return redirect("documentos:troca_marca")

        data_troca_texto = request.POST.get("data_troca_marca", "").strip()
        justificativa = request.POST.get("justificativa", "").strip()
        selecionados = request.POST.getlist("selecionar_item")

        if not data_troca_texto:
            messages.error(request, "Informe a data de vigência da troca de marca.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        if not justificativa:
            messages.error(request, "Informe a justificativa da troca de marca.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        if not selecionados:
            messages.error(request, "Selecione pelo menos um item para troca de marca.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        itens_por_id = {
            str(item["item_contrato"].id): item
            for item in itens_contexto
        }

        itens_para_salvar = []
        erros = []

        for contrato_item_id in selecionados:
            item_contexto = itens_por_id.get(str(contrato_item_id))

            if not item_contexto:
                erros.append("Um dos itens selecionados não está disponível para troca de marca.")
                continue

            nova_marca = request.POST.get(
                f"marca_nova_{contrato_item_id}",
                "",
            ).strip()

            if not nova_marca:
                erros.append(
                    f"Informe a nova marca do item {item_contexto['item_contrato'].item.nome_item}."
                )
                continue

            marca_anterior = item_contexto["marca_atual"] or item_contexto["marca_original"]

            if nova_marca.strip().lower() == (marca_anterior or "").strip().lower():
                erros.append(
                    f"A nova marca do item {item_contexto['item_contrato'].item.nome_item} é igual à marca atual."
                )
                continue

            itens_para_salvar.append(
                {
                    "contrato_item": item_contexto["item_contrato"],
                    "quantidade_ativa": item_contexto["quantidade_ativa"],
                    "marca_anterior": marca_anterior,
                    "marca_nova": nova_marca,
                }
            )

        if erros:
            for erro in erros:
                messages.error(request, erro)
            return redirect(f"{request.path}?contrato={contrato.id}")

        if not itens_para_salvar:
            messages.error(request, "Nenhum item válido foi encontrado para troca de marca.")
            return redirect(f"{request.path}?contrato={contrato.id}")

        troca = TrocaMarca.objects.create(
            contrato=contrato,
            data_troca_marca=data_troca_texto,
            justificativa=justificativa,
            status=TrocaMarca.STATUS_REGISTRADO,
        )

        for item in itens_para_salvar:
            TrocaMarcaItem.objects.create(
                troca_marca=troca,
                contrato_item=item["contrato_item"],
                quantidade_ativa=item["quantidade_ativa"],
                marca_anterior=item["marca_anterior"],
                marca_nova=item["marca_nova"],
            )

        messages.success(
            request,
            f"Troca de marca registrada com sucesso para o contrato {contrato.numero_contrato}.",
        )

        return redirect(f"{request.path}?contrato={contrato.id}")

    trocas_marca = (
        TrocaMarca.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__fornecedor",
        )
        .prefetch_related("itens", "itens__contrato_item", "itens__contrato_item__item")
        .order_by("-data_troca_marca", "-criado_em")
    )

    if contrato:
        trocas_marca = trocas_marca.filter(contrato=contrato)

    return render(
        request,
        "documentos/troca_marca.html",
        {
            "pregoes": pregoes,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "contratos": contratos_base,
            "contrato": contrato,
            "itens_contexto": itens_contexto,
            "trocas_marca": trocas_marca[:50],
            "filtros": filtros,
            "data_padrao": timezone.localdate(),
        },
    )


def registrar_termo_aditivo_marca(request, troca_id):
    troca = get_object_or_404(
        TrocaMarca.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__escola__municipio",
            "contrato__fornecedor",
        ).prefetch_related(
            "itens",
            "itens__contrato_item",
            "itens__contrato_item__item",
        ),
        id=troca_id,
        status=TrocaMarca.STATUS_REGISTRADO,
    )

    contrato = troca.contrato

    if contrato.status not in [
        ContratoGerado.STATUS_GERADO,
        ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
    ]:
        messages.error(
            request,
            "Só é possível registrar termo aditivo de marca para contratos ativos ou parcialmente distratados.",
        )
        return redirect("documentos:troca_marca")

    numero_padrao = troca.numero_termo_aditivo or f"{troca.id:04d}/{contrato.pregao.ano}"
    data_padrao = troca.data_termo_aditivo or timezone.localdate()

    if request.method == "POST":
        numero_termo = request.POST.get("numero_termo_aditivo", "").strip()
        data_termo = request.POST.get("data_termo_aditivo", "").strip()
        observacoes = request.POST.get("observacoes_termo_aditivo", "").strip()

        if not numero_termo:
            messages.error(request, "Informe o número do termo aditivo.")
            return redirect("documentos:registrar_termo_aditivo_marca", troca_id=troca.id)

        if not data_termo:
            messages.error(request, "Informe a data do termo aditivo.")
            return redirect("documentos:registrar_termo_aditivo_marca", troca_id=troca.id)

        troca.numero_termo_aditivo = numero_termo
        troca.data_termo_aditivo = data_termo
        troca.observacoes_termo_aditivo = observacoes
        troca.save(
            update_fields=[
                "numero_termo_aditivo",
                "data_termo_aditivo",
                "observacoes_termo_aditivo",
                "atualizado_em",
            ]
        )

        messages.success(
            request,
            f"Termo aditivo de marca {numero_termo} registrado com sucesso.",
        )

        return redirect(f"{reverse('documentos:troca_marca')}?contrato={contrato.id}")

    return render(
        request,
        "documentos/registrar_termo_aditivo_marca.html",
        {
            "troca": troca,
            "contrato": contrato,
            "itens": troca.itens.all(),
            "numero_padrao": numero_padrao,
            "data_padrao": data_padrao,
        },
    )


def inserir_tabela_termo_aditivo_marca_no_documento(documento, troca):
    """
    Insere a tabela dos itens com troca de marca no modelo.

    Colunas solicitadas:
    - Item
    - Marca Anterior
    - Nova Marca
    """
    dados_tabela = [
        [
            "Item",
            "Marca Anterior",
            "Nova Marca",
        ]
    ]

    for item in troca.itens.all():
        contrato_item = item.contrato_item
        dados_tabela.append(
            [
                contrato_item.item.nome_item,
                item.marca_anterior,
                item.marca_nova,
            ]
        )

    remover_tabelas_documento(documento)

    tabela = documento.add_table(rows=0, cols=3)

    try:
        tabela.style = "Table Grid"
    except KeyError:
        pass

    tabela.alignment = WD_TABLE_ALIGNMENT.CENTER
    aplicar_bordas_tabela(tabela)

    larguras = [
        3400,  # Item
        2400,  # Marca anterior
        2400,  # Nova marca
    ]

    for indice_linha, linha_dados in enumerate(dados_tabela):
        linha = tabela.add_row()

        for indice_coluna, valor in enumerate(linha_dados):
            celula = linha.cells[indice_coluna]
            celula.text = str(valor)
            definir_largura_coluna(celula, larguras[indice_coluna])

            if indice_linha == 0:
                formatar_celula_tabela(
                    celula,
                    negrito=True,
                    fundo="D9D9D9",
                    alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
                    tamanho_fonte=9,
                )
            else:
                alinhamento = WD_ALIGN_PARAGRAPH.LEFT if indice_coluna == 0 else WD_ALIGN_PARAGRAPH.CENTER
                formatar_celula_tabela(
                    celula,
                    negrito=False,
                    fundo=None,
                    alinhamento=alinhamento,
                    tamanho_fonte=9,
                )

    paragrafo_alvo = None

    for paragrafo in documento.paragraphs:
        texto = paragrafo.text or ""
        if "CLÁUSULA PRIMERIA" in texto or "CLÁUSULA PRIMEIRA" in texto:
            paragrafo_alvo = paragrafo
            break

    if paragrafo_alvo:
        paragrafo_alvo._p.addnext(tabela._tbl)
    else:
        documento.add_paragraph("Itens com troca de marca")
        documento.paragraphs[-1]._p.addnext(tabela._tbl)


def montar_documento_termo_aditivo_marca_word(troca):
    troca = TrocaMarca.objects.select_related(
        "contrato",
        "contrato__pregao",
        "contrato__escola",
        "contrato__escola__municipio",
        "contrato__fornecedor",
    ).prefetch_related(
        "itens",
        "itens__contrato_item",
        "itens__contrato_item__item",
    ).get(id=troca.id)

    contrato = troca.contrato

    if contrato.status not in [
        ContratoGerado.STATUS_GERADO,
        ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
    ]:
        raise ValueError("O termo aditivo de marca só pode ser gerado para contratos ativos ou parcialmente distratados.")

    if not troca.numero_termo_aditivo or not troca.data_termo_aditivo:
        raise ValueError("Registre o número e a data do termo aditivo de marca antes de gerar o documento.")

    if not troca.itens.exists():
        raise ValueError("Esta troca de marca não possui itens para gerar o termo aditivo.")

    modelo_path = (
        Path(settings.BASE_DIR)
        / "modelos"
        / "aditivos"
        / "modelo_termo_aditivo_marca.docx"
    )

    if not modelo_path.exists():
        raise FileNotFoundError(
            "Modelo de termo aditivo de marca não encontrado em modelos/aditivos/modelo_termo_aditivo_marca.docx"
        )

    documento = Document(modelo_path)
    corrigir_margens_documento(documento)

    pregao = contrato.pregao
    escola = contrato.escola
    fornecedor = contrato.fornecedor
    municipio = escola.municipio

    numero_pregao = f"{pregao.numero}/{pregao.ano}"
    numero_contrato = contrato.numero_contrato or f"{contrato.id:04d}/{pregao.ano}"
    numero_termo = troca.numero_termo_aditivo

    municipio_uf = ""
    if municipio:
        municipio_uf = f"{municipio.nome}/{municipio.uf}" if municipio.uf else municipio.nome

    endereco_escola_partes = []
    if escola.endereco:
        endereco_escola_partes.append(escola.endereco)
    if escola.numero:
        endereco_escola_partes.append(f"n.º {escola.numero}")
    if escola.bairro:
        endereco_escola_partes.append(escola.bairro)
    if municipio_uf:
        endereco_escola_partes.append(municipio_uf)
    endereco_escola = ", ".join(endereco_escola_partes)

    data_termo = data_por_extenso(troca.data_termo_aditivo)

    substituicoes = {
        "PRIMEIRO TERMO ADITIVO AO CONTRATO Nº_______/2025.": f"TERMO ADITIVO DE MARCA Nº {numero_termo} AO CONTRATO Nº {numero_contrato}.",
        "PREGÃO PRESENCIAL N.º _______/2025": f"PREGÃO PRESENCIAL N.º {numero_pregao}",
        "PREGÃO PRESENCIAL N.º _______/2025 PARA": f"PREGÃO PRESENCIAL N.º {numero_pregao} PARA",
        "ESCOLA ______________": f"ESCOLA {escola.nome_escola or ''}",
        "EMPRESA _________________________": f"EMPRESA {fornecedor.razao_social or ''}",
        "Escola Estadual ________": f"Escola Estadual {escola.nome_escola or ''}",
        "Rua _____________, n.º _____": endereco_escola,
        "____.___/____-__": escola.cnpj or "",
        "__________________ ___________________": escola.presidente_cdce or "",
        "Presidente do Conselho ________": "Presidente do Conselho Deliberativo da Comunidade Escolar",
        "n.º ______ , CPF/MF n.º _______": f"n.º {escola.rg_presidente or ''}, CPF/MF n.º {escola.cpf_presidente or ''}",
        "(empresa/fornecedor) _________": f"(empresa/fornecedor) {fornecedor.razao_social or ''}",
        "com endereço na _____________, n.º____, /UF _______": f"com endereço na {fornecedor.endereco or ''}",
        "CNPJ n.º ________________________": f"CNPJ n.º {fornecedor.cnpj or ''}",
        "Sr. (a) ___________________________": f"Sr. (a) {fornecedor.representante_legal or ''}",
        "nº____________________": f"nº {fornecedor.rg_representante or ''}",
        "CPF/MF sob o nº. ________________________": f"CPF/MF sob o nº. {fornecedor.cpf_representante or ''}",
        "PREGÃO nº. _______/202__": f"PREGÃO nº. {numero_pregao}",
        "data ___/___/2025": f"data {troca.data_troca_marca.strftime('%d/%m/%Y')}",
        "processo nº ___/202__": f"processo nº {numero_pregao}",
        "Local e data: .................": f"{municipio_uf}, {data_termo}.",
        "( Nome do presidente CDCE)": escola.presidente_cdce or "",
        "(CPF do presidente CDCE)": escola.cpf_presidente or "",
        "(Razão Social Fornecedor)": fornecedor.razao_social or "",
        "(CNPJ do fornecedor)": fornecedor.cnpj or "",
        "(nome do representante)": fornecedor.representante_legal or "",
        "(CPF do representante)": fornecedor.cpf_representante or "",
    }

    substituir_placeholders_documento(documento, substituicoes)
    inserir_tabela_termo_aditivo_marca_no_documento(documento, troca)

    return documento


def gerar_termo_aditivo_marca_word(request, troca_id):
    troca = get_object_or_404(
        TrocaMarca.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__fornecedor",
        ),
        id=troca_id,
        status=TrocaMarca.STATUS_REGISTRADO,
    )

    try:
        documento = montar_documento_termo_aditivo_marca_word(troca)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:troca_marca")
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:registrar_termo_aditivo_marca", troca_id=troca.id)

    arquivo_saida = BytesIO()
    documento.save(arquivo_saida)
    arquivo_saida.seek(0)

    contrato = troca.contrato
    nome_arquivo = (
        f"Termo_Aditivo_Marca_{troca.numero_termo_aditivo}_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    response = HttpResponse(
        arquivo_saida.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}.docx"'

    return response


def gerar_termo_aditivo_marca_pdf(request, troca_id):
    troca = get_object_or_404(
        TrocaMarca.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__fornecedor",
        ),
        id=troca_id,
        status=TrocaMarca.STATUS_REGISTRADO,
    )

    libreoffice = encontrar_libreoffice()

    if not libreoffice:
        messages.error(
            request,
            "LibreOffice não encontrado. Instale o LibreOffice ou verifique se o comando libreoffice/soffice está disponível no PATH.",
        )
        return redirect("documentos:troca_marca")

    try:
        documento = montar_documento_termo_aditivo_marca_word(troca)
    except FileNotFoundError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:troca_marca")
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("documentos:registrar_termo_aditivo_marca", troca_id=troca.id)

    contrato = troca.contrato
    nome_base = (
        f"Termo_Aditivo_Marca_{troca.numero_termo_aditivo}_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        .replace("/", "-")
        .replace("\\", "-")
        .replace(":", "-")
    )

    with tempfile.TemporaryDirectory() as pasta_temp:
        pasta_temp_path = Path(pasta_temp)

        caminho_docx = pasta_temp_path / f"{nome_base}.docx"
        caminho_pdf = pasta_temp_path / f"{nome_base}.pdf"

        documento.save(caminho_docx)

        comando = [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pasta_temp_path),
            str(caminho_docx),
        ]

        try:
            resultado = subprocess.run(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            messages.error(
                request,
                "O LibreOffice demorou muito para converter o termo aditivo de marca em PDF.",
            )
            return redirect("documentos:troca_marca")

        if resultado.returncode != 0:
            messages.error(
                request,
                f"Erro ao converter termo aditivo de marca para PDF pelo LibreOffice: {resultado.stderr or resultado.stdout}",
            )
            return redirect("documentos:troca_marca")

        if not caminho_pdf.exists():
            arquivos_pdf = list(pasta_temp_path.glob("*.pdf"))

            if arquivos_pdf:
                caminho_pdf = arquivos_pdf[0]
            else:
                messages.error(
                    request,
                    "O LibreOffice não gerou o arquivo PDF esperado.",
                )
                return redirect("documentos:troca_marca")

        pdf_bytes = caminho_pdf.read_bytes()

    response = HttpResponse(
        pdf_bytes,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_base}.pdf"'

    return response


def documentos_escolas_por_pregao(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    municipios_ids = pregao.municipios.values_list("id", flat=True)

    escolas = (
        Escola.objects.filter(
            ativo=True,
            municipio_id__in=municipios_ids,
        )
        .select_related("municipio")
        .order_by("nome_escola")
    )

    dados = []

    for escola in escolas:
        dados.append(
            {
                "id": escola.id,
                "nome": escola.nome_escola,
                "municipio": escola.municipio.nome if escola.municipio else "",
                "uf": escola.municipio.uf if escola.municipio and escola.municipio.uf else "",
            }
        )

    return JsonResponse({"escolas": dados})


def documentos_fornecedores_por_pregao(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    fornecedores = (
        pregao.fornecedores.filter(ativo=True)
        .order_by("razao_social")
    )

    dados = []

    for fornecedor in fornecedores:
        dados.append(
            {
                "id": fornecedor.id,
                "nome": fornecedor.razao_social,
                "cnpj": fornecedor.cnpj or "",
            }
        )

    return JsonResponse({"fornecedores": dados})



def documentos_contratos_por_filtros(request):
    """
    Retorna contratos ativos/parcialmente distratados conforme os filtros
    selecionados nas telas de Realinhamento de Preços e Troca de Marca.
    """
    contratos = (
        ContratoGerado.objects.filter(
            status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ]
        )
        .select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        )
        .order_by("-criado_em")
    )

    pregao_id = request.GET.get("pregao", "").strip()
    escola_id = request.GET.get("escola", "").strip()
    fornecedor_id = request.GET.get("fornecedor", "").strip()

    if pregao_id:
        contratos = contratos.filter(pregao_id=pregao_id)

    if escola_id:
        contratos = contratos.filter(escola_id=escola_id)

    if fornecedor_id:
        contratos = contratos.filter(fornecedor_id=fornecedor_id)

    contratos = aplicar_restricao_escola_contratos(request, contratos)

    dados = []

    for contrato in contratos[:200]:
        escola_texto = contrato.escola.nome_escola if contrato.escola else ""
        municipio_texto = ""

        if contrato.escola and contrato.escola.municipio:
            municipio_texto = contrato.escola.municipio.nome
            if contrato.escola.municipio.uf:
                municipio_texto += f"/{contrato.escola.municipio.uf}"

        fornecedor_texto = contrato.fornecedor.razao_social if contrato.fornecedor else ""
        pregao_texto = ""

        if contrato.pregao:
            pregao_texto = f"{contrato.pregao.numero}/{contrato.pregao.ano}"

        texto = f"{contrato.numero_contrato} - {escola_texto} - {fornecedor_texto}"

        dados.append(
            {
                "id": contrato.id,
                "numero": contrato.numero_contrato,
                "texto": texto,
                "pregao": pregao_texto,
                "escola": escola_texto,
                "municipio": municipio_texto,
                "fornecedor": fornecedor_texto,
                "status": contrato.get_status_display(),
            }
        )

    return JsonResponse({"contratos": dados})

def registrar_termo_aditivo(request, realinhamento_id):
    realinhamento = get_object_or_404(
        RealinhamentoPreco.objects.select_related(
            "contrato",
            "contrato__pregao",
            "contrato__escola",
            "contrato__escola__municipio",
            "contrato__fornecedor",
        ).prefetch_related(
            "itens",
            "itens__contrato_item",
            "itens__contrato_item__item",
        ),
        id=realinhamento_id,
        status=RealinhamentoPreco.STATUS_REGISTRADO,
    )

    contrato = realinhamento.contrato

    if contrato.status not in [
        ContratoGerado.STATUS_GERADO,
        ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
    ]:
        messages.error(
            request,
            "Só é possível registrar termo aditivo para contratos ativos ou parcialmente distratados.",
        )
        return redirect("documentos:realinhamento_precos")

    numero_padrao = realinhamento.numero_termo_aditivo or f"{realinhamento.id:04d}/{contrato.pregao.ano}"
    data_padrao = realinhamento.data_termo_aditivo or timezone.localdate()

    if request.method == "POST":
        numero_termo = request.POST.get("numero_termo_aditivo", "").strip()
        data_termo = request.POST.get("data_termo_aditivo", "").strip()
        observacoes = request.POST.get("observacoes_termo_aditivo", "").strip()

        if not numero_termo:
            messages.error(request, "Informe o número do termo aditivo.")
            return redirect("documentos:registrar_termo_aditivo", realinhamento_id=realinhamento.id)

        if not data_termo:
            messages.error(request, "Informe a data do termo aditivo.")
            return redirect("documentos:registrar_termo_aditivo", realinhamento_id=realinhamento.id)

        realinhamento.numero_termo_aditivo = numero_termo
        realinhamento.data_termo_aditivo = data_termo
        realinhamento.observacoes_termo_aditivo = observacoes
        realinhamento.save(
            update_fields=[
                "numero_termo_aditivo",
                "data_termo_aditivo",
                "observacoes_termo_aditivo",
                "atualizado_em",
            ]
        )

        messages.success(
            request,
            f"Termo aditivo {numero_termo} registrado com sucesso.",
        )

        return redirect(f"{reverse('documentos:realinhamento_precos')}?contrato={contrato.id}")

    return render(
        request,
        "documentos/registrar_termo_aditivo.html",
        {
            "realinhamento": realinhamento,
            "contrato": contrato,
            "itens": realinhamento.itens.all(),
            "numero_padrao": numero_padrao,
            "data_padrao": data_padrao,
        },
    )

# ============================================================
# DOCUMENTOS DO REALINHAMENTO DE PREÇOS - MODELOS NOVOS
# ============================================================

def caminho_modelo_realinhamento(nome_arquivo):
    return Path(settings.BASE_DIR) / "modelos" / "aditivos" / nome_arquivo


def carregar_realinhamento_completo(realinhamento_id):
    return (
        RealinhamentoPreco.objects.select_related(
            "contrato", "contrato__pregao", "contrato__escola", "contrato__escola__municipio", "contrato__fornecedor",
        )
        .prefetch_related("itens", "itens__contrato_item", "itens__contrato_item__item")
        .get(id=realinhamento_id)
    )


def texto_municipio_escola(escola):
    municipio = getattr(escola, "municipio", None)
    if not municipio:
        return ""
    return f"{municipio.nome}/{municipio.uf}" if getattr(municipio, "uf", "") else municipio.nome or ""


def texto_municipio_nome(escola):
    municipio = getattr(escola, "municipio", None)
    return municipio.nome if municipio else ""


def data_curta_br(data):
    return data.strftime("%d/%m/%Y") if data else ""


def limpar_nome_download(texto):
    return limpar_nome_arquivo(texto).replace(" ", "_")


def substituir_em_todos_elementos(documento, substituicoes):
    substituir_placeholders_documento(documento, substituicoes)


def definir_texto_celula(celula, texto, negrito=False, tamanho=9, alinhamento=WD_ALIGN_PARAGRAPH.CENTER):
    celula.text = str(texto or "")
    celula.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for paragrafo in celula.paragraphs:
        paragrafo.alignment = alinhamento
        for run in paragrafo.runs:
            run.font.name = "Arial"
            run.font.size = Pt(tamanho)
            run.bold = negrito


def dados_linhas_itens_revisao(realinhamento, incluir_cnpj_fornecedor=False):
    contrato = realinhamento.contrato
    fornecedor = contrato.fornecedor
    linhas = []
    for indice, item_realinhado in enumerate(realinhamento.itens.all(), start=1):
        contrato_item = item_realinhado.contrato_item
        fornecedor_texto = fornecedor.razao_social or ""
        if incluir_cnpj_fornecedor and fornecedor.cnpj:
            fornecedor_texto = f"{fornecedor_texto} - {fornecedor.cnpj}"
        linhas.append([
            str(indice), contrato_item.item.nome_item, contrato_item.marca, contrato_item.unidade,
            fornecedor_texto, formatar_moeda_br(item_realinhado.valor_unitario_novo),
        ])
    return linhas


def preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=0, incluir_cnpj_fornecedor=False, municipio_no_cabecalho=False):
    if len(documento.tables) <= tabela_indice:
        return
    tabela = documento.tables[tabela_indice]
    contrato = realinhamento.contrato
    municipio = texto_municipio_nome(contrato.escola)

    indice_cabecalho = 0
    if municipio_no_cabecalho and len(tabela.rows) >= 1:
        for celula in tabela.rows[0].cells:
            if "Município" in celula.text:
                definir_texto_celula(celula, f"Município: {municipio}", negrito=True, tamanho=9)
                break
        indice_cabecalho = 1

    while len(tabela.rows) > indice_cabecalho + 1:
        linha = tabela.rows[-1]
        linha._tr.getparent().remove(linha._tr)

    for linha_dados in dados_linhas_itens_revisao(realinhamento, incluir_cnpj_fornecedor=incluir_cnpj_fornecedor):
        nova_linha = tabela.add_row()
        for indice, valor in enumerate(linha_dados):
            if indice < len(nova_linha.cells):
                alinhamento = WD_ALIGN_PARAGRAPH.LEFT if indice in [1, 4] else WD_ALIGN_PARAGRAPH.CENTER
                definir_texto_celula(nova_linha.cells[indice], valor, tamanho=8, alinhamento=alinhamento)
    aplicar_bordas_tabela(tabela)


def inserir_paragrafos_itens_termo_valor(documento, realinhamento):
    paragrafo_alvo = None
    for paragrafo in documento.paragraphs:
        if "fica aditado o valor dos itens" in (paragrafo.text or "").strip().lower():
            paragrafo_alvo = paragrafo
            break
    if paragrafo_alvo is None:
        paragrafo_alvo = documento.add_paragraph("Fica aditado o valor dos Itens:")
    ultimo_elemento = paragrafo_alvo._p
    contrato = realinhamento.contrato
    municipio = texto_municipio_nome(contrato.escola)
    pregao = contrato.pregao
    for indice, item_realinhado in enumerate(realinhamento.itens.all(), start=1):
        contrato_item = item_realinhado.contrato_item
        texto = (
            f"{indice} - {contrato_item.item.nome_item}, marca {contrato_item.marca}, "
            f"adquirido junto ao Processo Licitatório do Pregão Presencial de n.º {pregao.numero}/{pregao.ano}, "
            f"realizado pela Câmara de Negócios do Município de {municipio}/MT, cujo valor unitário "
            f"({contrato_item.unidade}) do produto passa a ser na quantia de {formatar_moeda_br(item_realinhado.valor_unitario_novo)}."
        )
        novo = documento.add_paragraph()
        novo.style = paragrafo_alvo.style
        novo.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        run = novo.add_run(texto)
        run.font.name = "Arial"
        run.font.size = Pt(11)
        ultimo_elemento.addnext(novo._p)
        ultimo_elemento = novo._p


def montar_substituicoes_realinhamento(realinhamento, dados_extra=None):
    dados_extra = dados_extra or {}
    contrato = realinhamento.contrato
    pregao = contrato.pregao
    escola = contrato.escola
    fornecedor = contrato.fornecedor
    municipio_nome = texto_municipio_nome(escola)
    municipio_uf = texto_municipio_escola(escola)
    ano_curto = str(pregao.ano)[-2:]
    data_base = realinhamento.data_termo_aditivo or realinhamento.data_realinhamento or timezone.localdate()
    numero_pregao = f"{pregao.numero}/{pregao.ano}"
    numero_pregao_curto = f"{pregao.numero}/{ano_curto}"
    numero_contrato = contrato.numero_contrato or f"{contrato.id:04d}/{pregao.ano}"
    produtos_resumo = "; ".join([
        f"{item.contrato_item.item.nome_item} - {formatar_moeda_br(item.valor_unitario_novo)}"
        for item in realinhamento.itens.all()
    ])
    endereco_escola = ", ".join([p for p in [
        escola.endereco or "",
        f"nº {escola.numero}" if escola.numero else "",
        f"Bairro {escola.bairro}" if escola.bairro else "",
        municipio_uf,
    ] if p])
    return {
        "_____/202__": numero_contrato,
        "_____/20__": numero_pregao_curto,
        "___/20__": numero_pregao_curto,
        "____/20__": numero_pregao_curto,
        "____/___": numero_pregao,
        "____/202__": numero_pregao,
        "____/202___": numero_pregao,
        "ESCOLA __________________": f"ESCOLA {escola.nome_escola or ''}",
        "A EMPRESA __________________": f"A EMPRESA {fornecedor.razao_social or ''}",
        "A Escola Estadual ________________________": f"A Escola Estadual {escola.nome_escola or ''}",
        "à Rua _________, nº ________, Bairro _____________": f"à {endereco_escola}",
        "no município de _______- MT": f"no município de {municipio_nome}- MT",
        "inscrita no CNPJ nº. ________________": f"inscrita no CNPJ nº. {escola.cnpj or ''}",
        "representada pelo (a) Senhor (a) _____________": f"representada pelo (a) Senhor (a) {escola.presidente_cdce or ''}",
        "(Presidente) __________": f"(Presidente) {escola.presidente_cdce or ''}",
        "a Empresa\n______________________.": f"a Empresa\n{fornecedor.razao_social or ''}.",
        "inscrita no CNPJ/MF sob o nº. ____________": f"inscrita no CNPJ/MF sob o nº. {fornecedor.cnpj or ''}",
        "com sede social\nna Rua ______________, nº ______, bairro _______": f"com sede social\n{fornecedor.endereco or ''}",
        "representada pelo Senhor _________": f"representada pelo Senhor {fornecedor.representante_legal or ''}",
        "RG nº. _________ SSP/____": f"RG nº. {fornecedor.rg_representante or ''} {fornecedor.orgao_expedidor_representante or ''}",
        "CPF/MF\nsob o nº. _________": f"CPF/MF\nsob o nº. {fornecedor.cpf_representante or ''}",
        "16720/2026/UNGR/SEDUC": dados_extra.get("manifestacao_juridica") or "________/____/UNGR/SEDUC",
        "Contrato nº _______/202__": f"Contrato nº {numero_contrato}",
        "Contrato n° _____/202__": f"Contrato n° {numero_contrato}",
        "Município, data mês e ano.": f"{municipio_uf}, {data_por_extenso(data_base)}.",
        "( Nome do presidente CDCE)": escola.presidente_cdce or "",
        "(CPF do presidente CDCE)": escola.cpf_presidente or "",
        "(Razão Social Fornecedor)": fornecedor.razao_social or "",
        "(CNPJ do fornecedor)": fornecedor.cnpj or "",
        "(nome do representante)": fornecedor.representante_legal or "",
        "(CPF do representante)": fornecedor.cpf_representante or "",
        "___________/MT": f"{municipio_nome}/MT" if municipio_nome else "",
        "_________/MT": f"{municipio_nome}/MT" if municipio_nome else "",
        "(MUNICÍPIO)": municipio_nome or "",
        "(RAZÃO SOCIAL DO FORNECEDOR)": fornecedor.razao_social or "",
        "(CNPJ DO FORNECEDOR)": fornecedor.cnpj or "",
        "solicitado pela empresa\n_______________________, CNPJ:": f"solicitado pela empresa\n{fornecedor.razao_social or ''}, CNPJ:",
        "Contratada: _______________; CNPJ\n_______": f"Contratada: {fornecedor.razao_social or ''}; CNPJ\n{fornecedor.cnpj or ''}",
        "(nome do produto) (novo valor)": produtos_resumo,
        "segundo manifestação jurídico nº\n_________________": f"segundo manifestação jurídico nº\n{dados_extra.get('manifestacao_juridica', '')}",
        "Processo n.º _________": f"Processo n.º {dados_extra.get('numero_processo', '')}",
        "PARECER Nº _____/20__ – REVISÃO DE PREÇO": f"PARECER Nº {dados_extra.get('numero_parecer', '')} – REVISÃO DE PREÇO",
        "PARECER Nº _____/20__": f"PARECER Nº {dados_extra.get('numero_parecer', '')}",
        "Processo Administrativo n° ______________": f"Processo Administrativo n° {dados_extra.get('processo_administrativo') or dados_extra.get('numero_processo') or ''}",
        "Sinop/MT, ____ de _____ de ____": f"Sinop/MT, {data_por_extenso(data_base)}",
        "Sinop/MT, ___ de _______de 20__.": f"Sinop/MT, {data_por_extenso(data_base)}.",
        "_____ dias do mês de ____ de ________ (___/___/____)": data_por_extenso(data_base) + f" ({data_curta_br(data_base)})",
        "____________________, Coordenador": f"{dados_extra.get('nome_lavrador_ata', '')}, Coordenador",
        "Representante legal da DRE\n(nome completo CPF e cargo)": (
            f"Representante legal da DRE\n{dados_extra.get('representante_dre', '')} - CPF: {dados_extra.get('cpf_representante_dre', '')} - {dados_extra.get('cargo_representante_dre', '')}"
        ),
        "CNPJ: ___________": f"CNPJ: {fornecedor.cnpj or ''}",
    }


def montar_documento_termo_aditivo_word(realinhamento):
    realinhamento = carregar_realinhamento_completo(realinhamento.id)
    contrato = realinhamento.contrato
    if contrato.status not in [ContratoGerado.STATUS_GERADO, ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO]:
        raise ValueError("O termo aditivo só pode ser gerado para contratos ativos ou parcialmente distratados.")
    if not realinhamento.numero_termo_aditivo or not realinhamento.data_termo_aditivo:
        raise ValueError("Registre o número e a data do termo aditivo antes de gerar o documento.")
    if not realinhamento.itens.exists():
        raise ValueError("Este realinhamento não possui itens para gerar o termo aditivo.")
    modelo_path = caminho_modelo_realinhamento("modelo_termo_aditivo_valor.docx")
    if not modelo_path.exists():
        raise FileNotFoundError("Modelo de termo aditivo não encontrado em modelos/aditivos/modelo_termo_aditivo_valor.docx")
    documento = Document(modelo_path)
    corrigir_margens_documento(documento)
    substituir_em_todos_elementos(documento, montar_substituicoes_realinhamento(realinhamento))
    inserir_paragrafos_itens_termo_valor(documento, realinhamento)
    return documento


DOCUMENTOS_REALINHAMENTO = {
    "extrato": {"titulo": "Extrato do Termo Aditivo", "modelo": "modelo_extrato_termo_aditivo_valor.docx", "nome": "Extrato_Termo_Aditivo", "campos": [("manifestacao_juridica", "Número da Manifestação Jurídica", "text", True), ("numero_processo", "Número do Processo", "text", True), ("representante_dre", "Representante Legal da DRE", "text", True), ("cpf_representante_dre", "CPF", "text", True), ("cargo_representante_dre", "Cargo", "text", True)]},
    "parecer": {"titulo": "Parecer de Revisão de Preços", "modelo": "modelo_parecer_revisao_precos.docx", "nome": "Parecer_Revisao_Precos", "campos": [("numero_parecer", "Número do Parecer da Revisão de Preços. Ex.: 0005/2025", "text", True), ("processo_administrativo", "Número do Processo Administrativo", "text", True)]},
    "ata": {"titulo": "Ata de Revisão de Preços", "modelo": "modelo_ata_revisao_precos.docx", "nome": "Ata_Revisao_Precos", "campos": [("nome_lavrador_ata", "Nome de quem lavra a ata", "text", True)]},
    "resultado": {"titulo": "Resultado Final da Revisão de Preços", "modelo": "modelo_resultado_final_revisao_precos.docx", "nome": "Resultado_Final_Revisao_Precos", "campos": []},
}


def montar_documento_realinhamento_por_tipo(realinhamento, tipo_documento, dados_extra=None):
    if tipo_documento not in DOCUMENTOS_REALINHAMENTO:
        raise ValueError("Tipo de documento inválido.")
    realinhamento = carregar_realinhamento_completo(realinhamento.id)
    config = DOCUMENTOS_REALINHAMENTO[tipo_documento]
    modelo_path = caminho_modelo_realinhamento(config["modelo"])
    if not modelo_path.exists():
        raise FileNotFoundError(f"Modelo não encontrado em modelos/aditivos/{config['modelo']}")
    documento = Document(modelo_path)
    corrigir_margens_documento(documento)
    substituir_em_todos_elementos(documento, montar_substituicoes_realinhamento(realinhamento, dados_extra))
    if tipo_documento == "parecer":
        contrato = realinhamento.contrato
        if len(documento.tables) >= 1 and len(documento.tables[0].rows) >= 2:
            definir_texto_celula(documento.tables[0].rows[1].cells[0], contrato.fornecedor.razao_social, tamanho=10, alinhamento=WD_ALIGN_PARAGRAPH.LEFT)
            definir_texto_celula(documento.tables[0].rows[1].cells[1], contrato.fornecedor.cnpj, tamanho=10, alinhamento=WD_ALIGN_PARAGRAPH.LEFT)
        preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=1, municipio_no_cabecalho=True)
    elif tipo_documento == "ata":
        preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=0)
    elif tipo_documento == "resultado":
        preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=0, incluir_cnpj_fornecedor=True)
    return documento


def resposta_download_docx(documento, nome_base):
    arquivo_saida = BytesIO(); documento.save(arquivo_saida); arquivo_saida.seek(0)
    response = HttpResponse(arquivo_saida.getvalue(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    response["Content-Disposition"] = f'attachment; filename="{limpar_nome_download(nome_base)}.docx"'
    return response


def resposta_download_pdf(documento, nome_base):
    libreoffice = encontrar_libreoffice()
    if not libreoffice:
        raise FileNotFoundError("LibreOffice não encontrado. Instale o LibreOffice ou verifique se o comando libreoffice/soffice está disponível no PATH.")
    with tempfile.TemporaryDirectory() as pasta_temp:
        pasta_temp_path = Path(pasta_temp)
        caminho_docx = pasta_temp_path / f"{limpar_nome_download(nome_base)}.docx"
        caminho_pdf = pasta_temp_path / f"{limpar_nome_download(nome_base)}.pdf"
        documento.save(caminho_docx)
        resultado = subprocess.run([libreoffice, "--headless", "--convert-to", "pdf", "--outdir", str(pasta_temp_path), str(caminho_docx)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if resultado.returncode != 0:
            raise ValueError(resultado.stderr or resultado.stdout or "Erro ao converter documento para PDF.")
        if not caminho_pdf.exists():
            arquivos_pdf = list(pasta_temp_path.glob("*.pdf"))
            if arquivos_pdf:
                caminho_pdf = arquivos_pdf[0]
            else:
                raise ValueError("O LibreOffice não gerou o arquivo PDF esperado.")
        pdf_bytes = caminho_pdf.read_bytes()
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{limpar_nome_download(nome_base)}.pdf"'
    return response


def gerar_documento_realinhamento(request, realinhamento_id, tipo_documento, formato):
    formato = (formato or "").lower(); tipo_documento = (tipo_documento or "").lower()
    if tipo_documento not in DOCUMENTOS_REALINHAMENTO:
        messages.error(request, "Tipo de documento inválido."); return redirect("documentos:realinhamento_precos")
    if formato not in ["word", "pdf"]:
        messages.error(request, "Formato inválido."); return redirect("documentos:realinhamento_precos")
    realinhamento = get_object_or_404(RealinhamentoPreco.objects.select_related("contrato", "contrato__pregao", "contrato__escola", "contrato__escola__municipio", "contrato__fornecedor"), id=realinhamento_id, status=RealinhamentoPreco.STATUS_REGISTRADO)
    contrato = realinhamento.contrato; config = DOCUMENTOS_REALINHAMENTO[tipo_documento]; campos = config.get("campos", [])
    if request.method != "POST":
        return render(request, "documentos/dados_documento_realinhamento.html", {"realinhamento": realinhamento, "contrato": contrato, "tipo_documento": tipo_documento, "formato": formato, "titulo_documento": config["titulo"], "campos": campos})
    dados_extra = {}
    for nome, rotulo, tipo_campo, obrigatorio in campos:
        valor = request.POST.get(nome, "").strip()
        if obrigatorio and not valor:
            messages.error(request, f"Informe: {rotulo}.")
            return redirect("documentos:gerar_documento_realinhamento", realinhamento_id=realinhamento.id, tipo_documento=tipo_documento, formato=formato)
        dados_extra[nome] = valor
    try:
        documento = montar_documento_realinhamento_por_tipo(realinhamento, tipo_documento, dados_extra)
        nome_base = f"{config['nome']}_{contrato.numero_contrato}_{contrato.escola.nome_escola}_{contrato.fornecedor.razao_social}"
        return resposta_download_pdf(documento, nome_base) if formato == "pdf" else resposta_download_docx(documento, nome_base)
    except Exception as erro:
        messages.error(request, str(erro)); return redirect("documentos:realinhamento_precos")


# ============================================================
# AJUSTES V2 - DOCUMENTOS DE REALINHAMENTO / REVISÃO DE PREÇOS
# ============================================================


def mes_extenso_pt(data):
    meses = [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    if not data:
        return ""
    return meses[data.month - 1]


def data_extenso_simples(data):
    if not data:
        return ""
    return f"{data.day:02d} de {mes_extenso_pt(data)} de {data.year}"


def data_ata_extenso(data):
    if not data:
        return ""
    return f"{data.day:02d} dias do mês de {mes_extenso_pt(data)} de {data.year}"


def texto_pregao_ano(pregao):
    return f"{pregao.numero}/{pregao.ano}" if pregao else ""


def texto_pregao_ano_curto(pregao):
    if not pregao:
        return ""
    return f"{pregao.numero}/{str(pregao.ano)[-2:]}"


def texto_fornecedor_cnpj(fornecedor):
    if not fornecedor:
        return ""
    return f"{fornecedor.razao_social or ''} - CNPJ: {fornecedor.cnpj or ''}"


def produtos_realinhados_texto(realinhamento):
    partes = []
    for item in realinhamento.itens.all():
        ci = item.contrato_item
        partes.append(f"{ci.item.nome_item} - {formatar_moeda_br(item.valor_unitario_novo)}")
    return "; ".join(partes)


def produtos_realinhados_texto_com_marca(realinhamento):
    partes = []
    for item in realinhamento.itens.all():
        ci = item.contrato_item
        partes.append(
            f"{ci.item.nome_item}, marca {ci.marca}, novo valor unitário {formatar_moeda_br(item.valor_unitario_novo)}"
        )
    return "; ".join(partes)


def remover_paragrafo(paragrafo):
    elemento = paragrafo._element
    pai = elemento.getparent()
    if pai is not None:
        pai.remove(elemento)


def remover_paragrafos_modelo_termo(documento):
    remover = []
    for paragrafo in documento.paragraphs:
        texto = (paragrafo.text or "").lower()
        if "(nome do item)" in texto or "valor unitário (unidade de medida)" in texto:
            remover.append(paragrafo)
    for paragrafo in remover:
        remover_paragrafo(paragrafo)


def substituir_em_todos_elementos(documento, substituicoes):
    # Remove chaves vazias e converte valores para string.
    substituicoes = {
        str(chave): str(valor or "")
        for chave, valor in (substituicoes or {}).items()
        if chave is not None and str(chave) != ""
    }
    substituir_placeholders_documento(documento, substituicoes)


def montar_substituicoes_realinhamento(realinhamento, dados_extra=None):
    dados_extra = dados_extra or {}
    contrato = realinhamento.contrato
    pregao = contrato.pregao
    escola = contrato.escola
    fornecedor = contrato.fornecedor
    municipio_nome = texto_municipio_nome(escola)
    municipio_uf = texto_municipio_escola(escola)
    numero_pregao = texto_pregao_ano(pregao)
    numero_pregao_curto = texto_pregao_ano_curto(pregao)
    numero_contrato = contrato.numero_contrato or f"{contrato.id:04d}/{pregao.ano}"
    data_base = realinhamento.data_termo_aditivo or realinhamento.data_realinhamento or timezone.localdate()
    endereco_escola = ", ".join([p for p in [
        escola.endereco or "",
        f"nº {escola.numero}" if escola.numero else "",
        f"Bairro {escola.bairro}" if escola.bairro else "",
        municipio_uf,
    ] if p])

    manifestacao = dados_extra.get("manifestacao_juridica", "")
    numero_processo = dados_extra.get("numero_processo", "")
    processo_adm = dados_extra.get("processo_administrativo") or numero_processo
    numero_parecer = dados_extra.get("numero_parecer", "")
    lavrador = dados_extra.get("nome_lavrador_ata", "")

    return {
        # TERMO ADITIVO
        "AO CONTRATO N°. _____/202__": f"AO CONTRATO N°. {numero_contrato}",
        "PREGÃO PRESENCIAL N.º___/20__": f"PREGÃO PRESENCIAL N.º {numero_pregao}",
        "CELEBRAM A ESCOLA  __________________ E A EMPRESA __________________.": f"CELEBRAM A ESCOLA {escola.nome_escola or ''} E A EMPRESA {fornecedor.razao_social or ''}.",
        "A Escola Estadual ________________________": f"A Escola Estadual {escola.nome_escola or ''}",
        "situada à Rua _________, nº ________, Bairro _____________": f"situada à {endereco_escola}",
        "no município de _______- MT": f"no município de {municipio_nome}- MT",
        "inscrita no CNPJ nº. ________________": f"inscrita no CNPJ nº. {escola.cnpj or ''}",
        "representada pelo (a) Senhor (a) _____________": f"representada pelo (a) Senhor (a) {escola.presidente_cdce or ''}",
        "(Presidente) __________": f"(Presidente) {escola.presidente_cdce or ''}",
        "Empresa ______________________.": f"Empresa {fornecedor.razao_social or ''}.",
        "inscrita no CNPJ/MF sob o nº. ____________": f"inscrita no CNPJ/MF sob o nº. {fornecedor.cnpj or ''}",
        "com sede social na Rua ______________, nº ______, bairro _______": f"com sede social em {fornecedor.endereco or ''}",
        "representada pelo Senhor _________": f"representada pelo Senhor {fornecedor.representante_legal or ''}",
        "RG nº. _________ SSP/____": f"RG nº. {fornecedor.rg_representante or ''} {fornecedor.orgao_expedidor_representante or ''}",
        "sob o nº. _________": f"sob o nº. {fornecedor.cpf_representante or ''}",
        "Manifestação Jurídica de\n16720/2026/UNGR/SEDUC": f"Manifestação Jurídica de\n{manifestacao or '______/____/UNGR/SEDUC'}",
        "Contrato nº _______/202__": f"Contrato nº {numero_contrato}",
        "Contrato n° _____/202__": f"Contrato n° {numero_contrato}",
        "Município, data mês e ano.": f"{municipio_uf}, {data_extenso_simples(data_base)}.",
        "( Nome do presidente CDCE)": escola.presidente_cdce or "",
        "(CPF do presidente CDCE)": escola.cpf_presidente or "",
        "(Razão Social Fornecedor)": fornecedor.razao_social or "",
        "(CNPJ do fornecedor)": fornecedor.cnpj or "",
        "(nome do representante)": fornecedor.representante_legal or "",
        "(CPF do representante)": fornecedor.cpf_representante or "",

        # EXTRATO
        "Pregão Presencial n.º ____/202__": f"Pregão Presencial n.º {numero_pregao}",
        "manifestação jurídico nº _________________": f"manifestação jurídico nº {manifestacao}",
        "Processo n.º _________": f"Processo n.º {numero_processo}",
        "RP n.º ____/202___": f"RP n.º {numero_pregao}",
        "município de _________/MT": f"município de {municipio_nome}/MT",
        "Município de__________": f"Município de {municipio_nome}",
        "Contratada: _______________;  CNPJ _______": f"Contratada: {fornecedor.razao_social or ''}; CNPJ {fornecedor.cnpj or ''}",
        "Produtos realinhados: (nome do produto) (novo valor).": f"Produtos realinhados: {produtos_realinhados_texto(realinhamento)}.",
        "(nome completo CPF e cargo)": f"{dados_extra.get('representante_dre', '')} - CPF: {dados_extra.get('cpf_representante_dre', '')} - {dados_extra.get('cargo_representante_dre', '')}",

        # PARECER
        "PARECER Nº _____/20__ – REVISÃO DE PREÇO": f"PARECER Nº {numero_parecer} – REVISÃO DE PREÇO",
        "PARECER Nº  _____/20__ – REVISÃO DE PREÇO": f"PARECER Nº {numero_parecer} – REVISÃO DE PREÇO",
        "PARECER Nº _____/20__": f"PARECER Nº {numero_parecer}",
        "Ref.: Pregão Presencial SRP no ____/___": f"Ref.: Pregão Presencial SRP no {numero_pregao}",
        "Processo Administrativo n° ______________": f"Processo Administrativo n° {processo_adm}",
        "Sinop/MT, ____  de _____ de ____": f"Sinop/MT, {data_extenso_simples(data_base)}",
        "Pregão Presencial nº ____/____- (MUNICÍPIO)": f"Pregão Presencial nº {numero_pregao}- {municipio_nome}",
        "(RAZÃO SOCIAL DO FORNECEDOR)": fornecedor.razao_social or "",
        "(CNPJ DO FORNECEDOR)": fornecedor.cnpj or "",
        "Município: _____________": f"Município: {municipio_nome}",

        # ATA
        "Referente Pregão Presencial SRP no ____/20__, Município de __________": f"Referente Pregão Presencial SRP no {numero_pregao}, Município de {municipio_nome}",
        "Aos _____ dias do mês de ____ de ________ (___/___/____)": f"Aos {data_ata_extenso(data_base)} ({data_curta_br(data_base)})",
        "pregão ____/20__- (MUNICÍPIO)": f"pregão {numero_pregao}- {municipio_nome}",
        "solicitado pela empresa _______________________, CNPJ: _________": f"solicitado pela empresa {fornecedor.razao_social or ''}, CNPJ: {fornecedor.cnpj or ''}",
        "eu, ____________________, Coordenador": f"eu, {lavrador}, Coordenador",

        # RESULTADO FINAL
        "RESULTADO DO PREGÃO PRESENCIAL SRP Nº ______/20___ – (MUNICÍPIO)": f"RESULTADO DO PREGÃO PRESENCIAL SRP Nº {numero_pregao} – {municipio_nome}",
        "Pregão Presencial SRP Nº ______/20___ – (MUNICÍPIO)": f"Pregão Presencial SRP Nº {numero_pregao} – {municipio_nome}",
        "fornecedor _______________________ - CNPJ: ___________": f"fornecedor {fornecedor.razao_social or ''} - CNPJ: {fornecedor.cnpj or ''}",
        "Sinop/MT, ___  de _______de 20__.": f"Sinop/MT, {data_extenso_simples(data_base)}.",
        "Sinop/MT, ___ de _______de 20__.": f"Sinop/MT, {data_extenso_simples(data_base)}.",
    }


def inserir_paragrafos_itens_termo_valor(documento, realinhamento):
    remover_paragrafos_modelo_termo(documento)
    paragrafo_alvo = None
    for paragrafo in documento.paragraphs:
        if "fica aditado o valor dos itens" in (paragrafo.text or "").strip().lower():
            paragrafo_alvo = paragrafo
            break
    if paragrafo_alvo is None:
        paragrafo_alvo = documento.add_paragraph("Fica aditado o valor dos Itens:")
    ultimo_elemento = paragrafo_alvo._p
    contrato = realinhamento.contrato
    municipio = texto_municipio_nome(contrato.escola)
    pregao = contrato.pregao
    for indice, item_realinhado in enumerate(realinhamento.itens.all(), start=1):
        contrato_item = item_realinhado.contrato_item
        texto = (
            f"{indice} - {contrato_item.item.nome_item}, marca {contrato_item.marca}, "
            f"adquirido junto ao Processo Licitatório do Pregão Presencial de n.º {texto_pregao_ano(pregao)}, "
            f"realizado pela Câmara de Negócios do Município de {municipio}/MT, cujo valor unitário "
            f"({contrato_item.unidade}) do produto passa a ser na quantia de {formatar_moeda_br(item_realinhado.valor_unitario_novo)}."
        )
        novo = documento.add_paragraph()
        novo.style = paragrafo_alvo.style
        novo.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        run = novo.add_run(texto)
        run.font.name = "Arial"
        run.font.size = Pt(11)
        ultimo_elemento.addnext(novo._p)
        ultimo_elemento = novo._p


def preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=0, incluir_cnpj_fornecedor=False, municipio_no_cabecalho=False):
    if len(documento.tables) <= tabela_indice:
        return
    tabela = documento.tables[tabela_indice]
    municipio = texto_municipio_nome(realinhamento.contrato.escola)
    indice_cabecalho = 0
    if municipio_no_cabecalho and len(tabela.rows) >= 1:
        for celula in tabela.rows[0].cells:
            if "Município" in celula.text:
                definir_texto_celula(celula, f"Município: {municipio}", negrito=True, tamanho=9)
                break
        indice_cabecalho = 1
    while len(tabela.rows) > indice_cabecalho + 1:
        linha = tabela.rows[-1]
        linha._tr.getparent().remove(linha._tr)
    for linha_dados in dados_linhas_itens_revisao(realinhamento, incluir_cnpj_fornecedor=incluir_cnpj_fornecedor):
        nova_linha = tabela.add_row()
        for indice, valor in enumerate(linha_dados):
            if indice < len(nova_linha.cells):
                alinhamento = WD_ALIGN_PARAGRAPH.LEFT if indice in [1, 4] else WD_ALIGN_PARAGRAPH.CENTER
                definir_texto_celula(nova_linha.cells[indice], valor, tamanho=8, alinhamento=alinhamento)
    aplicar_bordas_tabela(tabela)


def montar_documento_termo_aditivo_word(realinhamento):
    realinhamento = carregar_realinhamento_completo(realinhamento.id)
    contrato = realinhamento.contrato
    if contrato.status not in [ContratoGerado.STATUS_GERADO, ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO]:
        raise ValueError("O termo aditivo só pode ser gerado para contratos ativos ou parcialmente distratados.")
    if not realinhamento.numero_termo_aditivo or not realinhamento.data_termo_aditivo:
        raise ValueError("Registre o número e a data do termo aditivo antes de gerar o documento.")
    if not realinhamento.itens.exists():
        raise ValueError("Este realinhamento não possui itens para gerar o termo aditivo.")
    modelo_path = caminho_modelo_realinhamento("modelo_termo_aditivo_valor.docx")
    if not modelo_path.exists():
        raise FileNotFoundError("Modelo de termo aditivo não encontrado em modelos/aditivos/modelo_termo_aditivo_valor.docx")
    documento = Document(modelo_path)
    corrigir_margens_documento(documento)
    substituir_em_todos_elementos(documento, montar_substituicoes_realinhamento(realinhamento))
    inserir_paragrafos_itens_termo_valor(documento, realinhamento)
    return documento


def montar_documento_realinhamento_por_tipo(realinhamento, tipo_documento, dados_extra=None):
    if tipo_documento not in DOCUMENTOS_REALINHAMENTO:
        raise ValueError("Tipo de documento inválido.")
    realinhamento = carregar_realinhamento_completo(realinhamento.id)
    config = DOCUMENTOS_REALINHAMENTO[tipo_documento]
    modelo_path = caminho_modelo_realinhamento(config["modelo"])
    if not modelo_path.exists():
        raise FileNotFoundError(f"Modelo não encontrado em modelos/aditivos/{config['modelo']}")
    documento = Document(modelo_path)
    corrigir_margens_documento(documento)
    substituir_em_todos_elementos(documento, montar_substituicoes_realinhamento(realinhamento, dados_extra))
    if tipo_documento == "parecer":
        contrato = realinhamento.contrato
        if len(documento.tables) >= 1 and len(documento.tables[0].rows) >= 2:
            definir_texto_celula(documento.tables[0].rows[1].cells[0], contrato.fornecedor.razao_social, tamanho=10, alinhamento=WD_ALIGN_PARAGRAPH.LEFT)
            definir_texto_celula(documento.tables[0].rows[1].cells[1], contrato.fornecedor.cnpj, tamanho=10, alinhamento=WD_ALIGN_PARAGRAPH.LEFT)
        preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=1, municipio_no_cabecalho=True)
    elif tipo_documento == "ata":
        preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=0)
    elif tipo_documento == "resultado":
        preencher_tabela_revisao_precos(documento, realinhamento, tabela_indice=0, incluir_cnpj_fornecedor=True)
    return documento

# ============================================================
# CHAMADA PÚBLICA - ADJUDICAÇÃO UNIFICADA
# ============================================================

def decimal_br_para_decimal(valor):
    valor = (valor or "").strip()

    if not valor:
        return Decimal("0")

    valor = valor.replace("R$", "").replace(" ", "")

    if "," in valor and "." in valor:
        valor = valor.replace(".", "").replace(",", ".")
    else:
        valor = valor.replace(",", ".")

    return Decimal(valor)


def garantir_itens_chamada_publica(pregao):
    """
    Garante a existência dos PregaoItem a partir do Quantitativo por Pregão.
    A Chamada Pública usa os mesmos itens/quantitativos já cadastrados no certame.
    """
    if PregaoItem.objects.filter(pregao=pregao).exists():
        return

    quantitativos = (
        QuantitativoPregao.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("item__nome_item")
    )

    ordem = 1

    for quantitativo in quantitativos:
        PregaoItem.objects.create(
            pregao=pregao,
            item=quantitativo.item,
            ordem=ordem,
            quantidade_total=quantitativo.quantidade,
            status=PregaoItem.STATUS_PENDENTE,
        )
        ordem += 1


def obter_chamadas_publicas_projetos():
    return (
        Pregao.objects.filter(tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)
        .prefetch_related("municipios", "fornecedores")
        .order_by("-ano", "-numero")
    )


def fornecedores_validos_chamada_publica(pregao):
    return (
        pregao.fornecedores.filter(
            ativo=True,
            tipo_fornecedor_chamada__in=[
                Fornecedor.TIPO_INDIVIDUAL,
                Fornecedor.TIPO_GRUPO_FORMAL,
            ],
        )
        .order_by("razao_social")
    )


def total_adjudicado_item_chamada(pregao, pregao_item, projeto_excluir=None):
    queryset = ResultadoChamadaPublicaItem.objects.filter(
        pregao=pregao,
        pregao_item=pregao_item,
        status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
    )

    if projeto_excluir:
        queryset = queryset.exclude(projeto=projeto_excluir)

    return queryset.aggregate(total=Sum("quantidade_adjudicada")).get("total") or Decimal("0.000")


def total_adjudicado_fornecedor_ano(fornecedor, ano, projeto_excluir=None):
    queryset = ResultadoChamadaPublicaItem.objects.filter(
        fornecedor=fornecedor,
        pregao__ano=ano,
        status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
    )

    if projeto_excluir:
        queryset = queryset.exclude(projeto=projeto_excluir)

    return queryset.aggregate(total=Sum("valor_total")).get("total") or Decimal("0.00")


def projetos_venda(request):
    """
    Tela principal da adjudicação da Chamada Pública.

    O Projeto de Venda físico permanece como documento entregue pelo fornecedor.
    No sistema, esta tela lista os registros de adjudicação feitos a partir dele.
    """
    chamadas_publicas = obter_chamadas_publicas_projetos()

    pregao_id = request.GET.get("pregao")
    pregao = None

    projetos = (
        ProjetoVenda.objects.select_related("pregao", "fornecedor")
        .prefetch_related("itens", "resultados")
        .order_by("-criado_em")
    )

    if pregao_id:
        pregao = get_object_or_404(Pregao, id=pregao_id, tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)
        projetos = projetos.filter(pregao=pregao)

    return render(
        request,
        "documentos/projetos_venda.html",
        {
            "chamadas_publicas": chamadas_publicas,
            "pregao": pregao,
            "projetos": projetos,
        },
    )


def montar_contexto_registro_projeto_venda(pregao, projeto=None):
    garantir_itens_chamada_publica(pregao)

    fornecedores = fornecedores_validos_chamada_publica(pregao)

    itens_pregao = (
        PregaoItem.objects.filter(
            pregao=pregao,
            media_preco__isnull=False,
        )
        .select_related("item")
        .order_by("ordem", "item__nome_item")
    )

    itens_salvos = {}

    if projeto:
        itens_salvos = {
            item_salvo.pregao_item_id: item_salvo
            for item_salvo in ProjetoVendaItem.objects.filter(projeto=projeto)
            .select_related("pregao_item", "item")
        }

    linhas_itens = []

    for item_pregao in itens_pregao:
        item_salvo = itens_salvos.get(item_pregao.id)
        ja_adjudicada = total_adjudicado_item_chamada(
            pregao=pregao,
            pregao_item=item_pregao,
            projeto_excluir=projeto,
        )
        saldo_restante = item_pregao.quantidade_total - ja_adjudicada

        linhas_itens.append(
            {
                "item_pregao": item_pregao,
                "item_salvo": item_salvo,
                "media_preco": item_pregao.media_preco,
                "ja_adjudicada": ja_adjudicada,
                "saldo_restante": saldo_restante,
            }
        )

    return fornecedores, linhas_itens


def salvar_adjudicacao_unificada(
    request,
    pregao,
    fornecedor,
    cronograma_entrega,
    observacoes,
    itens_digitados,
    projeto=None,
):
    with transaction.atomic():
        if projeto is None:
            projeto = ProjetoVenda.objects.create(
                pregao=pregao,
                fornecedor=fornecedor,
                data_entrega=timezone.localdate(),
                cronograma_entrega=cronograma_entrega,
                observacoes=observacoes,
                valor_total=Decimal("0.00"),
                registrado_por=request.user if request.user.is_authenticated else None,
            )
        else:
            ResultadoChamadaPublicaItem.objects.filter(projeto=projeto).delete()
            projeto.itens.all().delete()

            projeto.fornecedor = fornecedor
            projeto.data_entrega = timezone.localdate()
            projeto.cronograma_entrega = cronograma_entrega
            projeto.observacoes = observacoes
            projeto.valor_total = Decimal("0.00")
            projeto.status = ProjetoVenda.STATUS_REGISTRADO
            projeto.save(
                update_fields=[
                    "fornecedor",
                    "data_entrega",
                    "cronograma_entrega",
                    "observacoes",
                    "valor_total",
                    "status",
                    "atualizado_em",
                ]
            )

        valor_total_geral = Decimal("0.00")

        for item in itens_digitados:
            projeto_item = ProjetoVendaItem.objects.create(
                projeto=projeto,
                pregao_item=item["pregao_item"],
                item=item["item"],
                marca=item["marca"],
                quantidade_ofertada=item["quantidade"],
                valor_unitario=item["valor_unitario"],
                valor_total=item["valor_total"],
                cronograma_entrega="",
            )

            ResultadoChamadaPublicaItem.objects.create(
                pregao=pregao,
                projeto=projeto,
                projeto_item=projeto_item,
                fornecedor=fornecedor,
                pregao_item=item["pregao_item"],
                item=item["item"],
                quantidade_adjudicada=item["quantidade"],
                valor_unitario=item["valor_unitario"],
                valor_total=item["valor_total"],
                registrado_por=request.user if request.user.is_authenticated else None,
            )

            valor_total_geral += item["valor_total"]

        projeto.valor_total = valor_total_geral
        projeto.save(update_fields=["valor_total", "atualizado_em"])

        return projeto


def registrar_projeto_venda(request, pregao_id):
    if usuario_eh_consulta_escola(request):
        messages.error(request, "Seu perfil permite apenas consultar informações. Esta ação não está disponível para usuários Consulta/Escola.")
        return redirect("documentos:resultado_chamada_publica")

    """
    Registra diretamente a adjudicação da Chamada Pública.

    O nome da URL foi mantido para não quebrar rotas/migrations anteriores,
    mas o fluxo agora é de adjudicação direta, sem etapa separada de Projeto de Venda.
    """
    pregao = get_object_or_404(Pregao, id=pregao_id, tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)

    fornecedores, linhas_itens = montar_contexto_registro_projeto_venda(pregao)

    if request.method == "POST":
        fornecedor_id = request.POST.get("fornecedor")
        cronograma_entrega = (request.POST.get("cronograma_entrega") or "").strip()
        observacoes = (request.POST.get("observacoes") or "").strip()

        if not fornecedor_id:
            messages.error(request, "Selecione o fornecedor.")
            return redirect("documentos:registrar_projeto_venda", pregao_id=pregao.id)

        fornecedor = get_object_or_404(
            Fornecedor,
            id=fornecedor_id,
            tipo_fornecedor_chamada__in=[
                Fornecedor.TIPO_INDIVIDUAL,
                Fornecedor.TIPO_GRUPO_FORMAL,
            ],
        )

        if not pregao.fornecedores.filter(id=fornecedor.id).exists():
            messages.error(request, "O fornecedor selecionado não está vinculado a esta Chamada Pública.")
            return redirect("documentos:registrar_projeto_venda", pregao_id=pregao.id)

        itens_digitados = []
        erros = []
        valor_total_adjudicacao = Decimal("0.00")

        for linha in linhas_itens:
            item_pregao = linha["item_pregao"]
            quantidade_texto = request.POST.get(f"quantidade_{item_pregao.id}", "").strip()
            marca_item = (request.POST.get(f"marca_{item_pregao.id}") or "").strip()

            if not quantidade_texto:
                continue

            try:
                quantidade = decimal_br_para_decimal(quantidade_texto)
            except InvalidOperation:
                erros.append(f"A quantidade do item '{item_pregao.item.nome_item}' é inválida.")
                continue

            if quantidade <= 0:
                continue

            saldo_restante = linha["saldo_restante"]

            if quantidade > saldo_restante:
                erros.append(
                    f"A quantidade adjudicada do item '{item_pregao.item.nome_item}' ultrapassa o saldo restante. "
                    f"Saldo disponível: {saldo_restante}."
                )
                continue

            if not item_pregao.media_preco:
                erros.append(
                    f"O item '{item_pregao.item.nome_item}' não possui Média de Preço cadastrada."
                )
                continue

            valor_unitario = item_pregao.media_preco.quantize(Decimal("0.01"))
            valor_total = (quantidade * valor_unitario).quantize(Decimal("0.01"))
            valor_total_adjudicacao += valor_total

            itens_digitados.append(
                {
                    "pregao_item": item_pregao,
                    "item": item_pregao.item,
                    "quantidade": quantidade,
                    "marca": marca_item,
                    "valor_unitario": valor_unitario,
                    "valor_total": valor_total,
                }
            )

        if not itens_digitados:
            erros.append("Informe a quantidade adjudicada em pelo menos um item.")

        limite_fornecedor = fornecedor.limite_anual_chamada_publica or Decimal("0.00")
        valor_ja_adjudicado_fornecedor = total_adjudicado_fornecedor_ano(
            fornecedor=fornecedor,
            ano=pregao.ano,
        )

        if limite_fornecedor and (valor_ja_adjudicado_fornecedor + valor_total_adjudicacao) > limite_fornecedor:
            erros.append(
                f"O valor total adjudicado ultrapassa o limite anual do fornecedor. "
                f"Limite: {fornecedor.get_limite_anual_chamada_publica_display()}."
            )

        if erros:
            for erro in erros:
                messages.error(request, erro)

            return render(
                request,
                "documentos/registrar_projeto_venda.html",
                {
                    "pregao": pregao,
                    "fornecedores": fornecedores,
                    "linhas_itens": linhas_itens,
                    "projeto": None,
                    "modo_edicao": False,
                    "dados_post": request.POST,
                },
            )

        salvar_adjudicacao_unificada(
            request=request,
            pregao=pregao,
            fornecedor=fornecedor,
            cronograma_entrega=cronograma_entrega,
            observacoes=observacoes,
            itens_digitados=itens_digitados,
        )

        messages.success(request, "Adjudicação registrada com sucesso.")
        return redirect(f"{reverse('documentos:projetos_venda')}?pregao={pregao.id}")

    return render(
        request,
        "documentos/registrar_projeto_venda.html",
        {
            "pregao": pregao,
            "fornecedores": fornecedores,
            "linhas_itens": linhas_itens,
            "projeto": None,
            "modo_edicao": False,
            "dados_post": {},
        },
    )


def editar_projeto_venda(request, projeto_id):
    if usuario_eh_consulta_escola(request):
        messages.error(request, "Seu perfil permite apenas consultar informações. Esta ação não está disponível para usuários Consulta/Escola.")
        return redirect("documentos:resultado_chamada_publica")

    projeto = get_object_or_404(
        ProjetoVenda.objects.select_related("pregao", "fornecedor"),
        id=projeto_id,
    )

    pregao = projeto.pregao
    fornecedores, linhas_itens = montar_contexto_registro_projeto_venda(pregao, projeto=projeto)

    if projeto.status == ProjetoVenda.STATUS_CANCELADO:
        messages.error(request, "Registro cancelado não pode ser editado.")
        return redirect(f"{reverse('documentos:projetos_venda')}?pregao={pregao.id}")

    if request.method == "POST":
        fornecedor_id = request.POST.get("fornecedor")
        cronograma_entrega = (request.POST.get("cronograma_entrega") or "").strip()
        observacoes = (request.POST.get("observacoes") or "").strip()

        fornecedor = get_object_or_404(
            Fornecedor,
            id=fornecedor_id,
            tipo_fornecedor_chamada__in=[
                Fornecedor.TIPO_INDIVIDUAL,
                Fornecedor.TIPO_GRUPO_FORMAL,
            ],
        )

        if not pregao.fornecedores.filter(id=fornecedor.id).exists():
            messages.error(request, "O fornecedor selecionado não está vinculado a esta Chamada Pública.")
            return redirect("documentos:editar_projeto_venda", projeto_id=projeto.id)

        itens_digitados = []
        erros = []
        valor_total_adjudicacao = Decimal("0.00")

        for linha in linhas_itens:
            item_pregao = linha["item_pregao"]
            quantidade_texto = request.POST.get(f"quantidade_{item_pregao.id}", "").strip()
            marca_item = (request.POST.get(f"marca_{item_pregao.id}") or "").strip()

            if not quantidade_texto:
                continue

            try:
                quantidade = decimal_br_para_decimal(quantidade_texto)
            except InvalidOperation:
                erros.append(f"A quantidade do item '{item_pregao.item.nome_item}' é inválida.")
                continue

            if quantidade <= 0:
                continue

            saldo_restante = linha["saldo_restante"]

            if quantidade > saldo_restante:
                erros.append(
                    f"A quantidade adjudicada do item '{item_pregao.item.nome_item}' ultrapassa o saldo restante. "
                    f"Saldo disponível: {saldo_restante}."
                )
                continue

            if not item_pregao.media_preco:
                erros.append(
                    f"O item '{item_pregao.item.nome_item}' não possui Média de Preço cadastrada."
                )
                continue

            valor_unitario = item_pregao.media_preco.quantize(Decimal("0.01"))
            valor_total = (quantidade * valor_unitario).quantize(Decimal("0.01"))
            valor_total_adjudicacao += valor_total

            itens_digitados.append(
                {
                    "pregao_item": item_pregao,
                    "item": item_pregao.item,
                    "quantidade": quantidade,
                    "marca": marca_item,
                    "valor_unitario": valor_unitario,
                    "valor_total": valor_total,
                }
            )

        if not itens_digitados:
            erros.append("Informe a quantidade adjudicada em pelo menos um item.")

        limite_fornecedor = fornecedor.limite_anual_chamada_publica or Decimal("0.00")
        valor_ja_adjudicado_fornecedor = total_adjudicado_fornecedor_ano(
            fornecedor=fornecedor,
            ano=pregao.ano,
            projeto_excluir=projeto,
        )

        if limite_fornecedor and (valor_ja_adjudicado_fornecedor + valor_total_adjudicacao) > limite_fornecedor:
            erros.append(
                f"O valor total adjudicado ultrapassa o limite anual do fornecedor. "
                f"Limite: {fornecedor.get_limite_anual_chamada_publica_display()}."
            )

        if erros:
            for erro in erros:
                messages.error(request, erro)

            return render(
                request,
                "documentos/registrar_projeto_venda.html",
                {
                    "pregao": pregao,
                    "fornecedores": fornecedores,
                    "linhas_itens": linhas_itens,
                    "projeto": projeto,
                    "modo_edicao": True,
                    "dados_post": request.POST,
                },
            )

        salvar_adjudicacao_unificada(
            request=request,
            pregao=pregao,
            fornecedor=fornecedor,
            cronograma_entrega=cronograma_entrega,
            observacoes=observacoes,
            itens_digitados=itens_digitados,
            projeto=projeto,
        )

        messages.success(request, "Adjudicação atualizada com sucesso.")
        return redirect(f"{reverse('documentos:projetos_venda')}?pregao={pregao.id}")

    return render(
        request,
        "documentos/registrar_projeto_venda.html",
        {
            "pregao": pregao,
            "fornecedores": fornecedores,
            "linhas_itens": linhas_itens,
            "projeto": projeto,
            "modo_edicao": True,
            "dados_post": {},
        },
    )


def cancelar_projeto_venda(request, projeto_id):
    if usuario_eh_consulta_escola(request):
        messages.error(request, "Seu perfil permite apenas consultar informações. Esta ação não está disponível para usuários Consulta/Escola.")
        return redirect("documentos:resultado_chamada_publica")

    projeto = get_object_or_404(ProjetoVenda, id=projeto_id)

    if request.method != "POST":
        return redirect(f"{reverse('documentos:projetos_venda')}?pregao={projeto.pregao_id}")

    projeto.status = ProjetoVenda.STATUS_CANCELADO
    projeto.save(update_fields=["status", "atualizado_em"])

    ResultadoChamadaPublicaItem.objects.filter(projeto=projeto).update(
        status=ResultadoChamadaPublicaItem.STATUS_CANCELADO
    )

    messages.success(request, "Adjudicação cancelada com sucesso.")
    return redirect(f"{reverse('documentos:projetos_venda')}?pregao={projeto.pregao_id}")


def adjudicacao_chamada_publica(request):
    """
    Mantido apenas por compatibilidade com o menu/rotas anteriores.
    Redireciona para a tela unificada de adjudicação.
    """
    pregao_id = request.GET.get("pregao") or request.POST.get("pregao")

    if pregao_id:
        return redirect(f"{reverse('documentos:projetos_venda')}?pregao={pregao_id}")

    return redirect("documentos:projetos_venda")


def resultado_chamada_publica(request):
    chamadas_publicas = obter_chamadas_publicas_projetos()

    pregao_id = request.GET.get("pregao")
    pregao = None
    resultados = ResultadoChamadaPublicaItem.objects.none()
    resumo_itens = []

    if pregao_id:
        pregao = get_object_or_404(Pregao, id=pregao_id, tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)

        resultados = (
            ResultadoChamadaPublicaItem.objects.filter(
                pregao=pregao,
                status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
            )
            .select_related("fornecedor", "item", "pregao_item", "projeto", "projeto_item")
            .order_by("pregao_item__ordem", "fornecedor__razao_social")
        )

        totais = {
            linha["pregao_item"]: linha["total"] or Decimal("0.000")
            for linha in resultados.values("pregao_item").annotate(total=Sum("quantidade_adjudicada"))
        }

        for item_pregao in PregaoItem.objects.filter(pregao=pregao).select_related("item").order_by("ordem"):
            qtd = totais.get(item_pregao.id, Decimal("0.000"))
            resumo_itens.append(
                {
                    "item_pregao": item_pregao,
                    "adjudicado": qtd,
                    "saldo": item_pregao.quantidade_total - qtd,
                }
            )

    return render(
        request,
        "documentos/resultado_chamada_publica.html",
        {
            "chamadas_publicas": chamadas_publicas,
            "pregao": pregao,
            "resultados": resultados,
            "resumo_itens": resumo_itens,
        },
    )

# ============================================================
# CHAMADA PÚBLICA - RESULTADO FINAL WORD/PDF
# Modelo conforme documento oficial enviado pelo usuário.
# Ajustes: tabela na largura do texto, vencedores agrupados por fornecedor
# e itens desertos ao final.
# ============================================================

def limpar_nome_arquivo_chamada_publica(nome):
    texto = str(nome or "").strip()

    for caractere in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        texto = texto.replace(caractere, "-")

    return texto or "arquivo"


def formatar_quantidade_chamada(valor):
    if valor is None:
        return "0"

    numero = Decimal(valor)
    numero = numero.quantize(Decimal("0.001"))

    if numero == numero.to_integral():
        return f"{int(numero)}"

    texto = f"{numero:,.3f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    texto = texto.rstrip("0").rstrip(",")

    return texto


def obter_primeiro_municipio_certame(pregao):
    municipio = pregao.municipios.all().order_by("nome").first()

    if not municipio:
        return None

    return municipio


def obter_municipio_texto_resultado(pregao):
    municipio = obter_primeiro_municipio_certame(pregao)

    if not municipio:
        return "-"

    if municipio.uf:
        return f"{municipio.nome}/{municipio.uf}"

    return municipio.nome


def obter_fornecedor_documento_chamada(fornecedor):
    if not fornecedor:
        return "-"

    documento = ""

    tipo = getattr(fornecedor, "tipo_fornecedor_chamada", "")

    if tipo == getattr(Fornecedor, "TIPO_INDIVIDUAL", "individual"):
        documento = getattr(fornecedor, "cpf_fornecedor_individual", "") or getattr(fornecedor, "cpf", "")
    else:
        documento = getattr(fornecedor, "cnpj", "") or getattr(fornecedor, "cpf_fornecedor_individual", "")

    if documento:
        return f"{fornecedor.razao_social} - {documento}"

    return fornecedor.razao_social


def obter_unidade_resultado_chamada(item):
    unidade = ""

    if hasattr(item, "unidade_medida"):
        unidade = str(item.unidade_medida or "").strip()

    if not unidade:
        try:
            unidade = str(item.get_unidade_medida_display() or "").strip()
        except Exception:
            unidade = ""

    mapa = {
        "quilograma": "KG",
        "quilo": "KG",
        "kg": "KG",
        "litro": "Litro",
        "l": "Litro",
        "unidade": "Unid.",
        "unid": "Unid.",
    }

    return mapa.get(unidade.lower(), unidade or "-")


def obter_resultados_chamada_publica(pregao):
    return (
        ResultadoChamadaPublicaItem.objects.filter(
            pregao=pregao,
            status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
        )
        .select_related(
            "fornecedor",
            "item",
            "pregao_item",
            "projeto",
            "projeto_item",
        )
        .order_by("fornecedor__razao_social", "pregao_item__ordem", "item__nome_item")
    )


def montar_linhas_resultado_final_chamada_publica(pregao):
    """
    Monta a tabela conforme o modelo:
    1. Primeiro todos os itens adjudicados, agrupados por fornecedor.
    2. Depois os itens desertos/saldos não adjudicados, ao final da tabela.
    3. A coluna Item é sequencial no documento, não necessariamente a ordem original do cadastro.
    """
    resultados = list(obter_resultados_chamada_publica(pregao))

    quantidade_adjudicada_por_item = {}

    for resultado in resultados:
        quantidade_adjudicada_por_item[resultado.pregao_item_id] = (
            quantidade_adjudicada_por_item.get(resultado.pregao_item_id, Decimal("0.000"))
            + (resultado.quantidade_adjudicada or Decimal("0.000"))
        )

    linhas_adjudicadas = []
    linhas_desertas = []

    for resultado in resultados:
        linhas_adjudicadas.append(
            {
                "genero": resultado.item.nome_item,
                "unidade": obter_unidade_resultado_chamada(resultado.item),
                "quantidade": resultado.quantidade_adjudicada,
                "marca": resultado.projeto_item.marca or "-",
                "fornecedor": obter_fornecedor_documento_chamada(resultado.fornecedor),
                "preco": resultado.valor_unitario,
                "valor_total": resultado.valor_total,
                "deserto": False,
            }
        )

    itens_pregao = (
        PregaoItem.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("ordem", "item__nome_item")
    )

    for item_pregao in itens_pregao:
        quantidade_total = item_pregao.quantidade_total or Decimal("0.000")
        quantidade_adjudicada = quantidade_adjudicada_por_item.get(item_pregao.id, Decimal("0.000"))
        saldo = quantidade_total - quantidade_adjudicada

        if saldo > 0:
            linhas_desertas.append(
                {
                    "genero": item_pregao.item.nome_item,
                    "unidade": obter_unidade_resultado_chamada(item_pregao.item),
                    "quantidade": saldo,
                    "marca": "",
                    "fornecedor": "DESERTO -",
                    "preco": None,
                    "valor_total": Decimal("0.00"),
                    "deserto": True,
                }
            )

    linhas = linhas_adjudicadas + linhas_desertas

    for indice, linha in enumerate(linhas, start=1):
        linha["ordem"] = indice

    return linhas


def aplicar_fonte_run_resultado(run, tamanho=10, negrito=False):
    run.bold = negrito
    run.font.size = Pt(tamanho)
    run.font.name = "Arial"
    try:
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    except Exception:
        pass


def configurar_celula_texto_resultado(
    celula,
    texto,
    negrito=False,
    tamanho=10,
    alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
):
    celula.text = ""

    paragrafo = celula.paragraphs[0]
    paragrafo.alignment = alinhamento
    paragrafo.paragraph_format.space_before = Pt(0)
    paragrafo.paragraph_format.space_after = Pt(0)
    paragrafo.paragraph_format.line_spacing = 1

    run = paragrafo.add_run(str(texto or ""))
    aplicar_fonte_run_resultado(run, tamanho=tamanho, negrito=negrito)

    celula.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

def ajustar_largura_celula(celula, largura_cm):
    tc = celula._tc
    tcPr = tc.get_or_add_tcPr()
    tcW = tcPr.find(qn("w:tcW"))

    if tcW is None:
        tcW = OxmlElement("w:tcW")
        tcPr.append(tcW)

    tcW.set(qn("w:w"), str(int(largura_cm * 567)))
    tcW.set(qn("w:type"), "dxa")

def configurar_largura_tabela(tabela, largura_pct=5000):
    """
    Configura a largura da tabela em percentual.

    Compatível com versões do python-docx em que tblPr não possui
    o atributo tblW diretamente.
    """
    tbl = tabela._tbl
    tblPr = tbl.tblPr

    tblW = tblPr.find(qn("w:tblW"))

    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.append(tblW)

    tblW.set(qn("w:w"), str(largura_pct))
    tblW.set(qn("w:type"), "pct")


def montar_documento_resultado_final_chamada_publica(pregao):
    linhas = montar_linhas_resultado_final_chamada_publica(pregao)

    documento = Document()

    section = documento.sections[0]
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(1.91)
    section.right_margin = Cm(1.91)

    style = documento.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(10)

    titulo = documento.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    titulo.paragraph_format.space_after = Pt(18)
    run = titulo.add_run("RESULTADO DA CHAMADA PÚBLICA")
    run.bold = True
    run.font.name = "Arial"
    run.font.size = Pt(10)

    municipio_texto = obter_municipio_texto_resultado(pregao)

    introducao = documento.add_paragraph()
    introducao.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    introducao.paragraph_format.space_after = Pt(10)
    introducao.paragraph_format.line_spacing = 1

    texto_intro = (
        "A CÂMARA DE NEGÓCIOS DA ALIMENTAÇÃO ESCOLAR DA DIRETORIA REGIONAL DO "
        "MUNICÍPIO DE SINOP, torna público para conhecimento dos interessados o Resultado da Chamada Pública "
        f"n°{pregao.numero}/{pregao.ano} para Aquisição de Gêneros Alimentícios da Agricultura Familiar e do "
        "Empreendedor Familiar Rural, e de suas organizações, para atendimento dos alunos matriculados Rede Pública "
        f"Estadual, do município de {municipio_texto}, em observância, ao FNDE/PNAE, Resolução CD/FNDE nº 06 de "
        "08/05/2020, IN nº 011/2024/GS/SEDUC/MT, nos termos do Edital, declarado (s) vencedor (es) :"
    )

    run = introducao.add_run(texto_intro)
    run.font.name = "Arial"
    run.font.size = Pt(10)

    tabela = documento.add_table(rows=1, cols=8)
    tabela.style = "Table Grid"
    tabela.alignment = WD_TABLE_ALIGNMENT.CENTER
    tabela.autofit = False
    configurar_largura_tabela(tabela)

    # Larguras somam aproximadamente a largura útil da página A4 com margens de 1 cm.
    larguras = [0.70, 3.45, 0.75, 0.75, 1.10, 5.25, 1.35, 1.35]
    cabecalhos = [
        "Item",
        "Gênero Alimentício",
        "Unid.",
        "Quant.",
        "Marca",
        "Fornecedor/CNPJ/CPF",
        "Preço\nAquisição",
        "Valor Total",
    ]

    for indice, cabecalho in enumerate(cabecalhos):
        celula = tabela.rows[0].cells[indice]
        ajustar_largura_celula(celula, larguras[indice])
        configurar_celula_texto_resultado(celula, cabecalho, negrito=True, tamanho=10)

    total_geral = Decimal("0.00")

    for linha in linhas:
        cells = tabela.add_row().cells

        valores = [
            linha["ordem"],
            linha["genero"],
            linha["unidade"],
            formatar_quantidade_chamada(linha["quantidade"]),
            linha["marca"],
            linha["fornecedor"],
            formatar_moeda_br(linha["preco"]) if linha["preco"] is not None else "",
            formatar_moeda_br(linha["valor_total"]) if linha["valor_total"] is not None else "",
        ]

        for indice, valor in enumerate(valores):
            ajustar_largura_celula(cells[indice], larguras[indice])

            alinhamento = WD_ALIGN_PARAGRAPH.LEFT if indice in [1, 5] else WD_ALIGN_PARAGRAPH.CENTER
            configurar_celula_texto_resultado(
                cells[indice],
                valor,
                negrito=False,
                tamanho=10,
                alinhamento=alinhamento,
            )

        if linha["valor_total"] is not None:
            total_geral += linha["valor_total"]

    # Linhas em branco com zeros ao final, seguindo o padrão do modelo enviado.
    for _ in range(4):
        cells = tabela.add_row().cells

        for indice in range(7):
            ajustar_largura_celula(cells[indice], larguras[indice])
            configurar_celula_texto_resultado(cells[indice], "", tamanho=10)

        ajustar_largura_celula(cells[7], larguras[7])
        configurar_celula_texto_resultado(cells[7], "R$ 0,00", tamanho=10)

    cells = tabela.add_row().cells

    for indice in range(7):
        ajustar_largura_celula(cells[indice], larguras[indice])
        configurar_celula_texto_resultado(cells[indice], "", tamanho=10)

    ajustar_largura_celula(cells[7], larguras[7])
    configurar_celula_texto_resultado(cells[7], formatar_moeda_br(total_geral), negrito=True, tamanho=10)

    documento.add_paragraph("")

    municipio_assinatura = obter_primeiro_municipio_certame(pregao)
    nome_municipio_assinatura = municipio_assinatura.nome if municipio_assinatura else "Sinop"

    data_documento = timezone.localdate()
    meses = [
        "",
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]

    p_data = documento.add_paragraph()
    p_data.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_data.paragraph_format.space_before = Pt(18)
    run = p_data.add_run(
        f"{nome_municipio_assinatura}, {data_documento.day} de {meses[data_documento.month]} de {data_documento.year}"
    )
    run.font.name = "Arial"
    run.font.size = Pt(10)

    documento.add_paragraph("")
    documento.add_paragraph("")

    assinatura = documento.add_paragraph()
    assinatura.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = assinatura.add_run("___________________________________________________")
    run.font.name = "Arial"
    run.font.size = Pt(10)

    assinatura_nome = documento.add_paragraph()
    assinatura_nome.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = assinatura_nome.add_run("Câmara de Negócios da Alimentação Escolar")
    run.font.name = "Arial"
    run.font.size = Pt(10)

    return documento


def nome_arquivo_resultado_chamada_publica(pregao, extensao):
    nome_base = f"Resultado_Final_Chamada_Publica_{pregao.numero}_{pregao.ano}"
    return f"{limpar_nome_arquivo_chamada_publica(nome_base)}.{extensao}"


def gerar_resultado_chamada_publica_word(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id, tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)

    documento = montar_documento_resultado_final_chamada_publica(pregao)

    arquivo_saida = BytesIO()
    documento.save(arquivo_saida)
    arquivo_saida.seek(0)

    response = HttpResponse(
        arquivo_saida.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo_resultado_chamada_publica(pregao, "docx")}"'

    return response


def gerar_resultado_chamada_publica_pdf(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id, tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA)

    libreoffice = encontrar_libreoffice()

    if not libreoffice:
        messages.error(
            request,
            "LibreOffice não encontrado. Instale o LibreOffice ou verifique se o comando libreoffice/soffice está disponível no PATH.",
        )
        return redirect(f"{reverse('documentos:resultado_chamada_publica')}?pregao={pregao.id}")

    documento = montar_documento_resultado_final_chamada_publica(pregao)

    nome_docx = nome_arquivo_resultado_chamada_publica(pregao, "docx")
    nome_pdf = nome_arquivo_resultado_chamada_publica(pregao, "pdf")
    nome_base = nome_docx.replace(".docx", "")

    with tempfile.TemporaryDirectory() as pasta_temp:
        pasta_temp_path = Path(pasta_temp)

        caminho_docx = pasta_temp_path / nome_docx
        caminho_pdf = pasta_temp_path / nome_pdf

        documento.save(caminho_docx)

        comando = [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pasta_temp_path),
            str(caminho_docx),
        ]

        try:
            resultado = subprocess.run(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            messages.error(
                request,
                "O LibreOffice demorou muito para converter o Resultado Final em PDF.",
            )
            return redirect(f"{reverse('documentos:resultado_chamada_publica')}?pregao={pregao.id}")

        if resultado.returncode != 0:
            messages.error(
                request,
                f"Erro ao converter Resultado Final para PDF pelo LibreOffice: {resultado.stderr or resultado.stdout}",
            )
            return redirect(f"{reverse('documentos:resultado_chamada_publica')}?pregao={pregao.id}")

        if not caminho_pdf.exists():
            arquivos_pdf = list(pasta_temp_path.glob("*.pdf"))

            if arquivos_pdf:
                caminho_pdf = arquivos_pdf[0]
            else:
                messages.error(
                    request,
                    "O LibreOffice não gerou o arquivo PDF esperado.",
                )
                return redirect(f"{reverse('documentos:resultado_chamada_publica')}?pregao={pregao.id}")

        pdf_bytes = caminho_pdf.read_bytes()

    response = HttpResponse(
        pdf_bytes,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_base}.pdf"'

    return response



# ============================================================
# RELATÓRIOS GERENCIAIS
# ============================================================

def relatorios_usuario_eh_consulta_escola(request):
    return usuario_eh_consulta_escola(request)


def relatorios_escola_vinculada_usuario(request):
    return escola_vinculada_usuario(request)


def relatorios_certames_permitidos(request):
    certames = Pregao.objects.all().prefetch_related("municipios").order_by("-ano", "-numero")

    if relatorios_usuario_eh_consulta_escola(request):
        escola = relatorios_escola_vinculada_usuario(request)

        if not escola:
            return certames.none()

        certames = certames.filter(
            models.Q(quantitativos_escola__escola=escola)
            | models.Q(contratos_gerados__escola=escola)
        ).distinct()

    return certames


def relatorios_certame_permitido_ou_404(request, pregao_id):
    return get_object_or_404(
        relatorios_certames_permitidos(request),
        id=pregao_id,
    )


def relatorios_escolas_permitidas_por_certame(request, pregao):
    if relatorios_usuario_eh_consulta_escola(request):
        escola = relatorios_escola_vinculada_usuario(request)

        if not escola:
            return Escola.objects.none()

        return Escola.objects.filter(id=escola.id)

    return (
        Escola.objects.filter(
            quantitativos__pregao=pregao,
            quantitativos__quantidade__gt=0,
        )
        .select_related("municipio")
        .distinct()
        .order_by("nome_escola")
    )


def relatorios_obter_descricao_item(item):
    return (
        getattr(item, "descricao", "")
        or getattr(item, "descricao_item", "")
        or getattr(item, "nome_item", "")
        or ""
    )


def relatorios_unidade_item(item):
    try:
        return item.get_unidade_medida_display()
    except Exception:
        return getattr(item, "unidade_medida", "") or getattr(item, "unidade", "") or ""


def relatorios_quantidade_contratada_item(pregao, item, escola=None):
    """
    Calcula a quantidade contratada do item dentro do certame.

    Ajuste importante para Chamada Pública:
    alguns contratos são gerados por escola/fornecedor usando o vínculo
    do quantitativo da escola. Por isso, além de contrato__pregao, também
    consideramos quantitativo_escola__pregao para garantir que os contratos
    da Chamada Pública sejam contabilizados corretamente no relatório de saldo.
    """
    status_validos = [
        ContratoGerado.STATUS_GERADO,
        ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
    ]

    filtros_base = models.Q(
        item=item,
        contrato__status__in=status_validos,
    )

    filtros_certame = (
        models.Q(contrato__pregao=pregao)
        | models.Q(quantitativo_escola__pregao=pregao)
    )

    filtros = filtros_base & filtros_certame

    if escola:
        filtros = filtros & (
            models.Q(contrato__escola=escola)
            | models.Q(quantitativo_escola__escola=escola)
        )

    return (
        ContratoItemGerado.objects.filter(filtros)
        .distinct()
        .aggregate(total=Sum("quantidade_contratada"))
        .get("total")
    ) or Decimal("0")


def relatorios_calendario_certames(request):
    hoje = timezone.localdate()

    certames = (
        Pregao.objects.all()
        .prefetch_related("municipios")
        .order_by("data_pregao", "-ano", "-numero")
    )

    linhas = []

    for certame in certames:
        dias = (certame.data_pregao - hoje).days

        if dias < 0:
            tempo = f"há {abs(dias)} dia(s)"
            tempo_status = "passado"
        elif dias > 0:
            tempo = f"Falta(m) {dias} dia(s)"
            tempo_status = "futuro"
        else:
            tempo = "Hoje"
            tempo_status = "hoje"

        linhas.append(
            {
                "certame": certame,
                "dias": dias,
                "tempo": tempo,
                "tempo_status": tempo_status,
            }
        )

    return render(
        request,
        "documentos/relatorios_calendario_certames.html",
        {
            "linhas": linhas,
            "hoje": hoje,
        },
    )


def relatorios_quantitativo_certame(request):
    certames = relatorios_certames_permitidos(request)
    pregao_id = request.GET.get("pregao")
    pregao = None
    linhas = []

    if pregao_id:
        pregao = relatorios_certame_permitido_ou_404(request, pregao_id)

        if relatorios_usuario_eh_consulta_escola(request):
            escola = relatorios_escola_vinculada_usuario(request)

            quantitativos = (
                QuantitativoEscola.objects.filter(
                    pregao=pregao,
                    escola=escola,
                    quantidade__gt=0,
                )
                .select_related("item")
                .order_by("item__nome_item")
            )

            for quantitativo in quantitativos:
                linhas.append(
                    {
                        "item": quantitativo.item,
                        "unidade": relatorios_unidade_item(quantitativo.item),
                        "descricao": relatorios_obter_descricao_item(quantitativo.item),
                        "quantidade": quantitativo.quantidade,
                    }
                )
        else:
            quantitativos = (
                QuantitativoPregao.objects.filter(
                    pregao=pregao,
                    quantidade__gt=0,
                )
                .select_related("item")
                .order_by("item__nome_item")
            )

            for quantitativo in quantitativos:
                linhas.append(
                    {
                        "item": quantitativo.item,
                        "unidade": relatorios_unidade_item(quantitativo.item),
                        "descricao": relatorios_obter_descricao_item(quantitativo.item),
                        "quantidade": quantitativo.quantidade,
                    }
                )

    return render(
        request,
        "documentos/relatorios_quantitativo_certame.html",
        {
            "certames": certames,
            "pregao": pregao,
            "linhas": linhas,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_quantitativo_escola(request):
    certames = relatorios_certames_permitidos(request)
    pregao_id = request.GET.get("pregao")
    escola_id = request.GET.get("escola")
    pregao = None
    escola = None
    escolas = Escola.objects.none()
    linhas = []

    if pregao_id:
        pregao = relatorios_certame_permitido_ou_404(request, pregao_id)
        escolas = relatorios_escolas_permitidas_por_certame(request, pregao)

        if relatorios_usuario_eh_consulta_escola(request):
            escola = relatorios_escola_vinculada_usuario(request)
        elif escola_id:
            escola = get_object_or_404(escolas, id=escola_id)

        if escola:
            quantitativos = (
                QuantitativoEscola.objects.filter(
                    pregao=pregao,
                    escola=escola,
                    quantidade__gt=0,
                )
                .select_related("item")
                .order_by("item__nome_item")
            )

            for quantitativo in quantitativos:
                linhas.append(
                    {
                        "item": quantitativo.item,
                        "unidade": relatorios_unidade_item(quantitativo.item),
                        "descricao": relatorios_obter_descricao_item(quantitativo.item),
                        "quantidade": quantitativo.quantidade,
                    }
                )

    return render(
        request,
        "documentos/relatorios_quantitativo_escola.html",
        {
            "certames": certames,
            "pregao": pregao,
            "escolas": escolas,
            "escola": escola,
            "linhas": linhas,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_saldo_certame(request):
    certames = relatorios_certames_permitidos(request)
    pregao_id = request.GET.get("pregao")
    pregao = None
    linhas = []
    restrito_escola = relatorios_usuario_eh_consulta_escola(request)
    escola = relatorios_escola_vinculada_usuario(request) if restrito_escola else None

    if pregao_id:
        pregao = relatorios_certame_permitido_ou_404(request, pregao_id)

        if restrito_escola:
            quantitativos = (
                QuantitativoEscola.objects.filter(
                    pregao=pregao,
                    escola=escola,
                    quantidade__gt=0,
                )
                .select_related("item")
                .order_by("item__nome_item")
            )

            for quantitativo in quantitativos:
                contratado = relatorios_quantidade_contratada_item(
                    pregao,
                    quantitativo.item,
                    escola=escola,
                )
                saldo = quantitativo.quantidade - contratado

                if saldo > 0:
                    linhas.append(
                        {
                            "item": quantitativo.item,
                            "unidade": relatorios_unidade_item(quantitativo.item),
                            "quantitativo": quantitativo.quantidade,
                            "contratado": contratado,
                            "saldo": saldo,
                        }
                    )

        elif getattr(pregao, "tipo_certame", "") == getattr(Pregao, "TIPO_CHAMADA_PUBLICA", "chamada_publica"):
            resultados_adjudicados = (
                ResultadoChamadaPublicaItem.objects.filter(
                    pregao=pregao,
                    status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
                )
                .values("item")
                .annotate(quantidade_adjudicada_total=Sum("quantidade_adjudicada"))
                .order_by("item__nome_item")
            )

            itens_ids = [linha["item"] for linha in resultados_adjudicados]

            itens = {
                item.id: item
                for item in Item.objects.filter(id__in=itens_ids)
            }

            for resultado in resultados_adjudicados:
                item = itens.get(resultado["item"])

                if not item:
                    continue

                quantidade_adjudicada = resultado["quantidade_adjudicada_total"] or Decimal("0")

                contratado = relatorios_quantidade_contratada_item(
                    pregao,
                    item,
                )
                saldo = quantidade_adjudicada - contratado

                if saldo > 0:
                    linhas.append(
                        {
                            "item": item,
                            "unidade": relatorios_unidade_item(item),
                            "quantitativo": quantidade_adjudicada,
                            "contratado": contratado,
                            "saldo": saldo,
                        }
                    )

        else:
            quantitativos = (
                QuantitativoPregao.objects.filter(
                    pregao=pregao,
                    quantidade__gt=0,
                )
                .select_related("item")
                .order_by("item__nome_item")
            )

            for quantitativo in quantitativos:
                contratado = relatorios_quantidade_contratada_item(
                    pregao,
                    quantitativo.item,
                )
                saldo = quantitativo.quantidade - contratado

                if saldo > 0:
                    linhas.append(
                        {
                            "item": quantitativo.item,
                            "unidade": relatorios_unidade_item(quantitativo.item),
                            "quantitativo": quantitativo.quantidade,
                            "contratado": contratado,
                            "saldo": saldo,
                        }
                    )

    return render(
        request,
        "documentos/relatorios_saldo_certame.html",
        {
            "certames": certames,
            "pregao": pregao,
            "linhas": linhas,
            "restrito_escola": restrito_escola,
            "escola_vinculada": escola,
        },
    )

def relatorios_saldo_escola(request):
    certames = relatorios_certames_permitidos(request)
    pregao_id = request.GET.get("pregao")
    escola_id = request.GET.get("escola")
    pregao = None
    escola = None
    escolas = Escola.objects.none()
    linhas = []

    if pregao_id:
        pregao = relatorios_certame_permitido_ou_404(request, pregao_id)
        escolas = relatorios_escolas_permitidas_por_certame(request, pregao)

        if relatorios_usuario_eh_consulta_escola(request):
            escola = relatorios_escola_vinculada_usuario(request)
        elif escola_id:
            escola = get_object_or_404(escolas, id=escola_id)

        if escola:
            quantitativos = (
                QuantitativoEscola.objects.filter(
                    pregao=pregao,
                    escola=escola,
                    quantidade__gt=0,
                )
                .select_related("item")
                .order_by("item__nome_item")
            )

            for quantitativo in quantitativos:
                contratado = relatorios_quantidade_contratada_item(
                    pregao,
                    quantitativo.item,
                    escola=escola,
                )
                saldo = quantitativo.quantidade - contratado

                if saldo > 0:
                    linhas.append(
                        {
                            "item": quantitativo.item,
                            "unidade": relatorios_unidade_item(quantitativo.item),
                            "quantitativo": quantitativo.quantidade,
                            "contratado": contratado,
                            "saldo": saldo,
                        }
                    )

    return render(
        request,
        "documentos/relatorios_saldo_escola.html",
        {
            "certames": certames,
            "pregao": pregao,
            "escolas": escolas,
            "escola": escola,
            "linhas": linhas,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_escolas_por_certame(request, pregao_id):
    pregao = relatorios_certame_permitido_ou_404(request, pregao_id)
    escolas = relatorios_escolas_permitidas_por_certame(request, pregao)

    dados = []

    for escola in escolas:
        dados.append(
            {
                "id": escola.id,
                "nome": escola.nome_escola,
                "municipio": escola.municipio.nome if escola.municipio else "",
                "uf": escola.municipio.uf if escola.municipio and escola.municipio.uf else "",
            }
        )

    return JsonResponse({"escolas": dados})



# ============================================================
# RELATÓRIOS GERENCIAIS - ETAPA 09.1
# ============================================================

def relatorios_status_contrato_choices():
    return ContratoGerado.STATUS_CHOICES


def relatorios_contratos_base_permitidos(request):
    contratos = (
        ContratoGerado.objects.select_related(
            "pregao",
            "escola",
            "escola__municipio",
            "fornecedor",
        )
        .all()
        .order_by(
            "escola__nome_escola",
            "fornecedor__razao_social",
            "-criado_em",
        )
    )

    return aplicar_restricao_escola_contratos(request, contratos)


def relatorios_escolas_com_contratos_permitidas(request):
    if relatorios_usuario_eh_consulta_escola(request):
        escola = relatorios_escola_vinculada_usuario(request)

        if not escola:
            return Escola.objects.none()

        return Escola.objects.filter(id=escola.id).select_related("municipio")

    return (
        Escola.objects.filter(contratos_gerados__isnull=False)
        .select_related("municipio")
        .distinct()
        .order_by("nome_escola")
    )


def relatorios_fornecedores_com_contratos_permitidos(request):
    fornecedores = Fornecedor.objects.filter(contratos_gerados__isnull=False)

    if relatorios_usuario_eh_consulta_escola(request):
        escola = relatorios_escola_vinculada_usuario(request)

        if not escola:
            return fornecedores.none()

        fornecedores = fornecedores.filter(contratos_gerados__escola=escola)

    return fornecedores.distinct().order_by("razao_social")


def relatorios_contratos_por_escola(request):
    certames = relatorios_certames_permitidos(request)
    escolas = relatorios_escolas_com_contratos_permitidas(request)
    status_choices = relatorios_status_contrato_choices()

    filtros = {
        "pregao": request.GET.get("pregao") or "",
        "escola": request.GET.get("escola") or "",
        "status": request.GET.get("status") or "",
    }

    contratos = relatorios_contratos_base_permitidos(request)

    if filtros["pregao"]:
        contratos = contratos.filter(pregao_id=filtros["pregao"])
        escolas = escolas.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()

    if filtros["escola"] and not relatorios_usuario_eh_consulta_escola(request):
        contratos = contratos.filter(escola_id=filtros["escola"])

    if filtros["status"]:
        contratos = contratos.filter(status=filtros["status"])

    total_contratos = contratos.count()
    valor_total = contratos.aggregate(total=Sum("valor_total")).get("total") or Decimal("0")

    return render(
        request,
        "documentos/relatorios_contratos_escola.html",
        {
            "certames": certames,
            "escolas": escolas,
            "status_choices": status_choices,
            "contratos": contratos,
            "filtros": filtros,
            "total_contratos": total_contratos,
            "valor_total": valor_total,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_contratos_por_fornecedor(request):
    certames = relatorios_certames_permitidos(request)
    fornecedores = relatorios_fornecedores_com_contratos_permitidos(request)
    status_choices = relatorios_status_contrato_choices()

    filtros = {
        "pregao": request.GET.get("pregao") or "",
        "fornecedor": request.GET.get("fornecedor") or "",
        "status": request.GET.get("status") or "",
    }

    contratos = relatorios_contratos_base_permitidos(request)

    if filtros["pregao"]:
        contratos = contratos.filter(pregao_id=filtros["pregao"])
        fornecedores = fornecedores.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()

    if filtros["fornecedor"]:
        contratos = contratos.filter(fornecedor_id=filtros["fornecedor"])

    if filtros["status"]:
        contratos = contratos.filter(status=filtros["status"])

    total_contratos = contratos.count()
    valor_total = contratos.aggregate(total=Sum("valor_total")).get("total") or Decimal("0")

    return render(
        request,
        "documentos/relatorios_contratos_fornecedor.html",
        {
            "certames": certames,
            "fornecedores": fornecedores,
            "status_choices": status_choices,
            "contratos": contratos,
            "filtros": filtros,
            "total_contratos": total_contratos,
            "valor_total": valor_total,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_montar_itens_sem_contrato(pregao, escola=None):
    linhas = []

    if escola:
        quantitativos = (
            QuantitativoEscola.objects.filter(
                pregao=pregao,
                escola=escola,
                quantidade__gt=0,
            )
            .select_related("item")
            .order_by("item__nome_item")
        )

        itens_adjudicados_chamada = None

        if getattr(pregao, "tipo_certame", "") == getattr(Pregao, "TIPO_CHAMADA_PUBLICA", "chamada_publica"):
            itens_adjudicados_chamada = set(
                ResultadoChamadaPublicaItem.objects.filter(
                    pregao=pregao,
                    status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
                ).values_list("item_id", flat=True)
            )

        for quantitativo in quantitativos:
            if itens_adjudicados_chamada is not None and quantitativo.item_id not in itens_adjudicados_chamada:
                continue

            contratado = relatorios_quantidade_contratada_item(
                pregao,
                quantitativo.item,
                escola=escola,
            )

            saldo = quantitativo.quantidade - contratado

            if saldo > 0:
                linhas.append(
                    {
                        "certame": pregao,
                        "item": quantitativo.item,
                        "unidade": relatorios_unidade_item(quantitativo.item),
                        "quantidade_base": quantitativo.quantidade,
                        "contratado": contratado,
                        "saldo": saldo,
                        "escola": escola,
                    }
                )

        return linhas

    if getattr(pregao, "tipo_certame", "") == getattr(Pregao, "TIPO_CHAMADA_PUBLICA", "chamada_publica"):
        resultados_adjudicados = (
            ResultadoChamadaPublicaItem.objects.filter(
                pregao=pregao,
                status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
            )
            .values("item")
            .annotate(quantidade_adjudicada_total=Sum("quantidade_adjudicada"))
            .order_by("item__nome_item")
        )

        itens_ids = [linha["item"] for linha in resultados_adjudicados]

        itens = {
            item.id: item
            for item in Item.objects.filter(id__in=itens_ids)
        }

        for resultado in resultados_adjudicados:
            item = itens.get(resultado["item"])

            if not item:
                continue

            quantidade_base = resultado["quantidade_adjudicada_total"] or Decimal("0")
            contratado = relatorios_quantidade_contratada_item(pregao, item)
            saldo = quantidade_base - contratado

            if saldo > 0:
                linhas.append(
                    {
                        "certame": pregao,
                        "item": item,
                        "unidade": relatorios_unidade_item(item),
                        "quantidade_base": quantidade_base,
                        "contratado": contratado,
                        "saldo": saldo,
                        "escola": None,
                    }
                )

        return linhas

    quantitativos = (
        QuantitativoPregao.objects.filter(
            pregao=pregao,
            quantidade__gt=0,
        )
        .select_related("item")
        .order_by("item__nome_item")
    )

    for quantitativo in quantitativos:
        contratado = relatorios_quantidade_contratada_item(pregao, quantitativo.item)
        saldo = quantitativo.quantidade - contratado

        if saldo > 0:
            linhas.append(
                {
                    "certame": pregao,
                    "item": quantitativo.item,
                    "unidade": relatorios_unidade_item(quantitativo.item),
                    "quantidade_base": quantitativo.quantidade,
                    "contratado": contratado,
                    "saldo": saldo,
                    "escola": None,
                }
            )

    return linhas


def relatorios_itens_sem_contrato(request):
    certames = relatorios_certames_permitidos(request)
    pregao_id = request.GET.get("pregao") or ""
    escola_id = request.GET.get("escola") or ""

    pregao = None
    escola = None
    escolas = Escola.objects.none()
    linhas = []

    if pregao_id:
        pregao = relatorios_certame_permitido_ou_404(request, pregao_id)
        escolas = relatorios_escolas_permitidas_por_certame(request, pregao)

        if relatorios_usuario_eh_consulta_escola(request):
            escola = relatorios_escola_vinculada_usuario(request)
        elif escola_id:
            escola = get_object_or_404(escolas, id=escola_id)

        linhas = relatorios_montar_itens_sem_contrato(pregao, escola=escola)

    total_itens = len(linhas)
    total_saldo = sum((linha["saldo"] for linha in linhas), Decimal("0"))

    return render(
        request,
        "documentos/relatorios_itens_sem_contrato.html",
        {
            "certames": certames,
            "pregao": pregao,
            "escolas": escolas,
            "escola": escola,
            "linhas": linhas,
            "total_itens": total_itens,
            "total_saldo": total_saldo,
            "filtros": {
                "pregao": pregao_id,
                "escola": escola_id,
            },
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )



def relatorios_escolas_contratos_por_certame(request, pregao_id):
    pregao = relatorios_certame_permitido_ou_404(request, pregao_id)

    escolas = Escola.objects.filter(
        contratos_gerados__pregao=pregao,
    )

    if relatorios_usuario_eh_consulta_escola(request):
        escola = relatorios_escola_vinculada_usuario(request)

        if not escola:
            escolas = escolas.none()
        else:
            escolas = escolas.filter(id=escola.id)

    escolas = (
        escolas
        .select_related("municipio")
        .distinct()
        .order_by("nome_escola")
    )

    dados = []

    for escola in escolas:
        dados.append(
            {
                "id": escola.id,
                "nome": escola.nome_escola,
                "municipio": escola.municipio.nome if escola.municipio else "",
                "uf": escola.municipio.uf if escola.municipio and escola.municipio.uf else "",
            }
        )

    return JsonResponse({"escolas": dados})


def relatorios_fornecedores_contratos_por_certame(request, pregao_id):
    pregao = relatorios_certame_permitido_ou_404(request, pregao_id)

    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__pregao=pregao,
    )

    if relatorios_usuario_eh_consulta_escola(request):
        escola = relatorios_escola_vinculada_usuario(request)

        if not escola:
            fornecedores = fornecedores.none()
        else:
            fornecedores = fornecedores.filter(contratos_gerados__escola=escola)

    fornecedores = fornecedores.distinct().order_by("razao_social")

    dados = []

    for fornecedor in fornecedores:
        dados.append(
            {
                "id": fornecedor.id,
                "nome": fornecedor.razao_social,
                "cnpj": fornecedor.cnpj if getattr(fornecedor, "cnpj", None) else "",
            }
        )

    return JsonResponse({"fornecedores": dados})



# ============================================================
# RELATÓRIOS GERENCIAIS - ETAPA 09.2
# ============================================================

def relatorios_status_registrado(modelo):
    return getattr(modelo, "STATUS_REGISTRADO", "registrado")


def relatorios_status_cancelado(modelo):
    return getattr(modelo, "STATUS_CANCELADO", "cancelado")


def relatorios_contratos_financeiros_permitidos(request):
    contratos = ContratoGerado.objects.select_related(
        "pregao",
        "escola",
        "escola__municipio",
        "fornecedor",
    ).all()

    return aplicar_restricao_escola_contratos(request, contratos)


def relatorios_aplicar_filtro_contrato_base(queryset, filtros):
    if filtros.get("pregao"):
        queryset = queryset.filter(pregao_id=filtros["pregao"])

    if filtros.get("escola"):
        queryset = queryset.filter(escola_id=filtros["escola"])

    if filtros.get("fornecedor"):
        queryset = queryset.filter(fornecedor_id=filtros["fornecedor"])

    return queryset


def relatorios_resumo_financeiro_certame(request):
    certames = relatorios_certames_permitidos(request)
    pregao_id = request.GET.get("pregao") or ""
    tipo_certame = request.GET.get("tipo_certame") or ""

    contratos = relatorios_contratos_financeiros_permitidos(request)

    if pregao_id:
        contratos = contratos.filter(pregao_id=pregao_id)

    if tipo_certame:
        contratos = contratos.filter(pregao__tipo_certame=tipo_certame)
        certames = certames.filter(tipo_certame=tipo_certame)

    contratos_validos = contratos.exclude(status=ContratoGerado.STATUS_CANCELADO)

    contratos_por_certame = {}

    for contrato in contratos_validos:
        chave = contrato.pregao_id

        if chave not in contratos_por_certame:
            contratos_por_certame[chave] = {
                "certame": contrato.pregao,
                "total_contratos": 0,
                "valor_contratado": Decimal("0"),
                "valor_distratado": Decimal("0"),
                "valor_realinhado": Decimal("0"),
                "valor_atual_estimado": Decimal("0"),
            }

        contratos_por_certame[chave]["total_contratos"] += 1
        contratos_por_certame[chave]["valor_contratado"] += contrato.valor_total or Decimal("0")

    distratos = DistratoContrato.objects.select_related(
        "contrato",
        "contrato__pregao",
        "contrato__escola",
    ).filter(
        contrato__in=contratos_validos,
    )

    for distrato in distratos:
        chave = distrato.contrato.pregao_id

        if chave in contratos_por_certame:
            contratos_por_certame[chave]["valor_distratado"] += distrato.valor_total or Decimal("0")

    realinhamentos = RealinhamentoPreco.objects.select_related(
        "contrato",
        "contrato__pregao",
        "contrato__escola",
    ).filter(
        contrato__in=contratos_validos,
        status=relatorios_status_registrado(RealinhamentoPreco),
    )

    for realinhamento in realinhamentos:
        chave = realinhamento.contrato.pregao_id

        if chave in contratos_por_certame:
            contratos_por_certame[chave]["valor_realinhado"] += realinhamento.diferenca_total or Decimal("0")

    linhas = list(contratos_por_certame.values())

    for linha in linhas:
        linha["valor_atual_estimado"] = (
            linha["valor_contratado"]
            + linha["valor_realinhado"]
            - linha["valor_distratado"]
        )

    linhas.sort(
        key=lambda linha: (
            -linha["certame"].ano,
            str(linha["certame"].numero),
        )
    )

    totais = {
        "contratos": sum((linha["total_contratos"] for linha in linhas), 0),
        "contratado": sum((linha["valor_contratado"] for linha in linhas), Decimal("0")),
        "distratado": sum((linha["valor_distratado"] for linha in linhas), Decimal("0")),
        "realinhado": sum((linha["valor_realinhado"] for linha in linhas), Decimal("0")),
        "atual": sum((linha["valor_atual_estimado"] for linha in linhas), Decimal("0")),
    }

    return render(
        request,
        "documentos/relatorios_resumo_financeiro_certame.html",
        {
            "certames": certames,
            "linhas": linhas,
            "totais": totais,
            "filtros": {
                "pregao": pregao_id,
                "tipo_certame": tipo_certame,
            },
            "tipo_certame_choices": Pregao.TIPO_CERTAME_CHOICES,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_resumo_financeiro_escola(request):
    certames = relatorios_certames_permitidos(request)
    escolas = relatorios_escolas_com_contratos_permitidas(request)

    filtros = {
        "pregao": request.GET.get("pregao") or "",
        "escola": request.GET.get("escola") or "",
    }

    contratos = relatorios_contratos_financeiros_permitidos(request).exclude(
        status=ContratoGerado.STATUS_CANCELADO
    )

    if filtros["pregao"]:
        contratos = contratos.filter(pregao_id=filtros["pregao"])
        escolas = escolas.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()

    if filtros["escola"] and not relatorios_usuario_eh_consulta_escola(request):
        contratos = contratos.filter(escola_id=filtros["escola"])

    contratos_por_escola = {}

    for contrato in contratos:
        chave = contrato.escola_id

        if chave not in contratos_por_escola:
            contratos_por_escola[chave] = {
                "escola": contrato.escola,
                "total_contratos": 0,
                "valor_contratado": Decimal("0"),
                "valor_distratado": Decimal("0"),
                "valor_realinhado": Decimal("0"),
                "valor_atual_estimado": Decimal("0"),
            }

        contratos_por_escola[chave]["total_contratos"] += 1
        contratos_por_escola[chave]["valor_contratado"] += contrato.valor_total or Decimal("0")

    distratos = DistratoContrato.objects.select_related(
        "contrato",
        "contrato__escola",
    ).filter(
        contrato__in=contratos,
    )

    for distrato in distratos:
        chave = distrato.contrato.escola_id

        if chave in contratos_por_escola:
            contratos_por_escola[chave]["valor_distratado"] += distrato.valor_total or Decimal("0")

    realinhamentos = RealinhamentoPreco.objects.select_related(
        "contrato",
        "contrato__escola",
    ).filter(
        contrato__in=contratos,
        status=relatorios_status_registrado(RealinhamentoPreco),
    )

    for realinhamento in realinhamentos:
        chave = realinhamento.contrato.escola_id

        if chave in contratos_por_escola:
            contratos_por_escola[chave]["valor_realinhado"] += realinhamento.diferenca_total or Decimal("0")

    linhas = list(contratos_por_escola.values())

    for linha in linhas:
        linha["valor_atual_estimado"] = (
            linha["valor_contratado"]
            + linha["valor_realinhado"]
            - linha["valor_distratado"]
        )

    linhas.sort(key=lambda linha: linha["escola"].nome_escola)

    totais = {
        "contratos": sum((linha["total_contratos"] for linha in linhas), 0),
        "contratado": sum((linha["valor_contratado"] for linha in linhas), Decimal("0")),
        "distratado": sum((linha["valor_distratado"] for linha in linhas), Decimal("0")),
        "realinhado": sum((linha["valor_realinhado"] for linha in linhas), Decimal("0")),
        "atual": sum((linha["valor_atual_estimado"] for linha in linhas), Decimal("0")),
    }

    return render(
        request,
        "documentos/relatorios_resumo_financeiro_escola.html",
        {
            "certames": certames,
            "escolas": escolas,
            "linhas": linhas,
            "totais": totais,
            "filtros": filtros,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_distratos(request):
    certames = relatorios_certames_permitidos(request)
    escolas = relatorios_escolas_com_contratos_permitidas(request)
    fornecedores = relatorios_fornecedores_com_contratos_permitidos(request)

    filtros = {
        "pregao": request.GET.get("pregao") or "",
        "escola": request.GET.get("escola") or "",
        "fornecedor": request.GET.get("fornecedor") or "",
        "tipo": request.GET.get("tipo") or "",
        "data_inicio": request.GET.get("data_inicio") or "",
        "data_fim": request.GET.get("data_fim") or "",
    }

    distratos = DistratoContrato.objects.select_related(
        "contrato",
        "contrato__pregao",
        "contrato__escola",
        "contrato__escola__municipio",
        "contrato__fornecedor",
    ).all()

    contratos_permitidos = relatorios_contratos_financeiros_permitidos(request)
    distratos = distratos.filter(contrato__in=contratos_permitidos)

    if filtros["pregao"]:
        distratos = distratos.filter(contrato__pregao_id=filtros["pregao"])
        escolas = escolas.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()
        fornecedores = fornecedores.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()

    if filtros["escola"] and not relatorios_usuario_eh_consulta_escola(request):
        distratos = distratos.filter(contrato__escola_id=filtros["escola"])

    if filtros["fornecedor"]:
        distratos = distratos.filter(contrato__fornecedor_id=filtros["fornecedor"])

    if filtros["tipo"]:
        distratos = distratos.filter(tipo=filtros["tipo"])

    if filtros["data_inicio"]:
        distratos = distratos.filter(data_distrato__gte=filtros["data_inicio"])

    if filtros["data_fim"]:
        distratos = distratos.filter(data_distrato__lte=filtros["data_fim"])

    distratos = distratos.order_by("-data_distrato", "-criado_em")

    totais = {
        "distratos": distratos.count(),
        "valor": distratos.aggregate(total=Sum("valor_total")).get("total") or Decimal("0"),
    }

    return render(
        request,
        "documentos/relatorios_distratos.html",
        {
            "certames": certames,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "distratos": distratos,
            "tipo_choices": DistratoContrato.TIPO_CHOICES,
            "filtros": filtros,
            "totais": totais,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_realinhamentos(request):
    certames = relatorios_certames_permitidos(request)
    escolas = relatorios_escolas_com_contratos_permitidas(request)
    fornecedores = relatorios_fornecedores_com_contratos_permitidos(request)

    filtros = {
        "pregao": request.GET.get("pregao") or "",
        "escola": request.GET.get("escola") or "",
        "fornecedor": request.GET.get("fornecedor") or "",
        "status": request.GET.get("status") or "",
        "termo": request.GET.get("termo") or "",
        "data_inicio": request.GET.get("data_inicio") or "",
        "data_fim": request.GET.get("data_fim") or "",
    }

    realinhamentos = RealinhamentoPreco.objects.select_related(
        "contrato",
        "contrato__pregao",
        "contrato__escola",
        "contrato__escola__municipio",
        "contrato__fornecedor",
    ).all()

    contratos_permitidos = relatorios_contratos_financeiros_permitidos(request)
    realinhamentos = realinhamentos.filter(contrato__in=contratos_permitidos)

    if filtros["pregao"]:
        realinhamentos = realinhamentos.filter(contrato__pregao_id=filtros["pregao"])
        escolas = escolas.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()
        fornecedores = fornecedores.filter(contratos_gerados__pregao_id=filtros["pregao"]).distinct()

    if filtros["escola"] and not relatorios_usuario_eh_consulta_escola(request):
        realinhamentos = realinhamentos.filter(contrato__escola_id=filtros["escola"])

    if filtros["fornecedor"]:
        realinhamentos = realinhamentos.filter(contrato__fornecedor_id=filtros["fornecedor"])

    if filtros["status"]:
        realinhamentos = realinhamentos.filter(status=filtros["status"])

    if filtros["termo"] == "com_termo":
        realinhamentos = realinhamentos.exclude(numero_termo_aditivo="")
    elif filtros["termo"] == "sem_termo":
        realinhamentos = realinhamentos.filter(numero_termo_aditivo="")

    if filtros["data_inicio"]:
        realinhamentos = realinhamentos.filter(data_realinhamento__gte=filtros["data_inicio"])

    if filtros["data_fim"]:
        realinhamentos = realinhamentos.filter(data_realinhamento__lte=filtros["data_fim"])

    realinhamentos = realinhamentos.order_by("-data_realinhamento", "-criado_em")

    totais = {
        "realinhamentos": realinhamentos.count(),
        "diferenca": realinhamentos.aggregate(total=Sum("diferenca_total")).get("total") or Decimal("0"),
    }

    return render(
        request,
        "documentos/relatorios_realinhamentos.html",
        {
            "certames": certames,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "realinhamentos": realinhamentos,
            "status_choices": RealinhamentoPreco.STATUS_CHOICES,
            "filtros": filtros,
            "totais": totais,
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )


def relatorios_consolidado_chamada_publica(request):
    chamadas = relatorios_certames_permitidos(request).filter(
        tipo_certame=Pregao.TIPO_CHAMADA_PUBLICA,
    )

    chamada_id = request.GET.get("pregao") or ""
    chamada = None
    linhas = []

    if chamada_id:
        chamada = get_object_or_404(chamadas, id=chamada_id)

        resultados = (
            ResultadoChamadaPublicaItem.objects.filter(
                pregao=chamada,
                status=ResultadoChamadaPublicaItem.STATUS_REGISTRADO,
            )
            .select_related("fornecedor", "item")
            .order_by("fornecedor__razao_social", "item__nome_item")
        )

        if relatorios_usuario_eh_consulta_escola(request):
            escola = relatorios_escola_vinculada_usuario(request)
            contratos_chamada = ContratoGerado.objects.filter(
                pregao=chamada,
                escola=escola,
            ).exclude(status=ContratoGerado.STATUS_CANCELADO)
        else:
            contratos_chamada = ContratoGerado.objects.filter(
                pregao=chamada,
            ).exclude(status=ContratoGerado.STATUS_CANCELADO)

        contratados_por_fornecedor_item = {}

        itens_contratados = ContratoItemGerado.objects.filter(
            contrato__in=contratos_chamada,
        ).select_related(
            "contrato",
            "contrato__fornecedor",
            "item",
        )

        for contrato_item in itens_contratados:
            chave = (contrato_item.contrato.fornecedor_id, contrato_item.item_id)

            if chave not in contratados_por_fornecedor_item:
                contratados_por_fornecedor_item[chave] = {
                    "quantidade": Decimal("0"),
                    "valor": Decimal("0"),
                }

            contratados_por_fornecedor_item[chave]["quantidade"] += contrato_item.quantidade_contratada or Decimal("0")
            contratados_por_fornecedor_item[chave]["valor"] += contrato_item.valor_total or Decimal("0")

        for resultado in resultados:
            chave = (resultado.fornecedor_id, resultado.item_id)
            contratado = contratados_por_fornecedor_item.get(
                chave,
                {
                    "quantidade": Decimal("0"),
                    "valor": Decimal("0"),
                },
            )

            saldo_quantidade = (resultado.quantidade_adjudicada or Decimal("0")) - contratado["quantidade"]
            saldo_valor = (resultado.valor_total or Decimal("0")) - contratado["valor"]

            linhas.append(
                {
                    "fornecedor": resultado.fornecedor,
                    "item": resultado.item,
                    "unidade": relatorios_unidade_item(resultado.item),
                    "quantidade_adjudicada": resultado.quantidade_adjudicada or Decimal("0"),
                    "valor_adjudicado": resultado.valor_total or Decimal("0"),
                    "quantidade_contratada": contratado["quantidade"],
                    "valor_contratado": contratado["valor"],
                    "saldo_quantidade": saldo_quantidade,
                    "saldo_valor": saldo_valor,
                }
            )

    totais = {
        "adjudicado": sum((linha["valor_adjudicado"] for linha in linhas), Decimal("0")),
        "contratado": sum((linha["valor_contratado"] for linha in linhas), Decimal("0")),
        "saldo": sum((linha["saldo_valor"] for linha in linhas), Decimal("0")),
    }

    return render(
        request,
        "documentos/relatorios_consolidado_chamada_publica.html",
        {
            "chamadas": chamadas,
            "chamada": chamada,
            "linhas": linhas,
            "totais": totais,
            "filtros": {
                "pregao": chamada_id,
            },
            "restrito_escola": relatorios_usuario_eh_consulta_escola(request),
            "escola_vinculada": relatorios_escola_vinculada_usuario(request),
        },
    )
