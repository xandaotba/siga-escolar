import re
import unicodedata
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import models
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone

from apps.cadastros.models import Escola, Fornecedor
from apps.documentos.models import ContratoGerado, ContratoItemGerado
from apps.pregoes.models import Pregao

from .forms import AquisicaoNotaFiscalForm
from .models import AquisicaoNotaFiscal, AquisicaoNotaFiscalItem


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

    certames = Pregao.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("-ano", "-numero")

    escolas = Escola.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("nome_escola")

    fornecedores = Fornecedor.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("razao_social")

    totais = {
        "total": notas.count(),
        "rascunho": notas.filter(status=AquisicaoNotaFiscal.STATUS_RASCUNHO).count(),
        "conferencia": notas.filter(status=AquisicaoNotaFiscal.STATUS_EM_CONFERENCIA).count(),
        "confirmada": notas.filter(status=AquisicaoNotaFiscal.STATUS_CONFIRMADA).count(),
        "cancelada": notas.filter(status=AquisicaoNotaFiscal.STATUS_CANCELADA).count(),
    }

    return render(
        request,
        "aquisicoes/notas_fiscais.html",
        {
            "notas": notas,
            "certames": certames,
            "escolas": escolas,
            "fornecedores": fornecedores,
            "status_choices": AquisicaoNotaFiscal.STATUS_CHOICES,
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

    if nota.status == AquisicaoNotaFiscal.STATUS_CONFIRMADA:
        messages.warning(request, "Notas confirmadas não podem ser editadas.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    if nota.status == AquisicaoNotaFiscal.STATUS_CANCELADA:
        messages.warning(request, "Notas canceladas não podem ser editadas.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    if request.method == "POST":
        nota.numero_nota = (request.POST.get("numero_nota") or "").strip()
        nota.data_emissao = request.POST.get("data_emissao") or None
        nota.data_recebimento = request.POST.get("data_recebimento") or nota.data_recebimento
        nota.observacoes = request.POST.get("observacoes") or ""

        if not nota.numero_nota:
            messages.error(request, "Informe o número da NF-e.")
            return redirect("aquisicoes:nota_fiscal_editar", nota_id=nota.id)

        nota.save(update_fields=[
            "numero_nota",
            "data_emissao",
            "data_recebimento",
            "observacoes",
            "atualizado_em",
        ])

        messages.success(request, "Dados da nota fiscal atualizados com sucesso.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    return render(request, "aquisicoes/nota_fiscal_editar.html", {"nota": nota})

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

    if nota.status == AquisicaoNotaFiscal.STATUS_CONFIRMADA:
        messages.error(request, "Notas confirmadas somente poderão ser canceladas pela rotina de estorno que será criada depois.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    if request.method == "POST":
        nota.status = AquisicaoNotaFiscal.STATUS_CANCELADA
        nota.save(update_fields=["status", "atualizado_em"])
        messages.success(request, "Nota fiscal cancelada com sucesso.")
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


@login_required
def aquisicao_manual_nova(request):
    escola_vinculada = escola_vinculada_usuario(request) if usuario_eh_consulta_escola(request) else None

    if usuario_eh_consulta_escola(request) and not escola_vinculada:
        messages.error(request, "Seu usuário não possui escola vinculada. Solicite o vínculo ao administrador.")
        return redirect("aquisicoes:notas_fiscais")

    contratos_base = contratos_permitidos(request)
    certames = Pregao.objects.filter(contratos_gerados__in=contratos_base).distinct().order_by("-ano", "-numero")
    escolas = Escola.objects.filter(contratos_gerados__in=contratos_base).distinct().order_by("nome_escola")
    fornecedores = Fornecedor.objects.filter(contratos_gerados__in=contratos_base).distinct().order_by("razao_social")
    contratos = contratos_base.order_by("-pregao__ano", "pregao__numero", "escola__nome_escola", "fornecedor__razao_social")

    if request.method == "POST":
        contrato_id = request.POST.get("contrato")
        numero_nota = (request.POST.get("numero_nota") or "").strip()
        serie = ""
        chave_acesso = ""
        data_emissao = request.POST.get("data_emissao") or None
        data_recebimento = request.POST.get("data_recebimento") or None
        observacoes = request.POST.get("observacoes") or ""

        if not contrato_id:
            messages.error(request, "Selecione o contrato para registrar a aquisição.")
            return redirect("aquisicoes:aquisicao_manual_nova")

        contrato = get_object_or_404(contratos_base, id=contrato_id)
        pregao = contrato.pregao
        escola = contrato.escola
        fornecedor = contrato.fornecedor

        if chave_acesso and len(chave_acesso) != 44:
            messages.error(request, "A chave de acesso deve conter exatamente 44 dígitos.")
            return redirect("aquisicoes:aquisicao_manual_nova")

        if chave_acesso and AquisicaoNotaFiscal.objects.filter(chave_acesso=chave_acesso).exists():
            messages.error(request, "Já existe uma nota fiscal registrada com esta chave de acesso.")
            return redirect("aquisicoes:aquisicao_manual_nova")

        contrato_item_ids = request.POST.getlist("contrato_item[]")
        descricoes = request.POST.getlist("descricao_produto[]")
        quantidades = request.POST.getlist("quantidade[]")
        valores_unitarios = request.POST.getlist("valor_unitario[]")
        valores_totais = request.POST.getlist("valor_total[]")

        itens_validos = []

        for indice, contrato_item_id in enumerate(contrato_item_ids):
            contrato_item_id = (contrato_item_id or "").strip()
            descricao = descricoes[indice].strip() if indice < len(descricoes) else ""
            quantidade = decimal_br_para_decimal(quantidades[indice] if indice < len(quantidades) else "")
            valor_unitario = decimal_br_para_decimal(valores_unitarios[indice] if indice < len(valores_unitarios) else "")
            valor_total = decimal_br_para_decimal(valores_totais[indice] if indice < len(valores_totais) else "")

            if not contrato_item_id and not descricao and quantidade == 0 and valor_unitario == 0 and valor_total == 0:
                continue

            if not contrato_item_id:
                messages.error(request, f"Selecione o item do contrato na linha {indice + 1}.")
                return redirect("aquisicoes:aquisicao_manual_nova")

            contrato_item = get_object_or_404(
                ContratoItemGerado.objects.select_related("contrato", "item"),
                id=contrato_item_id,
                contrato=contrato,
            )

            if quantidade <= 0:
                messages.error(request, f"Informe uma quantidade maior que zero na linha {indice + 1}.")
                return redirect("aquisicoes:aquisicao_manual_nova")

            valor_calculado = (quantidade * valor_unitario).quantize(Decimal("0.01"))

            if valor_total <= 0:
                valor_total = valor_calculado

            if abs(valor_total - valor_calculado) > Decimal("0.05"):
                messages.error(request, f"O valor total da linha {indice + 1} está diferente de quantidade x valor unitário.")
                return redirect("aquisicoes:aquisicao_manual_nova")

            itens_validos.append({
                "contrato_item": contrato_item,
                "descricao": descricao or contrato_item.item.nome_item,
                "unidade": contrato_item.unidade or "",
                "quantidade": quantidade,
                "valor_unitario": valor_unitario,
                "valor_total": valor_total,
            })

        if not itens_validos:
            messages.error(request, "Adicione pelo menos um item da nota fiscal.")
            return redirect("aquisicoes:aquisicao_manual_nova")

        nota = AquisicaoNotaFiscal.objects.create(
            pregao=pregao,
            escola=escola,
            fornecedor=fornecedor,
            contrato=contrato,
            metodo_entrada=AquisicaoNotaFiscal.METODO_MANUAL,
            chave_acesso=chave_acesso,
            numero_nota=numero_nota,
            serie=serie,
            data_emissao=data_emissao,
            data_recebimento=data_recebimento,
            observacoes=observacoes,
            status=AquisicaoNotaFiscal.STATUS_EM_CONFERENCIA,
            criado_por=request.user,
            valor_total=Decimal("0"),
        )

        total_nota = Decimal("0")

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
            total_nota += dados["valor_total"]

        nota.valor_total = total_nota
        nota.save(update_fields=["valor_total", "atualizado_em"])

        messages.success(request, "Aquisição manual registrada com sucesso.")
        return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

    return render(request, "aquisicoes/aquisicao_manual_form.html", {
        "certames": certames,
        "escolas": escolas,
        "fornecedores": fornecedores,
        "contratos": contratos,
        "contratos_json": contratos_json_permitidos(request),
        "restrito_escola": usuario_eh_consulta_escola(request),
        "escola_vinculada": escola_vinculada,
    })


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

    status_notas_validas = [AquisicaoNotaFiscal.STATUS_CONFIRMADA]
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

    certames = Pregao.objects.filter(
        contratos_gerados__in=contratos_permitidos_base
    ).distinct().order_by("-ano", "-numero")

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


@login_required
def upload_xml_nfe(request):
    escola_vinculada = escola_vinculada_usuario(request) if usuario_eh_consulta_escola(request) else None

    if request.method == "POST":
        contrato_id = request.POST.get("contrato")
        arquivo = request.FILES.get("arquivo_xml")
        data_recebimento = request.POST.get("data_recebimento") or timezone.localdate()
        observacoes = request.POST.get("observacoes") or ""

        if not contrato_id:
            messages.error(request, "Selecione o contrato.")
            return redirect("aquisicoes:upload_xml")

        if not arquivo:
            messages.error(request, "Selecione o arquivo XML da NF-e.")
            return redirect("aquisicoes:upload_xml")

        contrato = get_object_or_404(contratos_permitidos(request), id=contrato_id)

        try:
            dados_xml = extrair_dados_xml_nfe(arquivo)
            arquivo.seek(0)

            dados_xml["data_recebimento"] = data_recebimento
            dados_xml["observacoes"] = observacoes

            nota = criar_nota_aquisicao_por_itens(
                request=request,
                contrato=contrato,
                metodo_entrada=AquisicaoNotaFiscal.METODO_XML,
                dados_nota=dados_xml,
                itens_extraidos=dados_xml["itens"],
                arquivo_xml=arquivo,
            )

            messages.success(request, "XML da NF-e importado com sucesso. Confira os itens antes de confirmar a aquisição.")
            return redirect("aquisicoes:nota_fiscal_detalhe", nota_id=nota.id)

        except Exception as erro:
            messages.error(request, f"Não foi possível importar o XML: {erro}")
            return redirect("aquisicoes:upload_xml")

    contratos_base = contratos_permitidos(request)

    certames = Pregao.objects.filter(
        contratos_gerados__in=contratos_base
    ).distinct().order_by("-ano", "-numero")

    return render(
        request,
        "aquisicoes/upload_xml.html",
        {
            "certames": certames,
            "restrito_escola": usuario_eh_consulta_escola(request),
            "escola_vinculada": escola_vinculada,
        },
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

