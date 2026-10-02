import base64
import re
import unicodedata
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone

from apps.cadastros.models import Escola, Fornecedor
from apps.documentos.models import ContratoGerado, ContratoItemGerado
from apps.pregoes.models import Pregao

from .forms import AquisicaoNotaFiscalForm
from .models import AquisicaoNotaFiscal, AquisicaoNotaFiscalItem
from .consulta_danfe import ConsultaDanfeErro, consultar_xml_consultadanfe


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


def aquisicoes_permitidas(request):
    notas = AquisicaoNotaFiscal.objects.select_related(
        "pregao",
        "escola",
        "escola__municipio",
        "fornecedor",
        "contrato",
        "criado_por",
    ).all()

    if usuario_eh_consulta_escola(request):
        escola = escola_vinculada_usuario(request)

        if not escola:
            return notas.none()

        notas = notas.filter(escola=escola)

    return notas


def contratos_permitidos(request):
    contratos = ContratoGerado.objects.exclude(
        status=ContratoGerado.STATUS_CANCELADO
    ).select_related("pregao", "escola", "fornecedor")

    if usuario_eh_consulta_escola(request):
        escola = escola_vinculada_usuario(request)

        if not escola:
            return contratos.none()

        contratos = contratos.filter(escola=escola)

    return contratos


@login_required
def notas_fiscais(request):
    filtros = {
        "pregao": request.GET.get("pregao") or "",
        "escola": request.GET.get("escola") or "",
        "fornecedor": request.GET.get("fornecedor") or "",
        "status": request.GET.get("status") or "",
        "busca": request.GET.get("busca") or "",
    }

    notas = aquisicoes_permitidas(request)

    if filtros["pregao"]:
        notas = notas.filter(pregao_id=filtros["pregao"])

    if filtros["escola"] and not usuario_eh_consulta_escola(request):
        notas = notas.filter(escola_id=filtros["escola"])

    if filtros["fornecedor"]:
        notas = notas.filter(fornecedor_id=filtros["fornecedor"])

    if filtros["status"]:
        notas = notas.filter(status=filtros["status"])

    if filtros["busca"]:
        busca = filtros["busca"].strip()
        notas = notas.filter(
            models.Q(chave_acesso__icontains=busca)
            | models.Q(numero_nota__icontains=busca)
            | models.Q(fornecedor__razao_social__icontains=busca)
            | models.Q(escola__nome_escola__icontains=busca)
        )

    notas = notas.order_by("-data_recebimento", "-criado_em")

    contratos_base = contratos_permitidos(request)

    certames = (
        Pregao.objects.filter(
            contratos_gerados__in=contratos_base
        )
        .distinct()
        .prefetch_related("municipios")
        .order_by("-ano", "-numero")
    )

    escolas = Escola.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("nome_escola")

    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("razao_social")

    totais = {
        "total": notas.count(),
        "confirmada": notas.filter(status=AquisicaoNotaFiscal.STATUS_CONFIRMADA).count(),
        "editada": notas.filter(status=AquisicaoNotaFiscal.STATUS_EDITADA).count(),
        "cancelada": notas.filter(status=AquisicaoNotaFiscal.STATUS_CANCELADA).count(),
    }

    status_choices = [
        (AquisicaoNotaFiscal.STATUS_CONFIRMADA, "Confirmada"),
        (AquisicaoNotaFiscal.STATUS_EDITADA, "Editada"),
        (AquisicaoNotaFiscal.STATUS_CANCELADA, "Cancelada"),
    ]

    return render(
        request,
        "aquisicoes/notas_fiscais.html",
        {
            "notas": notas,
            "certames": certames,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "status_choices": status_choices,
            "filtros": filtros,
            "totais": totais,
            "restrito_escola": usuario_eh_consulta_escola(request),
            "escola_vinculada": escola_vinculada_usuario(request),
        },
    )


@login_required
def nota_fiscal_nova(request):
    escola_vinculada = escola_vinculada_usuario(request) if usuario_eh_consulta_escola(request) else None

    if usuario_eh_consulta_escola(request) and not escola_vinculada:
        messages.error(request, "Seu usuário não possui escola vinculada. Solicite o vínculo ao administrador.")
        return redirect("aquisicoes:notas_fiscais")

    if request.method == "POST":
        form = AquisicaoNotaFiscalForm(
            request.POST,
            request.FILES,
            request=request,
            escola_vinculada=escola_vinculada,
        )

        if form.is_valid():
            nota = form.save(commit=False)

            if escola_vinculada:
                nota.escola = escola_vinculada

            nota.criado_por = request.user

            if nota.metodo_entrada in [
                AquisicaoNotaFiscal.METODO_XML,
                AquisicaoNotaFiscal.METODO_TABELA_COLADA,
            ]:
                nota.status = AquisicaoNotaFiscal.STATUS_EM_CONFERENCIA
            else:
                nota.status = AquisicaoNotaFiscal.STATUS_RASCUNHO

            nota.save()
            messages.success(request, "Nota fiscal registrada com sucesso. A inclusão dos itens será feita nas próximas etapas.")
            return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)
    else:
        form = AquisicaoNotaFiscalForm(
            request=request,
            escola_vinculada=escola_vinculada,
        )

    return render(
        request,
        "aquisicoes/nota_fiscal_form.html",
        {
            "form": form,
            "titulo": "Registrar Aquisição por NF-e",
            "subtitulo": "Cadastre a nota fiscal e escolha a forma de entrada dos itens.",
            "modo": "nova",
        },
    )


@login_required
def nota_fiscal_editar(request, nota_id):
    nota = get_object_or_404(aquisicoes_permitidas(request), id=nota_id)

    if nota.status == AquisicaoNotaFiscal.STATUS_CANCELADA:
        messages.warning(request, "Notas canceladas não podem ser editadas.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    metodos_editaveis = [
        AquisicaoNotaFiscal.METODO_MANUAL,
        AquisicaoNotaFiscal.METODO_XML,
        AquisicaoNotaFiscal.METODO_CHAVE_ACESSO,
    ]

    if nota.metodo_entrada not in metodos_editaveis:
        messages.warning(
            request,
            "A edição completa nesta tela está disponível para notas lançadas manualmente, importadas por XML ou consultadas pela chave de acesso.",
        )
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    escola_vinculada = escola_vinculada_usuario(request) if usuario_eh_consulta_escola(request) else None

    if usuario_eh_consulta_escola(request) and not escola_vinculada:
        messages.error(request, "Seu usuário não possui escola vinculada. Solicite o vínculo ao administrador.")
        return redirect("aquisicoes:notas_fiscais")

    contratos_base = contratos_permitidos(request)

    if request.method == "POST":
        contrato_id = request.POST.get("contrato")
        numero_nota = (request.POST.get("numero_nota") or "").strip()
        observacoes = request.POST.get("observacoes") or ""

        if not numero_nota:
            messages.error(request, "Informe o número da NF-e.")
            return redirect("aquisicoes:nota_fiscal_editar", nota_id=nota.id)

        if not contrato_id:
            messages.error(request, "Selecione o contrato.")
            return redirect("aquisicoes:nota_fiscal_editar", nota_id=nota.id)

        contrato = get_object_or_404(contratos_base, id=contrato_id)
        itens_validos, erro = extrair_itens_aquisicao_manual(request, contrato)

        if erro:
            messages.error(request, erro)
            return redirect("aquisicoes:nota_fiscal_editar", nota_id=nota.id)

        total_nota = sum(
            (item["valor_total"] for item in itens_validos),
            Decimal("0"),
        )

        with transaction.atomic():
            nota.pregao = contrato.pregao
            nota.escola = contrato.escola
            nota.fornecedor = contrato.fornecedor
            nota.contrato = contrato
            nota.numero_nota = numero_nota
            nota.observacoes = observacoes
            nota.valor_total = total_nota
            nota.status = AquisicaoNotaFiscal.STATUS_EDITADA

            if not nota.confirmado_por_id:
                nota.confirmado_por = request.user

            if not nota.confirmado_em:
                nota.confirmado_em = timezone.now()

            nota.save(update_fields=[
                "pregao",
                "escola",
                "fornecedor",
                "contrato",
                "numero_nota",
                "observacoes",
                "valor_total",
                "status",
                "confirmado_por",
                "confirmado_em",
                "atualizado_em",
            ])

            nota.itens.all().delete()
            criar_itens_aquisicao_manual(nota, itens_validos)

        messages.success(
            request,
            f"NF-e {nota.numero_nota} atualizada com sucesso. Status alterado para Editada.",
        )
        return redirect("aquisicoes:notas_fiscais")

    contexto = montar_contexto_aquisicao_manual(request, nota=nota)
    return render(request, "aquisicoes/aquisicao_manual_form.html", contexto)


@login_required
def nota_fiscal_detalhe(request, nota_id):
    nota = get_object_or_404(aquisicoes_permitidas(request), id=nota_id)
    itens = nota.itens.select_related("item", "contrato_item", "contrato_item__item").order_by("descricao_produto")

    return render(
        request,
        "aquisicoes/nota_fiscal_detalhe.html",
        {
            "nota": nota,
            "itens": itens,
        },
    )

@login_required
def nota_fiscal_cancelar(request, nota_id):
    nota = get_object_or_404(aquisicoes_permitidas(request), id=nota_id)

    if nota.status == AquisicaoNotaFiscal.STATUS_CANCELADA:
        messages.info(request, "Esta nota fiscal já está cancelada.")
        return redirect("aquisicoes:notas_fiscais")

    if request.method == "POST":
        nota.status = AquisicaoNotaFiscal.STATUS_CANCELADA
        nota.save(update_fields=["status", "atualizado_em"])
        messages.success(request, f"NF-e {nota.numero_nota or nota.id} cancelada com sucesso.")
        return redirect("aquisicoes:notas_fiscais")

    return render(request, "aquisicoes/nota_fiscal_cancelar.html", {"nota": nota})


@login_required
def nota_fiscal_confirmar(request, nota_id):
    nota = get_object_or_404(aquisicoes_permitidas(request), id=nota_id)

    if nota.status == AquisicaoNotaFiscal.STATUS_CONFIRMADA:
        messages.info(request, "Esta nota fiscal já está confirmada.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    if nota.status == AquisicaoNotaFiscal.STATUS_CANCELADA:
        messages.error(request, "Notas canceladas não podem ser confirmadas.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    if not nota.itens.exists():
        messages.error(request, "Não é possível confirmar uma nota fiscal sem itens.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    if request.method == "POST":
        nota.valor_total = nota.valor_itens
        nota.status = AquisicaoNotaFiscal.STATUS_CONFIRMADA
        nota.confirmado_por = request.user
        nota.confirmado_em = timezone.now()
        nota.save(update_fields=[
            "valor_total",
            "status",
            "confirmado_por",
            "confirmado_em",
            "atualizado_em",
        ])
        messages.success(request, "Aquisição confirmada com sucesso.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    itens = nota.itens.select_related("item", "contrato_item", "contrato_item__item").order_by("descricao_produto")
    return render(request, "aquisicoes/nota_fiscal_confirmar.html", {"nota": nota, "itens": itens})

# ============================================================
# ETAPA 11.2 AJUSTADA — ADIÇÃO MANUAL SIMPLIFICADA
# ============================================================

def decimal_br_para_decimal(valor, padrao=Decimal("0")):
    if valor is None:
        return padrao
    valor = str(valor).strip()
    if not valor:
        return padrao
    valor = valor.replace("R$", "").replace(" ", "")
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    try:
        return Decimal(valor)
    except (InvalidOperation, ValueError):
        return padrao


def contratos_json_permitidos(request):
    contratos = contratos_permitidos(request).order_by(
        "-pregao__ano", "pregao__numero", "escola__nome_escola", "fornecedor__razao_social"
    )
    return [
        {
            "id": contrato.id,
            "pregao_id": contrato.pregao_id,
            "escola_id": contrato.escola_id,
            "fornecedor_id": contrato.fornecedor_id,
            "numero": contrato.numero_contrato or f"Contrato #{contrato.id}",
            "texto": f"{contrato.numero_contrato or 'Contrato'} - {contrato.escola.nome_escola} - {contrato.fornecedor.razao_social}",
        }
        for contrato in contratos
    ]


def extrair_itens_aquisicao_manual(request, contrato):
    contrato_item_ids = request.POST.getlist("contrato_item[]")
    descricoes = request.POST.getlist("descricao_produto[]")
    quantidades = request.POST.getlist("quantidade[]")
    valores_unitarios = request.POST.getlist("valor_unitario[]")
    valores_totais = request.POST.getlist("valor_total[]")

    itens_validos = []

    for indice, contrato_item_id in enumerate(contrato_item_ids):
        contrato_item_id = (contrato_item_id or "").strip()
        descricao = descricoes[indice].strip() if indice < len(descricoes) else ""
        quantidade = decimal_br_para_decimal(
            quantidades[indice] if indice < len(quantidades) else ""
        )
        valor_unitario = decimal_br_para_decimal(
            valores_unitarios[indice] if indice < len(valores_unitarios) else ""
        )
        valor_total = decimal_br_para_decimal(
            valores_totais[indice] if indice < len(valores_totais) else ""
        )

        if not contrato_item_id and quantidade == 0 and valor_unitario == 0 and valor_total == 0:
            continue

        if not contrato_item_id:
            return None, f"Selecione o item do contrato na linha {indice + 1}."

        contrato_item = get_object_or_404(
            ContratoItemGerado.objects.select_related("contrato", "item"),
            id=contrato_item_id,
            contrato=contrato,
        )

        if quantidade <= 0:
            return None, f"Informe uma quantidade maior que zero na linha {indice + 1}."

        if valor_unitario <= 0:
            valor_unitario = contrato_item.valor_unitario or Decimal("0")

        valor_calculado = (quantidade * valor_unitario).quantize(Decimal("0.01"))

        if valor_total <= 0:
            valor_total = valor_calculado

        if abs(valor_total - valor_calculado) > Decimal("0.05"):
            return None, (
                f"O valor total da linha {indice + 1} está diferente de "
                "quantidade x valor unitário."
            )

        itens_validos.append({
            "contrato_item": contrato_item,
            "descricao": descricao or contrato_item.item.nome_item,
            "unidade": contrato_item.unidade or "",
            "quantidade": quantidade,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total,
        })

    if not itens_validos:
        return None, "Adicione pelo menos um item da nota fiscal."

    return itens_validos, None


def criar_itens_aquisicao_manual(nota, itens_validos):
    for dados in itens_validos:
        AquisicaoNotaFiscalItem.objects.create(
            nota=nota,
            contrato_item=dados["contrato_item"],
            item=dados["contrato_item"].item,
            descricao_produto=dados["descricao"],
            unidade=dados["unidade"],
            quantidade=dados["quantidade"],
            valor_unitario=dados["valor_unitario"],
            valor_total=dados["valor_total"],
            status=AquisicaoNotaFiscalItem.STATUS_VINCULADO,
            conferido=True,
        )


def montar_contexto_aquisicao_manual(request, nota=None):
    escola_vinculada = escola_vinculada_usuario(request) if usuario_eh_consulta_escola(request) else None
    contratos_base = contratos_permitidos(request)

    certames = (
        Pregao.objects.filter(contratos_gerados__in=contratos_base)
        .distinct()
        .prefetch_related("municipios")
        .order_by("-ano", "-numero")
    )
    escolas = Escola.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("nome_escola")
    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("razao_social")
    contratos = contratos_base.order_by(
        "-pregao__ano",
        "pregao__numero",
        "escola__nome_escola",
        "fornecedor__razao_social",
    )

    itens_iniciais = []

    if nota:
        for item_nota in nota.itens.select_related(
            "contrato_item",
            "contrato_item__item",
        ).order_by("descricao_produto"):
            if not item_nota.contrato_item:
                continue

            contrato_item = item_nota.contrato_item
            itens_iniciais.append({
                "contrato_item_id": contrato_item.id,
                "texto": (
                    f"{contrato_item.item.nome_item} | "
                    f"{contrato_item.unidade or '-'} | "
                    f"R$ {item_nota.valor_unitario}"
                ),
                "unidade": item_nota.unidade or contrato_item.unidade or "",
                "quantidade": format(item_nota.quantidade, "f"),
                "valor_unitario": format(item_nota.valor_unitario, "f"),
                "valor_total": format(item_nota.valor_total, "f"),
            })

    return {
        "certames": certames,
        "escolas": escolas,
        "fornecedores": fornecedores,
        "contratos": contratos,
        "contratos_json": contratos_json_permitidos(request),
        "restrito_escola": usuario_eh_consulta_escola(request),
        "escola_vinculada": escola_vinculada,
        "modo_edicao": bool(nota),
        "nota": nota,
        "itens_iniciais": itens_iniciais,
    }


@login_required
def aquisicao_manual_nova(request):
    escola_vinculada = escola_vinculada_usuario(request) if usuario_eh_consulta_escola(request) else None

    if usuario_eh_consulta_escola(request) and not escola_vinculada:
        messages.error(request, "Seu usuário não possui escola vinculada. Solicite o vínculo ao administrador.")
        return redirect("aquisicoes:notas_fiscais")

    contratos_base = contratos_permitidos(request)

    if request.method == "POST":
        contrato_id = request.POST.get("contrato")
        numero_nota = (request.POST.get("numero_nota") or "").strip()
        observacoes = request.POST.get("observacoes") or ""

        if not numero_nota:
            messages.error(request, "Informe o número da NF-e.")
            return redirect("aquisicoes:aquisicao_manual_nova")

        if not contrato_id:
            messages.error(request, "Selecione o contrato para registrar a aquisição.")
            return redirect("aquisicoes:aquisicao_manual_nova")

        contrato = get_object_or_404(contratos_base, id=contrato_id)
        itens_validos, erro = extrair_itens_aquisicao_manual(request, contrato)

        if erro:
            messages.error(request, erro)
            return redirect("aquisicoes:aquisicao_manual_nova")

        total_nota = sum(
            (item["valor_total"] for item in itens_validos),
            Decimal("0"),
        )

        with transaction.atomic():
            nota = AquisicaoNotaFiscal.objects.create(
                pregao=contrato.pregao,
                escola=contrato.escola,
                fornecedor=contrato.fornecedor,
                contrato=contrato,
                metodo_entrada=AquisicaoNotaFiscal.METODO_MANUAL,
                chave_acesso="",
                numero_nota=numero_nota,
                serie="",
                observacoes=observacoes,
                status=AquisicaoNotaFiscal.STATUS_CONFIRMADA,
                criado_por=request.user,
                confirmado_por=request.user,
                confirmado_em=timezone.now(),
                valor_total=total_nota,
            )

            criar_itens_aquisicao_manual(nota, itens_validos)

        messages.success(
            request,
            f"NF-e {nota.numero_nota} registrada e confirmada com sucesso. Você já pode lançar a próxima nota.",
        )
        return redirect("aquisicoes:aquisicao_manual_nova")

    contexto = montar_contexto_aquisicao_manual(request)
    return render(request, "aquisicoes/aquisicao_manual_form.html", contexto)


@login_required
def ajax_itens_contrato(request, contrato_id):
    contrato = get_object_or_404(contratos_permitidos(request), id=contrato_id)
    itens = contrato.itens.select_related("item").order_by("item__nome_item")
    dados = [
        {
            "id": contrato_item.id,
            "item_id": contrato_item.item_id,
            "nome": contrato_item.item.nome_item,
            "marca": contrato_item.marca or "",
            "unidade": contrato_item.unidade or "",
            "quantidade_contratada": str(contrato_item.quantidade_contratada),
            "valor_unitario": str(contrato_item.valor_unitario),
            "texto": f"{contrato_item.item.nome_item} | {contrato_item.unidade or '-'} | R$ {contrato_item.valor_unitario}",
        }
        for contrato_item in itens
    ]
    return JsonResponse({"itens": dados})


@login_required
def upload_xml_em_breve(request):
    return render(request, "aquisicoes/opcao_em_breve.html", {
        "titulo": "Upload XML da NF-e",
        "subtitulo": "Esta opção será implementada na próxima etapa.",
        "mensagem": "Aqui o usuário fará upload do arquivo XML da NF-e para leitura automática dos produtos.",
    })


@login_required
def colar_tabela_em_breve(request):
    return render(request, "aquisicoes/opcao_em_breve.html", {
        "titulo": "Colar Tabela Copiada",
        "subtitulo": "Esta opção será implementada após o upload XML.",
        "mensagem": "Aqui o usuário colará a tabela copiada da aba Produtos e Serviços do portal da NF-e.",
    })


# ============================================================
# ETAPA 11.2 — AJAX PARA FORMULÁRIO MANUAL SIMPLIFICADO
# ============================================================

@login_required
def ajax_escolas_por_certame(request, pregao_id):
    contratos = contratos_permitidos(request).filter(pregao_id=pregao_id)

    escolas = Escola.objects.filter(
        contratos_gerados__in=contratos
    ).distinct().order_by("nome_escola")

    dados = [
        {
            "id": escola.id,
            "nome": escola.nome_escola,
            "municipio": escola.municipio.nome if getattr(escola, "municipio", None) else "",
        }
        for escola in escolas
    ]

    return JsonResponse({"escolas": dados})


@login_required
def ajax_fornecedores_por_certame_escola(request, pregao_id, escola_id):
    contratos = contratos_permitidos(request).filter(
        pregao_id=pregao_id,
        escola_id=escola_id,
    )

    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__in=contratos
    ).distinct().order_by("razao_social")

    dados = [
        {
            "id": fornecedor.id,
            "nome": fornecedor.razao_social,
            "documento": getattr(fornecedor, "cnpj", "") or getattr(fornecedor, "cpf", ""),
        }
        for fornecedor in fornecedores
    ]

    return JsonResponse({"fornecedores": dados})


@login_required
def ajax_contratos_por_certame_escola_fornecedor(request, pregao_id, escola_id, fornecedor_id):
    contratos = contratos_permitidos(request).filter(
        pregao_id=pregao_id,
        escola_id=escola_id,
        fornecedor_id=fornecedor_id,
    ).order_by("numero_contrato", "id")

    dados = [
        {
            "id": contrato.id,
            "numero": contrato.numero_contrato or f"Contrato #{contrato.id}",
            "valor_total": str(contrato.valor_total),
            "texto": f"{contrato.numero_contrato or 'Contrato'} - R$ {contrato.valor_total}",
        }
        for contrato in contratos
    ]

    return JsonResponse({"contratos": dados})


# ============================================================
# ETAPA 11.3 — RELATÓRIO DE SALDO DE AQUISIÇÕES
# ============================================================

@login_required
def relatorio_saldo_aquisicoes(request):
    filtros = {
        "pregao": request.GET.get("pregao") or "",
        "escola": request.GET.get("escola") or "",
        "fornecedor": request.GET.get("fornecedor") or "",
        "contrato": request.GET.get("contrato") or "",
        "somente_saldo": request.GET.get("somente_saldo") or "",
    }

    contratos_base = contratos_permitidos(request).select_related(
        "pregao",
        "escola",
        "fornecedor",
    ).exclude(
        status=ContratoGerado.STATUS_CANCELADO,
    )

    if filtros["pregao"]:
        contratos_base = contratos_base.filter(pregao_id=filtros["pregao"])

    if filtros["escola"] and not usuario_eh_consulta_escola(request):
        contratos_base = contratos_base.filter(escola_id=filtros["escola"])

    if filtros["fornecedor"]:
        contratos_base = contratos_base.filter(fornecedor_id=filtros["fornecedor"])

    if filtros["contrato"]:
        contratos_base = contratos_base.filter(id=filtros["contrato"])

    contratos = contratos_base.order_by(
        "-pregao__ano",
        "pregao__numero",
        "escola__nome_escola",
        "fornecedor__razao_social",
    )

    status_notas_validas = [
        AquisicaoNotaFiscal.STATUS_CONFIRMADA,
        AquisicaoNotaFiscal.STATUS_EDITADA,
    ]
    linhas = []

    totais = {
        "valor_contratado": Decimal("0"),
        "valor_adquirido": Decimal("0"),
        "saldo_financeiro": Decimal("0"),
        "itens": 0,
        "itens_com_saldo": 0,
        "itens_sem_saldo": 0,
    }

    for contrato in contratos:
        valor_adquirido_contrato = AquisicaoNotaFiscal.objects.filter(
            contrato=contrato,
            status__in=status_notas_validas,
        ).aggregate(total=Sum("valor_total")).get("total") or Decimal("0")

        saldo_financeiro_contrato = (contrato.valor_total or Decimal("0")) - valor_adquirido_contrato

        itens_contrato = contrato.itens.select_related("item").order_by("item__nome_item")

        for contrato_item in itens_contrato:
            aquisicoes_item = AquisicaoNotaFiscalItem.objects.filter(
                nota__contrato=contrato,
                nota__status__in=status_notas_validas,
                contrato_item=contrato_item,
            )

            quantidade_adquirida = aquisicoes_item.aggregate(total=Sum("quantidade")).get("total") or Decimal("0")
            valor_adquirido_item = aquisicoes_item.aggregate(total=Sum("valor_total")).get("total") or Decimal("0")

            quantidade_contratada = contrato_item.quantidade_contratada or Decimal("0")
            valor_contratado_item = contrato_item.valor_total or Decimal("0")
            saldo_quantidade = quantidade_contratada - quantidade_adquirida
            saldo_valor_item = valor_contratado_item - valor_adquirido_item

            if filtros["somente_saldo"] == "1" and saldo_quantidade <= 0:
                continue

            percentual_consumido = Decimal("0")
            if quantidade_contratada > 0:
                percentual_consumido = (quantidade_adquirida / quantidade_contratada) * Decimal("100")

            linhas.append({
                "contrato": contrato,
                "contrato_item": contrato_item,
                "item": contrato_item.item,
                "unidade": contrato_item.unidade,
                "quantidade_contratada": quantidade_contratada,
                "quantidade_adquirida": quantidade_adquirida,
                "saldo_quantidade": saldo_quantidade,
                "valor_unitario": contrato_item.valor_unitario,
                "valor_contratado_item": valor_contratado_item,
                "valor_adquirido_item": valor_adquirido_item,
                "saldo_valor_item": saldo_valor_item,
                "valor_contratado_contrato": contrato.valor_total or Decimal("0"),
                "valor_adquirido_contrato": valor_adquirido_contrato,
                "saldo_financeiro_contrato": saldo_financeiro_contrato,
                "percentual_consumido": percentual_consumido,
            })

            totais["valor_contratado"] += valor_contratado_item
            totais["valor_adquirido"] += valor_adquirido_item
            totais["saldo_financeiro"] += saldo_valor_item
            totais["itens"] += 1

            if saldo_quantidade > 0:
                totais["itens_com_saldo"] += 1
            else:
                totais["itens_sem_saldo"] += 1

    contratos_permitidos_base = contratos_permitidos(request)

    certames = (
        Pregao.objects.filter(
            contratos_gerados__in=contratos_permitidos_base
        )
        .distinct()
        .prefetch_related("municipios")
        .order_by("-ano", "-numero")
    )

    escolas = Escola.objects.filter(
        contratos_gerados__in=contratos_permitidos_base
    ).distinct().order_by("nome_escola")

    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__in=contratos_permitidos_base
    ).distinct().order_by("razao_social")

    contratos_filtro = contratos_permitidos_base.select_related(
        "pregao",
        "escola",
        "fornecedor",
    ).order_by("-pregao__ano", "pregao__numero", "escola__nome_escola")

    return render(
        request,
        "aquisicoes/relatorio_saldo_aquisicoes.html",
        {
            "linhas": linhas,
            "totais": totais,
            "certames": certames,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "contratos": contratos_filtro,
            "filtros": filtros,
            "restrito_escola": usuario_eh_consulta_escola(request),
            "escola_vinculada": escola_vinculada_usuario(request),
        },
    )


# ============================================================
# ETAPA 11.4/11.5 — UPLOAD XML E COLAR TABELA
# ============================================================

def normalizar_texto_aquisicao(valor):
    valor = str(valor or "").strip().upper()
    valor = unicodedata.normalize("NFKD", valor)
    valor = "".join(ch for ch in valor if not unicodedata.combining(ch))
    valor = re.sub(r"[^A-Z0-9 ]+", " ", valor)
    valor = re.sub(r"\s+", " ", valor).strip()
    return valor


def data_nfe_para_date(valor):
    valor = str(valor or "").strip()
    if not valor:
        return None

    # dhEmi: 2026-08-01T10:20:00-04:00
    if "T" in valor:
        valor = valor.split("T")[0]

    return valor or None


def buscar_contrato_item_por_descricao(contrato, descricao):
    descricao_norm = normalizar_texto_aquisicao(descricao)

    if not descricao_norm:
        return None

    itens = contrato.itens.select_related("item").all()

    # 1) correspondência direta mais segura
    for contrato_item in itens:
        nome_item = normalizar_texto_aquisicao(contrato_item.item.nome_item)
        if nome_item and (nome_item in descricao_norm or descricao_norm in nome_item):
            return contrato_item

    # 2) correspondência por palavras relevantes
    palavras_desc = set(p for p in descricao_norm.split() if len(p) >= 4)

    melhor_item = None
    melhor_pontuacao = 0

    for contrato_item in itens:
        nome_item = normalizar_texto_aquisicao(contrato_item.item.nome_item)
        palavras_item = set(p for p in nome_item.split() if len(p) >= 4)

        if not palavras_item:
            continue

        pontuacao = len(palavras_desc.intersection(palavras_item))

        if pontuacao > melhor_pontuacao:
            melhor_pontuacao = pontuacao
            melhor_item = contrato_item

    return melhor_item if melhor_pontuacao > 0 else None


def extrair_dados_xml_nfe(arquivo_xml):
    conteudo = arquivo_xml.read()

    if isinstance(conteudo, bytes):
        conteudo = conteudo.decode("utf-8", errors="ignore")

    raiz = ET.fromstring(conteudo)

    def limpar_tag(tag):
        return tag.split("}", 1)[-1] if "}" in tag else tag

    def primeiro_texto(elemento, nome_tag):
        if elemento is None:
            return ""

        for filho in elemento.iter():
            if limpar_tag(filho.tag) == nome_tag:
                return (filho.text or "").strip()

        return ""

    inf_nfe = None

    for elemento in raiz.iter():
        if limpar_tag(elemento.tag) == "infNFe":
            inf_nfe = elemento
            break

    if inf_nfe is None:
        raise ValueError("Não foi possível localizar a tag infNFe no XML. Verifique se o arquivo é uma NF-e autorizada.")

    chave = (inf_nfe.attrib.get("Id") or "").replace("NFe", "").strip()

    ide = None
    emit = None
    dest = None

    for filho in list(inf_nfe):
        tag = limpar_tag(filho.tag)

        if tag == "ide":
            ide = filho
        elif tag == "emit":
            emit = filho
        elif tag == "dest":
            dest = filho

    numero = primeiro_texto(ide, "nNF")
    serie = primeiro_texto(ide, "serie")
    data_emissao = data_nfe_para_date(primeiro_texto(ide, "dhEmi") or primeiro_texto(ide, "dEmi"))

    emitente = {
        "cnpj": primeiro_texto(emit, "CNPJ"),
        "cpf": primeiro_texto(emit, "CPF"),
        "nome": primeiro_texto(emit, "xNome"),
    }

    destinatario = {
        "cnpj": primeiro_texto(dest, "CNPJ"),
        "cpf": primeiro_texto(dest, "CPF"),
        "nome": primeiro_texto(dest, "xNome"),
    }

    itens = []

    for det in inf_nfe.iter():
        if limpar_tag(det.tag) != "det":
            continue

        prod = None
        for filho in list(det):
            if limpar_tag(filho.tag) == "prod":
                prod = filho
                break

        if prod is None:
            continue

        descricao = primeiro_texto(prod, "xProd")
        codigo = primeiro_texto(prod, "cProd")
        unidade = primeiro_texto(prod, "uCom") or primeiro_texto(prod, "uTrib")
        quantidade = decimal_br_para_decimal(primeiro_texto(prod, "qCom") or primeiro_texto(prod, "qTrib"))
        valor_unitario = decimal_br_para_decimal(primeiro_texto(prod, "vUnCom") or primeiro_texto(prod, "vUnTrib"))
        valor_total = decimal_br_para_decimal(primeiro_texto(prod, "vProd"))

        if descricao:
            itens.append({
                "codigo": codigo,
                "descricao": descricao,
                "unidade": unidade,
                "quantidade": quantidade,
                "valor_unitario": valor_unitario,
                "valor_total": valor_total,
            })

    if not itens:
        raise ValueError("Não foram encontrados produtos no XML da NF-e.")

    return {
        "chave": chave,
        "numero": numero,
        "serie": serie,
        "data_emissao": data_emissao,
        "emitente": emitente,
        "destinatario": destinatario,
        "itens": itens,
    }


def separar_campos_linha_tabela(linha):
    linha = str(linha or "").strip()

    if "\t" in linha:
        return [campo.strip() for campo in linha.split("\t") if campo.strip()]

    if ";" in linha:
        return [campo.strip() for campo in linha.split(";") if campo.strip()]

    return re.split(r"\s{2,}", linha)


def extrair_numeros_linha(linha):
    padrao = r"(?<!\d)(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+|\.\d+)?(?!\d)"
    return list(re.finditer(padrao, linha))


def parse_tabela_produtos_colada(texto):
    linhas = [linha.strip() for linha in str(texto or "").splitlines() if linha.strip()]
    itens = []

    palavras_ignorar = [
        "PRODUTO",
        "SERVIÇO",
        "SERVICO",
        "DESCRIÇÃO",
        "DESCRICAO",
        "QUANTIDADE",
        "UNIDADE",
        "VALOR",
        "CÓDIGO",
        "CODIGO",
    ]

    for linha in linhas:
        linha_norm = normalizar_texto_aquisicao(linha)

        if not linha_norm:
            continue

        if sum(1 for palavra in palavras_ignorar if palavra in linha_norm) >= 3:
            continue

        campos = separar_campos_linha_tabela(linha)

        descricao = ""
        unidade = ""
        quantidade = Decimal("0")
        valor_unitario = Decimal("0")
        valor_total = Decimal("0")

        if len(campos) >= 5:
            # Tenta entender cópias em formato tabulado:
            # código | descrição | quantidade | unidade | valor unitário | valor total
            numeros_por_indice = [(idx, decimal_br_para_decimal(campo)) for idx, campo in enumerate(campos) if re.search(r"\d", campo)]

            if len(numeros_por_indice) >= 3:
                idx_qtd, quantidade = numeros_por_indice[-3]
                idx_unit, valor_unitario = numeros_por_indice[-2]
                idx_total, valor_total = numeros_por_indice[-1]

                candidatos_descricao = campos[1:idx_qtd] if idx_qtd > 1 else campos[:idx_qtd]
                descricao = " ".join(candidatos_descricao).strip()

                if idx_qtd + 1 < len(campos) and not re.search(r"\d", campos[idx_qtd + 1]):
                    unidade = campos[idx_qtd + 1]
                elif idx_qtd - 1 >= 0 and not re.search(r"\d", campos[idx_qtd - 1]):
                    unidade = campos[idx_qtd - 1]
        else:
            matches = extrair_numeros_linha(linha)

            if len(matches) >= 3:
                quantidade = decimal_br_para_decimal(matches[-3].group())
                valor_unitario = decimal_br_para_decimal(matches[-2].group())
                valor_total = decimal_br_para_decimal(matches[-1].group())

                descricao = linha[:matches[-3].start()].strip()
                descricao = re.sub(r"^\d+\s*", "", descricao).strip()

                trecho_entre_qtd_e_unit = linha[matches[-3].end():matches[-2].start()].strip()
                unidade = trecho_entre_qtd_e_unit

        if not descricao:
            continue

        if quantidade <= 0 or valor_total <= 0:
            continue

        if valor_unitario <= 0 and quantidade > 0:
            valor_unitario = (valor_total / quantidade).quantize(Decimal("0.0001"))

        itens.append({
            "codigo": "",
            "descricao": descricao,
            "unidade": unidade,
            "quantidade": quantidade,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total,
        })

    return itens


def criar_nota_aquisicao_por_itens(request, contrato, metodo_entrada, dados_nota, itens_extraidos, arquivo_xml=None, tabela_colada=""):
    total_nota = sum((item["valor_total"] for item in itens_extraidos), Decimal("0"))

    chave_acesso = dados_nota.get("chave") or ""

    if chave_acesso and AquisicaoNotaFiscal.objects.filter(chave_acesso=chave_acesso).exists():
        raise ValueError("Já existe uma nota fiscal cadastrada com esta chave de acesso.")

    nota = AquisicaoNotaFiscal.objects.create(
        pregao=contrato.pregao,
        escola=contrato.escola,
        fornecedor=contrato.fornecedor,
        contrato=contrato,
        metodo_entrada=metodo_entrada,
        chave_acesso=chave_acesso,
        numero_nota=dados_nota.get("numero") or "",
        serie=dados_nota.get("serie") or "",
        data_emissao=dados_nota.get("data_emissao") or None,
        data_recebimento=dados_nota.get("data_recebimento") or timezone.localdate(),
        valor_total=total_nota,
        arquivo_xml=arquivo_xml,
        tabela_colada=tabela_colada,
        observacoes=dados_nota.get("observacoes") or "",
        status=AquisicaoNotaFiscal.STATUS_EM_CONFERENCIA,
        criado_por=request.user,
    )

    for item_extraido in itens_extraidos:
        contrato_item = buscar_contrato_item_por_descricao(contrato, item_extraido.get("descricao"))

        status_item = AquisicaoNotaFiscalItem.STATUS_PENDENTE
        item_cadastrado = None
        unidade = item_extraido.get("unidade") or ""

        if contrato_item:
            status_item = AquisicaoNotaFiscalItem.STATUS_VINCULADO
            item_cadastrado = contrato_item.item
            unidade = contrato_item.unidade or unidade

        AquisicaoNotaFiscalItem.objects.create(
            nota=nota,
            contrato_item=contrato_item,
            item=item_cadastrado,
            codigo_produto=item_extraido.get("codigo") or "",
            descricao_produto=item_extraido.get("descricao") or "",
            unidade=unidade,
            quantidade=item_extraido.get("quantidade") or Decimal("0"),
            valor_unitario=item_extraido.get("valor_unitario") or Decimal("0"),
            valor_total=item_extraido.get("valor_total") or Decimal("0"),
            status=status_item,
            conferido=bool(contrato_item),
        )

    return nota



def somente_digitos_aquisicao(valor):
    return re.sub(r"\D+", "", str(valor or ""))


def normalizar_unidade_aquisicao(valor):
    unidade = normalizar_texto_aquisicao(valor)

    aliases = {
        "KG": "QUILOGRAMA",
        "KGS": "QUILOGRAMA",
        "KILO": "QUILOGRAMA",
        "KILOGRAMA": "QUILOGRAMA",
        "QUILO": "QUILOGRAMA",
        "QUILOGRAMA": "QUILOGRAMA",
        "G": "GRAMA",
        "GR": "GRAMA",
        "GRAMA": "GRAMA",
        "L": "LITRO",
        "LT": "LITRO",
        "LTS": "LITRO",
        "LITRO": "LITRO",
        "ML": "MILILITRO",
        "MILILITRO": "MILILITRO",
        "UN": "UNIDADE",
        "UND": "UNIDADE",
        "UNID": "UNIDADE",
        "UNIDADE": "UNIDADE",
        "PCT": "PACOTE",
        "PCTS": "PACOTE",
        "PACOTE": "PACOTE",
        "CX": "CAIXA",
        "CXA": "CAIXA",
        "CAIXA": "CAIXA",
        "FD": "FARDO",
        "FARDO": "FARDO",
        "DZ": "DUZIA",
        "DUZIA": "DUZIA",
    }

    return aliases.get(unidade, unidade)


def pontuacao_correspondencia_xml(descricao_xml, unidade_xml, contrato_item):
    descricao_xml_norm = normalizar_texto_aquisicao(descricao_xml)
    nome_sistema_norm = normalizar_texto_aquisicao(contrato_item.item.nome_item)

    if not descricao_xml_norm or not nome_sistema_norm:
        return Decimal("0")

    if descricao_xml_norm == nome_sistema_norm:
        pontuacao_texto = 1.0
    else:
        similaridade = SequenceMatcher(
            None,
            descricao_xml_norm,
            nome_sistema_norm,
        ).ratio()

        palavras_xml = set(
            palavra for palavra in descricao_xml_norm.split()
            if len(palavra) >= 3
        )
        palavras_sistema = set(
            palavra for palavra in nome_sistema_norm.split()
            if len(palavra) >= 3
        )

        if palavras_xml and palavras_sistema:
            intersecao = len(palavras_xml.intersection(palavras_sistema))
            token_score = (
                2 * intersecao
                / (len(palavras_xml) + len(palavras_sistema))
            )
        else:
            token_score = 0

        contem = (
            nome_sistema_norm in descricao_xml_norm
            or descricao_xml_norm in nome_sistema_norm
        )

        pontuacao_texto = max(
            similaridade,
            token_score,
            0.92 if contem else 0,
        )

    unidade_xml_norm = normalizar_unidade_aquisicao(unidade_xml)
    unidade_sistema_norm = normalizar_unidade_aquisicao(contrato_item.unidade)

    unidade_compativel = bool(
        unidade_xml_norm
        and unidade_sistema_norm
        and unidade_xml_norm == unidade_sistema_norm
    )

    # A unidade ajuda a confirmar a sugestão, mas uma abreviação desconhecida
    # não impede uma boa correspondência textual.
    if unidade_compativel:
        pontuacao_final = min(1.0, pontuacao_texto + 0.06)
    else:
        pontuacao_final = pontuacao_texto

    return Decimal(str(round(pontuacao_final, 4)))


def sugerir_contrato_item_xml(contrato, descricao_xml, unidade_xml):
    itens_contrato = list(
        contrato.itens.select_related("item").order_by("item__nome_item")
    )

    melhor_item = None
    melhor_pontuacao = Decimal("0")

    for contrato_item in itens_contrato:
        pontuacao = pontuacao_correspondencia_xml(
            descricao_xml,
            unidade_xml,
            contrato_item,
        )

        if pontuacao > melhor_pontuacao:
            melhor_item = contrato_item
            melhor_pontuacao = pontuacao

    # Limiar conservador: se houver dúvida, o usuário escolhe manualmente.
    if melhor_item and melhor_pontuacao >= Decimal("0.80"):
        return melhor_item, melhor_pontuacao

    return None, melhor_pontuacao


def montar_linhas_revisao_xml(contrato, dados_xml, selecoes=None):
    selecoes = selecoes or {}
    linhas = []

    for indice, item_xml in enumerate(dados_xml.get("itens") or []):
        contrato_item = None
        origem_vinculo = "nao_encontrado"
        pontuacao = Decimal("0")

        contrato_item_id = str(selecoes.get(indice) or "").strip()

        if contrato_item_id:
            contrato_item = (
                contrato.itens.select_related("item")
                .filter(id=contrato_item_id)
                .first()
            )
            if contrato_item:
                origem_vinculo = "manual"
        else:
            contrato_item, pontuacao = sugerir_contrato_item_xml(
                contrato,
                item_xml.get("descricao"),
                item_xml.get("unidade"),
            )
            if contrato_item:
                origem_vinculo = "automatico"

        linhas.append({
            "indice": indice,
            "xml": item_xml,
            "contrato_item": contrato_item,
            "origem_vinculo": origem_vinculo,
            "pontuacao": pontuacao,
        })

    return linhas


def avisos_documentos_xml(contrato, dados_xml):
    avisos = []

    doc_emitente_xml = somente_digitos_aquisicao(
        dados_xml.get("emitente", {}).get("cnpj")
        or dados_xml.get("emitente", {}).get("cpf")
    )
    doc_fornecedor = somente_digitos_aquisicao(
        getattr(contrato.fornecedor, "cnpj", "")
        or getattr(contrato.fornecedor, "cpf", "")
    )

    if doc_emitente_xml and doc_fornecedor and doc_emitente_xml != doc_fornecedor:
        avisos.append(
            "O CNPJ/CPF do emitente do XML é diferente do fornecedor do contrato selecionado."
        )

    doc_destinatario_xml = somente_digitos_aquisicao(
        dados_xml.get("destinatario", {}).get("cnpj")
        or dados_xml.get("destinatario", {}).get("cpf")
    )
    doc_escola = somente_digitos_aquisicao(getattr(contrato.escola, "cnpj", ""))

    if doc_destinatario_xml and doc_escola and doc_destinatario_xml != doc_escola:
        avisos.append(
            "O CNPJ/CPF do destinatário do XML é diferente do CNPJ da escola selecionada."
        )

    return avisos


def criar_token_xml_temporario(
    arquivo,
    contrato_id,
    observacoes,
    metodo_entrada=None,
    origem_revisao="upload_xml",
):
    """
    Guarda o XML dentro de um token assinado e compactado.

    Isso evita depender de um arquivo temporário no filesystem/storage entre
    a tela de upload e a tela de revisão. É mais confiável tanto localmente
    quanto no Railway.
    """
    conteudo = arquivo.read()

    if not conteudo:
        raise ValueError("O arquivo XML está vazio.")

    nome_original = Path(arquivo.name or "nota_fiscal.xml").name

    token = signing.dumps(
        {
            "xml_b64": base64.b64encode(conteudo).decode("ascii"),
            "nome_original": nome_original,
            "contrato_id": int(contrato_id),
            "observacoes": observacoes or "",
            "metodo_entrada": metodo_entrada or AquisicaoNotaFiscal.METODO_XML,
            "origem_revisao": origem_revisao or "upload_xml",
        },
        salt="aquisicoes-upload-xml",
        compress=True,
    )

    return token, conteudo


def ler_token_xml_temporario(token):
    try:
        dados_token = signing.loads(
            token,
            salt="aquisicoes-upload-xml",
            max_age=2 * 60 * 60,
        )
    except signing.SignatureExpired as erro:
        raise ValueError(
            "A revisão do XML expirou. Importe o arquivo novamente."
        ) from erro
    except signing.BadSignature as erro:
        raise ValueError(
            "Os dados temporários da importação do XML são inválidos."
        ) from erro

    xml_b64 = str(dados_token.get("xml_b64") or "")

    if not xml_b64:
        raise ValueError(
            "O conteúdo do XML não foi encontrado. Importe o arquivo novamente."
        )

    try:
        conteudo = base64.b64decode(xml_b64.encode("ascii"))
    except Exception as erro:
        raise ValueError(
            "Não foi possível recuperar o XML da revisão. Importe novamente."
        ) from erro

    return dados_token, conteudo


def renderizar_revisao_xml(
    request,
    contrato,
    dados_xml,
    token,
    selecoes=None,
    mensagem_erro="",
):
    itens_contrato = list(
        contrato.itens.select_related("item").order_by("item__nome_item")
    )

    linhas = montar_linhas_revisao_xml(
        contrato,
        dados_xml,
        selecoes=selecoes,
    )

    quantidade_erros = sum(
        1 for linha in linhas if not linha["contrato_item"]
    )

    total_xml = sum(
        (
            item.get("valor_total") or Decimal("0")
            for item in dados_xml.get("itens") or []
        ),
        Decimal("0"),
    )

    return render(
        request,
        "aquisicoes/revisao_xml.html",
        {
            "contrato": contrato,
            "dados_xml": dados_xml,
            "token_importacao": token,
            "linhas": linhas,
            "itens_contrato": itens_contrato,
            "quantidade_erros": quantidade_erros,
            "total_xml": total_xml,
            "avisos_documentos": avisos_documentos_xml(
                contrato,
                dados_xml,
            ),
            "mensagem_erro": mensagem_erro,
            "origem_revisao": (
                "chave_acesso"
                if (
                    signing.loads(
                        token,
                        salt="aquisicoes-upload-xml",
                        max_age=2 * 60 * 60,
                    ).get("origem_revisao") == "chave_acesso"
                )
                else "upload_xml"
            ),
        },
    )





def confirmar_xml_revisado(
    request,
    token,
    observacoes,
    redirect_name,
):
    try:
        dados_token, conteudo = ler_token_xml_temporario(token)

        contrato = get_object_or_404(
            contratos_permitidos(request),
            id=dados_token["contrato_id"],
        )

        dados_xml = extrair_dados_xml_nfe(
            ContentFile(conteudo)
        )
        dados_xml["observacoes"] = observacoes

        contrato_item_ids = request.POST.getlist(
            "contrato_item[]"
        )

        selecoes = {
            indice: valor
            for indice, valor in enumerate(contrato_item_ids)
        }

        if len(contrato_item_ids) != len(dados_xml["itens"]):
            return renderizar_revisao_xml(
                request,
                contrato,
                dados_xml,
                token,
                selecoes=selecoes,
                mensagem_erro=(
                    "Não foi possível validar todos os itens da NF-e. "
                    "Revise os produtos antes de confirmar."
                ),
            )

        itens_confirmados = []
        erros = []

        for indice, item_xml in enumerate(dados_xml["itens"]):
            contrato_item_id = (
                contrato_item_ids[indice] or ""
            ).strip()

            if not contrato_item_id:
                erros.append(
                    f"Item {indice + 1}: selecione o produto correspondente do contrato."
                )
                continue

            contrato_item = (
                contrato.itens.select_related("item")
                .filter(id=contrato_item_id)
                .first()
            )

            if not contrato_item:
                erros.append(
                    f"Item {indice + 1}: o produto selecionado não pertence ao contrato."
                )
                continue

            quantidade = item_xml.get("quantidade") or Decimal("0")
            valor_unitario = (
                item_xml.get("valor_unitario")
                or contrato_item.valor_unitario
                or Decimal("0")
            )
            valor_total = item_xml.get("valor_total") or Decimal("0")

            if quantidade <= 0:
                erros.append(
                    f"Item {indice + 1}: quantidade inválida."
                )

            if valor_total <= 0 and quantidade > 0 and valor_unitario > 0:
                valor_total = (
                    quantidade * valor_unitario
                ).quantize(Decimal("0.01"))

            if valor_total <= 0:
                erros.append(
                    f"Item {indice + 1}: valor total inválido."
                )

            itens_confirmados.append({
                "xml": item_xml,
                "contrato_item": contrato_item,
                "quantidade": quantidade,
                "valor_unitario": valor_unitario,
                "valor_total": valor_total,
            })

        if erros:
            return renderizar_revisao_xml(
                request,
                contrato,
                dados_xml,
                token,
                selecoes=selecoes,
                mensagem_erro=" ".join(erros),
            )

        chave_acesso = dados_xml.get("chave") or ""

        if (
            chave_acesso
            and AquisicaoNotaFiscal.objects.filter(
                chave_acesso=chave_acesso
            ).exists()
        ):
            raise ValueError(
                "Já existe uma nota fiscal cadastrada com esta chave de acesso."
            )

        total_nota = sum(
            (
                item["valor_total"]
                for item in itens_confirmados
            ),
            Decimal("0"),
        )

        nome_original = (
            dados_token.get("nome_original")
            or "nota_fiscal.xml"
        )

        metodo_entrada = (
            dados_token.get("metodo_entrada")
            or AquisicaoNotaFiscal.METODO_XML
        )

        with transaction.atomic():
            nota = AquisicaoNotaFiscal(
                pregao=contrato.pregao,
                escola=contrato.escola,
                fornecedor=contrato.fornecedor,
                contrato=contrato,
                metodo_entrada=metodo_entrada,
                chave_acesso=chave_acesso,
                numero_nota=dados_xml.get("numero") or "",
                serie=dados_xml.get("serie") or "",
                data_emissao=dados_xml.get("data_emissao") or None,
                valor_total=total_nota,
                observacoes=observacoes,
                status=AquisicaoNotaFiscal.STATUS_CONFIRMADA,
                criado_por=request.user,
                confirmado_por=request.user,
                confirmado_em=timezone.now(),
            )

            nota.arquivo_xml.save(
                Path(nome_original).name,
                ContentFile(conteudo),
                save=False,
            )
            nota.save()

            for item in itens_confirmados:
                contrato_item = item["contrato_item"]
                item_xml = item["xml"]

                AquisicaoNotaFiscalItem.objects.create(
                    nota=nota,
                    contrato_item=contrato_item,
                    item=contrato_item.item,
                    codigo_produto=item_xml.get("codigo") or "",
                    descricao_produto=contrato_item.item.nome_item,
                    unidade=contrato_item.unidade or "",
                    quantidade=item["quantidade"],
                    valor_unitario=item["valor_unitario"],
                    valor_total=item["valor_total"],
                    status=AquisicaoNotaFiscalItem.STATUS_VINCULADO,
                    conferido=True,
                )

        origem_revisao = dados_token.get("origem_revisao") or "upload_xml"

        if origem_revisao == "chave_acesso":
            mensagem = (
                f"NF-e {nota.numero_nota or nota.id} consultada pela chave, "
                "conferida e confirmada com sucesso. "
                "Você já pode consultar outra nota."
            )
        else:
            mensagem = (
                f"NF-e {nota.numero_nota or nota.id} importada, "
                "conferida e confirmada com sucesso. "
                "Você já pode importar outra nota."
            )

        messages.success(request, mensagem)
        return redirect(redirect_name)

    except Exception as erro:
        messages.error(
            request,
            f"Não foi possível confirmar a nota fiscal: {erro}",
        )
        return redirect(redirect_name)



def contexto_upload_xml(request, selecionados=None, observacoes="", erro_importacao=""):
    selecionados = selecionados or {}
    contratos_base = contratos_permitidos(request)

    certames = (
        Pregao.objects.filter(
            contratos_gerados__in=contratos_base
        )
        .distinct()
        .prefetch_related("municipios")
        .order_by("-ano", "-numero")
    )

    return {
        "certames": certames,
        "restrito_escola": usuario_eh_consulta_escola(request),
        "escola_vinculada": escola_vinculada_usuario(request),
        "selecionados": selecionados,
        "observacoes_iniciais": observacoes or "",
        "erro_importacao": erro_importacao or "",
    }




def validar_chave_acesso_nfe(chave):
    chave = "".join(
        caractere
        for caractere in str(chave or "")
        if caractere.isdigit()
    )

    if len(chave) != 44:
        return False, chave, "A Chave de Acesso deve conter exatamente 44 dígitos."

    # Validação do dígito verificador da chave NF-e (módulo 11).
    corpo = chave[:43]
    digito_informado = int(chave[43])

    peso = 2
    soma = 0

    for caractere in reversed(corpo):
        soma += int(caractere) * peso
        peso += 1
        if peso > 9:
            peso = 2

    resto = soma % 11
    digito_calculado = 11 - resto

    if digito_calculado in (10, 11):
        digito_calculado = 0

    if digito_calculado != digito_informado:
        return False, chave, "A Chave de Acesso informada possui dígito verificador inválido."

    return True, chave, ""


def contexto_chave_acesso(
    request,
    selecionados=None,
    chave="",
    observacoes="",
    erro_consulta="",
):
    selecionados = selecionados or {}
    contratos_base = contratos_permitidos(request)

    certames = (
        Pregao.objects.filter(
            contratos_gerados__in=contratos_base
        )
        .distinct()
        .prefetch_related("municipios")
        .order_by("-ano", "-numero")
    )

    return {
        "certames": certames,
        "restrito_escola": usuario_eh_consulta_escola(request),
        "escola_vinculada": escola_vinculada_usuario(request),
        "selecionados": selecionados,
        "chave_inicial": chave or "",
        "observacoes_iniciais": observacoes or "",
        "erro_consulta": erro_consulta or "",
    }


@login_required
def chave_acesso_nfe(request):
    escola_vinculada = (
        escola_vinculada_usuario(request)
        if usuario_eh_consulta_escola(request)
        else None
    )

    if usuario_eh_consulta_escola(request) and not escola_vinculada:
        messages.error(
            request,
            "Seu usuário não possui escola vinculada. Solicite o vínculo ao administrador.",
        )
        return redirect("aquisicoes:notas_fiscais")

    if request.method == "POST":
        acao = request.POST.get("acao") or "consultar_chave"

        if acao == "confirmar_xml":
            return confirmar_xml_revisado(
                request,
                token=request.POST.get("token_importacao") or "",
                observacoes=request.POST.get("observacoes") or "",
                redirect_name="aquisicoes:chave_acesso",
            )

        if acao != "consultar_chave":
            messages.error(request, "Ação de consulta inválida.")
            return redirect("aquisicoes:chave_acesso")

        contrato_id = request.POST.get("contrato")
        chave_digitada = request.POST.get("chave_acesso") or ""
        observacoes = request.POST.get("observacoes") or ""

        selecionados = {
            "pregao": request.POST.get("pregao") or "",
            "escola": request.POST.get("escola") or "",
            "fornecedor": request.POST.get("fornecedor") or "",
            "contrato": contrato_id or "",
        }

        chave_valida, chave, erro_chave = validar_chave_acesso_nfe(
            chave_digitada
        )

        if not chave_valida:
            return render(
                request,
                "aquisicoes/chave_acesso.html",
                contexto_chave_acesso(
                    request,
                    selecionados=selecionados,
                    chave=chave,
                    observacoes=observacoes,
                    erro_consulta=erro_chave,
                ),
            )

        if not contrato_id:
            return render(
                request,
                "aquisicoes/chave_acesso.html",
                contexto_chave_acesso(
                    request,
                    selecionados=selecionados,
                    chave=chave,
                    observacoes=observacoes,
                    erro_consulta="Selecione o contrato.",
                ),
            )

        if AquisicaoNotaFiscal.objects.filter(
            chave_acesso=chave
        ).exists():
            return render(
                request,
                "aquisicoes/chave_acesso.html",
                contexto_chave_acesso(
                    request,
                    selecionados=selecionados,
                    chave=chave,
                    observacoes=observacoes,
                    erro_consulta=(
                        "Já existe uma nota fiscal cadastrada com esta Chave de Acesso."
                    ),
                ),
            )

        contrato = get_object_or_404(
            contratos_permitidos(request),
            id=contrato_id,
        )

        try:
            resultado_consulta = consultar_xml_consultadanfe(chave)
            codigo_xml = resultado_consulta["codigo_xml"]

            conteudo = codigo_xml.encode("utf-8")
            dados_xml = extrair_dados_xml_nfe(
                ContentFile(conteudo)
            )

            chave_xml = "".join(
                caractere
                for caractere in str(dados_xml.get("chave") or "")
                if caractere.isdigit()
            )

            if chave_xml and chave_xml != chave:
                raise ValueError(
                    "A chave existente no XML retornado é diferente da chave consultada."
                )

            arquivo_token = ContentFile(
                conteudo,
                name=f"NFe_{chave}.xml",
            )

            token, _ = criar_token_xml_temporario(
                arquivo_token,
                contrato.id,
                observacoes,
                metodo_entrada=AquisicaoNotaFiscal.METODO_CHAVE_ACESSO,
                origem_revisao="chave_acesso",
            )

            dados_xml["observacoes"] = observacoes

        except ConsultaDanfeErro as erro:
            return render(
                request,
                "aquisicoes/chave_acesso.html",
                contexto_chave_acesso(
                    request,
                    selecionados=selecionados,
                    chave=chave,
                    observacoes=observacoes,
                    erro_consulta=(
                        f"Não foi possível consultar esta NF-e automaticamente: {erro}"
                    ),
                ),
            )

        except Exception as erro:
            return render(
                request,
                "aquisicoes/chave_acesso.html",
                contexto_chave_acesso(
                    request,
                    selecionados=selecionados,
                    chave=chave,
                    observacoes=observacoes,
                    erro_consulta=(
                        f"Não foi possível processar o XML retornado: {erro}"
                    ),
                ),
            )

        return renderizar_revisao_xml(
            request,
            contrato,
            dados_xml,
            token,
        )

    return render(
        request,
        "aquisicoes/chave_acesso.html",
        contexto_chave_acesso(request),
    )



@login_required
def upload_xml_nfe(request):
    escola_vinculada = (
        escola_vinculada_usuario(request)
        if usuario_eh_consulta_escola(request)
        else None
    )

    if usuario_eh_consulta_escola(request) and not escola_vinculada:
        messages.error(
            request,
            "Seu usuário não possui escola vinculada. Solicite o vínculo ao administrador.",
        )
        return redirect("aquisicoes:notas_fiscais")

    if request.method == "POST":
        acao = request.POST.get("acao") or "importar"

        # ----------------------------------------------------
        # ETAPA 1: lê o XML e abre a tela de revisão.
        # ----------------------------------------------------
        if acao == "importar":
            contrato_id = request.POST.get("contrato")
            arquivo = request.FILES.get("arquivo_xml")
            observacoes = request.POST.get("observacoes") or ""

            selecionados = {
                "pregao": request.POST.get("pregao") or "",
                "escola": request.POST.get("escola") or "",
                "fornecedor": request.POST.get("fornecedor") or "",
                "contrato": contrato_id or "",
            }

            if not contrato_id:
                return render(
                    request,
                    "aquisicoes/upload_xml.html",
                    contexto_upload_xml(
                        request,
                        selecionados=selecionados,
                        observacoes=observacoes,
                        erro_importacao="Selecione o contrato.",
                    ),
                )

            if not arquivo:
                return render(
                    request,
                    "aquisicoes/upload_xml.html",
                    contexto_upload_xml(
                        request,
                        selecionados=selecionados,
                        observacoes=observacoes,
                        erro_importacao="Selecione o arquivo XML da NF-e.",
                    ),
                )

            contrato = get_object_or_404(
                contratos_permitidos(request),
                id=contrato_id,
            )

            try:
                # Primeiro lê/valida o XML. Somente depois cria o token de revisão.
                conteudo = arquivo.read()

                if not conteudo:
                    raise ValueError("O arquivo XML está vazio.")

                dados_xml = extrair_dados_xml_nfe(
                    ContentFile(conteudo)
                )

                if (
                    dados_xml.get("chave")
                    and AquisicaoNotaFiscal.objects.filter(
                        chave_acesso=dados_xml["chave"]
                    ).exists()
                ):
                    raise ValueError(
                        "Já existe uma nota fiscal cadastrada com esta chave de acesso."
                    )

                # Recria um objeto simples com nome para gerar o token.
                arquivo_token = ContentFile(
                    conteudo,
                    name=Path(arquivo.name or "nota_fiscal.xml").name,
                )

                token, _ = criar_token_xml_temporario(
                    arquivo_token,
                    contrato.id,
                    observacoes,
                    metodo_entrada=AquisicaoNotaFiscal.METODO_XML,
                    origem_revisao="upload_xml",
                )

                # Mantém observações digitadas na tela anterior.
                dados_xml["observacoes"] = observacoes

            except Exception as erro:
                return render(
                    request,
                    "aquisicoes/upload_xml.html",
                    contexto_upload_xml(
                        request,
                        selecionados=selecionados,
                        observacoes=observacoes,
                        erro_importacao=f"Não foi possível importar o XML: {erro}",
                    ),
                )

            # Importante: o render da próxima tela fica FORA do try.
            # Assim, um eventual problema de template não é escondido por um redirect
            # que apenas limpa a tela de upload.
            return renderizar_revisao_xml(
                request,
                contrato,
                dados_xml,
                token,
            )

        # ----------------------------------------------------
        # ETAPA 2: valida todos os vínculos e grava a nota.
        # ----------------------------------------------------
        if acao == "confirmar_xml":
            return confirmar_xml_revisado(
                request,
                token=request.POST.get("token_importacao") or "",
                observacoes=request.POST.get("observacoes") or "",
                redirect_name="aquisicoes:upload_xml",
            )

        messages.error(request, "Ação de importação inválida.")
        return redirect("aquisicoes:upload_xml")

    return render(
        request,
        "aquisicoes/upload_xml.html",
        contexto_upload_xml(request),
    )


@login_required
def colar_tabela_nfe(request):
    escola_vinculada = escola_vinculada_usuario(request) if usuario_eh_consulta_escola(request) else None

    if request.method == "POST":
        contrato_id = request.POST.get("contrato")
        numero_nota = (request.POST.get("numero_nota") or "").strip()
        data_emissao = request.POST.get("data_emissao") or None
        data_recebimento = request.POST.get("data_recebimento") or timezone.localdate()
        tabela_colada = request.POST.get("tabela_colada") or ""
        observacoes = request.POST.get("observacoes") or ""

        if not contrato_id:
            messages.error(request, "Selecione o contrato.")
            return redirect("aquisicoes:colar_tabela")

        if not numero_nota:
            messages.error(request, "Informe o número da NF-e.")
            return redirect("aquisicoes:colar_tabela")

        if not tabela_colada.strip():
            messages.error(request, "Cole a tabela de produtos da NF-e.")
            return redirect("aquisicoes:colar_tabela")

        contrato = get_object_or_404(contratos_permitidos(request), id=contrato_id)

        itens = parse_tabela_produtos_colada(tabela_colada)

        if not itens:
            messages.error(
                request,
                "Não foi possível identificar os produtos na tabela colada. Copie as linhas da tabela Produtos e Serviços ou use o lançamento manual.",
            )
            return redirect("aquisicoes:colar_tabela")

        dados_nota = {
            "numero": numero_nota,
            "serie": "",
            "chave": "",
            "data_emissao": data_emissao,
            "data_recebimento": data_recebimento,
            "observacoes": observacoes,
        }

        try:
            nota = criar_nota_aquisicao_por_itens(
                request=request,
                contrato=contrato,
                metodo_entrada=AquisicaoNotaFiscal.METODO_TABELA_COLADA,
                dados_nota=dados_nota,
                itens_extraidos=itens,
                tabela_colada=tabela_colada,
            )

            messages.success(request, "Tabela colada importada com sucesso. Confira os itens antes de confirmar a aquisição.")
            return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

        except Exception as erro:
            messages.error(request, f"Não foi possível importar a tabela colada: {erro}")
            return redirect("aquisicoes:colar_tabela")

    contratos_base = contratos_permitidos(request)

    certames = Pregao.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("-ano", "-numero")

    return render(
        request,
        "aquisicoes/colar_tabela.html",
        {
            "certames": certames,
            "restrito_escola": usuario_eh_consulta_escola(request),
            "escola_vinculada": escola_vinculada,
        },
    )

