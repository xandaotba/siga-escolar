from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.db.models import Max, Min
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.pregoes.models import (
    DesistenciaItem,
    Lance,
    Pregao,
    PregaoFornecedor,
    PregaoItem,
    ResultadoItem,
    PropostaInicialItem,
)
from .services import (
    definir_fornecedor_atual_se_necessario,
    fornecedores_ativos_do_item,
    fornecedores_participantes_do_item,
    iniciar_ou_continuar_pregao,
    obter_proximo_fornecedor_visual,
    proximo_fornecedor_da_rodada,
)


def calcular_alerta_media(item_atual, valor_adjudicado):
    """
    Verifica se o valor adjudicado está fora da faixa permitida
    em relação à média de preço cadastrada no item.

    Retorna None quando:
    - não há média cadastrada;
    - a média é zero ou inválida;
    - o valor está dentro da faixa.
    """

    media = item_atual.media_preco

    if media is None or media <= 0:
        return None

    percentual = item_atual.pregao.percentual_alerta_media or Decimal("50")

    limite_minimo = media * (Decimal("100") - percentual) / Decimal("100")
    limite_maximo = media * (Decimal("100") + percentual) / Decimal("100")

    valor_adjudicado = Decimal(valor_adjudicado)

    if limite_minimo <= valor_adjudicado <= limite_maximo:
        return None

    diferenca_percentual = ((valor_adjudicado - media) / media) * Decimal("100")

    if valor_adjudicado < limite_minimo:
        tipo = "abaixo"
        mensagem = (
            f"O valor a adjudicar está {abs(diferenca_percentual):.2f}% abaixo "
            f"da média cadastrada para este item."
        )
    else:
        tipo = "acima"
        mensagem = (
            f"O valor a adjudicar está {abs(diferenca_percentual):.2f}% acima "
            f"da média cadastrada para este item."
        )

    return {
        "tipo": tipo,
        "mensagem": mensagem,
        "media": media,
        "percentual": percentual,
        "limite_minimo": limite_minimo,
        "limite_maximo": limite_maximo,
        "valor_adjudicado": valor_adjudicado,
        "diferenca_percentual": diferenca_percentual,
    }



def execucao_pregao(request):
    pregoes = Pregao.objects.filter(
        tipo_certame=Pregao.TIPO_PREGAO_PRESENCIAL,
        status__in=[
            Pregao.STATUS_NAO_INICIADO,
            Pregao.STATUS_EM_ANDAMENTO,
        ]
    ).order_by("-ano", "-numero")

    return render(
        request,
        "execucao/execucao_pregao.html",
        {
            "pregoes": pregoes,
        },
    )


def abrir_execucao(request, pregao_id):
    pregao = get_object_or_404(
        Pregao,
        id=pregao_id,
        tipo_certame=Pregao.TIPO_PREGAO_PRESENCIAL,
    )

    try:
        item_atual = iniciar_ou_continuar_pregao(pregao)
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    except Exception as erro:
        messages.error(request, str(erro))
        return redirect("execucao:execucao_pregao")


def tela_lances(request, pregao_id, item_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)
    item_atual = get_object_or_404(PregaoItem, id=item_id, pregao=pregao)

    if item_atual.status == PregaoItem.STATUS_EM_DISPUTA:
        definir_fornecedor_atual_se_necessario(pregao, item_atual)
        item_atual.refresh_from_db()

    fornecedores_vinculos = (
        PregaoFornecedor.objects.filter(
            pregao=pregao,
            ativo_no_pregao=True,
        )
        .select_related("fornecedor")
        .order_by("ordem_inicial", "fornecedor__razao_social")
    )

    # Todos os fornecedores ativos do pregão aparecem no modal de propostas,
    # para permitir marcar quem participa e quem não participa do item.
    fornecedores = [v.fornecedor for v in fornecedores_vinculos]

    propostas_iniciais = {
        proposta.fornecedor_id: proposta
        for proposta in PropostaInicialItem.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
        )
    }

    fornecedores_participantes = fornecedores_participantes_do_item(pregao, item_atual)

    desistencias = DesistenciaItem.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    ).select_related("fornecedor")

    fornecedores_desistentes_ids = list(
        desistencias.values_list("fornecedor_id", flat=True)
    )

    fornecedores_ativos = fornecedores_ativos_do_item(pregao, item_atual)
    fornecedor_atual = item_atual.fornecedor_atual
    proximo_fornecedor = obter_proximo_fornecedor_visual(pregao, item_atual)

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "salvar_propostas_iniciais":
            return salvar_propostas_iniciais(request, pregao, item_atual)

        if acao == "registrar_lance":
            return registrar_lance_fornecedor_atual(request, pregao, item_atual)

        elif acao == "registrar_desistencia":
            return registrar_desistencia_fornecedor_atual(request, pregao, item_atual)

        elif acao == "finalizar_item":
            return finalizar_item(request, pregao, item_atual)

        elif acao == "marcar_deserto":
            return marcar_deserto(request, pregao, item_atual)

        elif acao == "marcar_fracassado":
            return marcar_fracassado(request, pregao, item_atual)

        else:
            messages.error(request, "Ação inválida.")
            return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    lances = Lance.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    ).select_related("fornecedor").order_by("ordem_lance")

    resultado = ResultadoItem.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    ).first()

    menor_lance_obj = (
        Lance.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
        )
        .order_by("valor_lance")
        .first()
    )

    menor_proposta_obj = (
        PropostaInicialItem.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
            participa=True,
            preco_inicial__isnull=False,
        )
        .order_by("preco_inicial")
        .first()
    )

    menor_valor_exibicao = None
    menor_valor_origem = ""

    if menor_lance_obj:
        menor_valor_exibicao = menor_lance_obj.valor_lance
        menor_valor_origem = "lance"
    elif menor_proposta_obj:
        menor_valor_exibicao = menor_proposta_obj.preco_inicial
        menor_valor_origem = "proposta"

    alerta_media = None

    if request.GET.get("confirmar_media") == "1" and menor_lance_obj:
        alerta_media = calcular_alerta_media(item_atual, menor_lance_obj.valor_lance)

    propostas_pendentes = False

    if item_atual.status == PregaoItem.STATUS_EM_DISPUTA:
        for fornecedor in fornecedores:
            proposta = propostas_iniciais.get(fornecedor.id)

            if not proposta:
                propostas_pendentes = True
                break

            if proposta.participa and (not proposta.marca or proposta.preco_inicial is None):
                propostas_pendentes = True
                break

    modo_conferencia_alteracao = request.session.get(
        f"conferencia_alteracao_item_{pregao.id}_{item_atual.id}",
        False,
    )

    lances_com_marca = []

    for lance in lances:
        proposta = propostas_iniciais.get(lance.fornecedor_id)

        lances_com_marca.append(
            {
                "lance": lance,
                "marca": proposta.marca if proposta and proposta.participa else "",
            }
        )

    return render(
        request,
        "execucao/tela_lances.html",
        {
            "pregao": pregao,
            "item_atual": item_atual,
            "fornecedores": fornecedores,
            "fornecedores_participantes": fornecedores_participantes,
            "fornecedores_ativos": fornecedores_ativos,
            "fornecedor_atual": fornecedor_atual,
            "proximo_fornecedor": proximo_fornecedor,
            "lances": lances,
            "lances_com_marca": lances_com_marca,
            "desistencias": desistencias,
            "fornecedores_desistentes_ids": fornecedores_desistentes_ids,
            "resultado": resultado,
            "propostas_iniciais": propostas_iniciais,
            "propostas_pendentes": propostas_pendentes,
            "menor_valor_exibicao": menor_valor_exibicao,
            "menor_valor_origem": menor_valor_origem,
            "alerta_media": alerta_media,
            "modo_conferencia_alteracao": modo_conferencia_alteracao,
        },
    )

def salvar_propostas_iniciais(request, pregao, item_atual):
    if item_atual.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(request, "Só é possível registrar propostas iniciais em item em disputa.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    fornecedores_vinculos = (
        PregaoFornecedor.objects.filter(
            pregao=pregao,
            ativo_no_pregao=True,
        )
        .select_related("fornecedor")
        .order_by("ordem_inicial", "fornecedor__razao_social")
    )

    erros = 0
    salvos = 0

    for vinculo in fornecedores_vinculos:
        fornecedor = vinculo.fornecedor

        participa = request.POST.get(f"participa_{fornecedor.id}") == "on"
        marca = request.POST.get(f"marca_{fornecedor.id}", "").strip()
        preco_texto = request.POST.get(f"preco_inicial_{fornecedor.id}", "").strip()

        if not participa:
            PropostaInicialItem.objects.update_or_create(
                pregao=pregao,
                pregao_item=item_atual,
                fornecedor=fornecedor,
                defaults={
                    "participa": False,
                    "marca": "",
                    "preco_inicial": None,
                },
            )
            salvos += 1
            continue

        if not marca:
            messages.error(
                request,
                f"Informe a marca para o fornecedor {fornecedor.razao_social} ou desmarque a participação dele neste item.",
            )
            erros += 1
            continue

        if not preco_texto:
            messages.error(
                request,
                f"Informe o preço inicial para o fornecedor {fornecedor.razao_social} ou desmarque a participação dele neste item.",
            )
            erros += 1
            continue

        preco_texto = preco_texto.replace(",", ".")

        try:
            preco_inicial = Decimal(preco_texto)
        except InvalidOperation:
            messages.error(
                request,
                f"Preço inicial inválido para o fornecedor {fornecedor.razao_social}.",
            )
            erros += 1
            continue

        if preco_inicial <= 0:
            messages.error(
                request,
                f"Preço inicial deve ser maior que zero para o fornecedor {fornecedor.razao_social}.",
            )
            erros += 1
            continue

        PropostaInicialItem.objects.update_or_create(
            pregao=pregao,
            pregao_item=item_atual,
            fornecedor=fornecedor,
            defaults={
                "participa": True,
                "marca": marca,
                "preco_inicial": preco_inicial,
            },
        )

        salvos += 1

    if erros:
        messages.error(request, "Algumas propostas não foram salvas por erro de preenchimento.")
    else:
        definir_fornecedor_atual_se_necessario(pregao, item_atual)
        messages.success(request, f"Propostas iniciais salvas com sucesso. Registros salvos: {salvos}.")

    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)


def registrar_lance_fornecedor_atual(request, pregao, item_atual):
    if item_atual.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(request, "Este item não está mais em disputa.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    definir_fornecedor_atual_se_necessario(pregao, item_atual)
    item_atual.refresh_from_db()

    fornecedor = item_atual.fornecedor_atual

    if not fornecedor:
        messages.error(request, "Não há fornecedor ativo para registrar lance neste item.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    if fornecedor not in fornecedores_ativos_do_item(pregao, item_atual):
        messages.error(request, "Este fornecedor não participa ou não está ativo para este item.")
        definir_fornecedor_atual_se_necessario(pregao, item_atual)
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    desistiu = DesistenciaItem.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
        fornecedor=fornecedor,
    ).exists()

    if desistiu:
        messages.error(request, "O fornecedor atual já desistiu deste item.")
        proximo_fornecedor_da_rodada(pregao, item_atual)
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    valor_lance = request.POST.get("valor_lance")

    if not valor_lance:
        messages.error(request, "Informe o valor do lance.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    try:
        valor_lance_decimal = Decimal(valor_lance.replace(",", "."))

        if valor_lance_decimal <= 0:
            messages.error(request, "O valor do lance deve ser maior que zero.")
            return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

        alerta_media_lance = calcular_alerta_media(item_atual, valor_lance_decimal)

        if alerta_media_lance and request.POST.get("confirmar_media_lance") != "sim":
            messages.error(
                request,
                f"{alerta_media_lance['mensagem']} Edite o valor do lance ou confirme novamente para registrar mesmo fora da média.",
            )
            return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

        ultima_ordem = Lance.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
        ).aggregate(Max("ordem_lance"))["ordem_lance__max"]

        proxima_ordem = 1 if ultima_ordem is None else ultima_ordem + 1

        Lance.objects.create(
            pregao=pregao,
            pregao_item=item_atual,
            fornecedor=fornecedor,
            valor_lance=valor_lance_decimal,
            ordem_lance=proxima_ordem,
            registrado_por=request.user if request.user.is_authenticated else None,
        )

        proximo_fornecedor_da_rodada(pregao, item_atual)

        messages.success(request, f"Lance registrado para {fornecedor.razao_social}.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    except InvalidOperation:
        messages.error(request, "Valor do lance inválido.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    except Exception as erro:
        messages.error(request, f"Erro ao registrar lance: {erro}")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)


def registrar_desistencia_fornecedor_atual(request, pregao, item_atual):
    if item_atual.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(request, "Este item não está mais em disputa.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    definir_fornecedor_atual_se_necessario(pregao, item_atual)
    item_atual.refresh_from_db()

    fornecedor = item_atual.fornecedor_atual

    if not fornecedor:
        messages.error(request, "Não há fornecedor ativo para desistir neste item.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    if fornecedor not in fornecedores_ativos_do_item(pregao, item_atual):
        messages.error(request, "Este fornecedor não participa ou não está ativo para este item.")
        definir_fornecedor_atual_se_necessario(pregao, item_atual)
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    if DesistenciaItem.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
        fornecedor=fornecedor,
    ).exists():
        messages.error(request, "Este fornecedor já desistiu deste item.")
        proximo_fornecedor_da_rodada(pregao, item_atual)
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    DesistenciaItem.objects.create(
        pregao=pregao,
        pregao_item=item_atual,
        fornecedor=fornecedor,
        registrado_por=request.user if request.user.is_authenticated else None,
    )

    nome_fornecedor = fornecedor.razao_social

    proximo_fornecedor_da_rodada(pregao, item_atual)

    messages.success(request, f"Desistência registrada para {nome_fornecedor}.")
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)


def finalizar_item(request, pregao, item_atual):
    if item_atual.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(request, "Este item não está em disputa.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    lances_existentes = Lance.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    )

    if not lances_existentes.exists():
        messages.error(request, "Não é possível finalizar o item sem nenhum lance. Use Deserto ou Fracassado.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    melhores_lances = (
        lances_existentes
        .values("fornecedor")
        .annotate(melhor_valor=Min("valor_lance"))
        .order_by("melhor_valor")[:3]
    )

    classificados = list(melhores_lances)

    if not classificados:
        messages.error(request, "Não foi possível identificar o vencedor do item.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    valor_adjudicado = classificados[0]["melhor_valor"]
    alerta_media = calcular_alerta_media(item_atual, valor_adjudicado)

    if alerta_media and request.POST.get("confirmar_media") != "sim":
        messages.error(
            request,
            f"{alerta_media['mensagem']} Confira os dados e confirme a finalização do item, se desejar prosseguir.",
        )
        return redirect(f"{request.path}?confirmar_media=1")

    resultado, criado = ResultadoItem.objects.get_or_create(
        pregao=pregao,
        pregao_item=item_atual,
        defaults={
            "status_resultado": ResultadoItem.STATUS_ADJUDICADO,
        },
    )

    resultado.status_resultado = ResultadoItem.STATUS_ADJUDICADO

    resultado.primeiro_fornecedor = None
    resultado.primeiro_valor = None
    resultado.segundo_fornecedor = None
    resultado.segundo_valor = None
    resultado.terceiro_fornecedor = None
    resultado.terceiro_valor = None

    if len(classificados) >= 1:
        resultado.primeiro_fornecedor_id = classificados[0]["fornecedor"]
        resultado.primeiro_valor = classificados[0]["melhor_valor"]

    if len(classificados) >= 2:
        resultado.segundo_fornecedor_id = classificados[1]["fornecedor"]
        resultado.segundo_valor = classificados[1]["melhor_valor"]

    if len(classificados) >= 3:
        resultado.terceiro_fornecedor_id = classificados[2]["fornecedor"]
        resultado.terceiro_valor = classificados[2]["melhor_valor"]

    resultado.save()

    item_atual.status = PregaoItem.STATUS_ENCERRADO
    item_atual.encerrado_em = timezone.now()
    item_atual.save(update_fields=["status", "encerrado_em"])

    messages.success(request, "Item finalizado com sucesso.")
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)


def marcar_deserto(request, pregao, item_atual):
    if item_atual.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(request, "Este item não está em disputa.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    ResultadoItem.objects.update_or_create(
        pregao=pregao,
        pregao_item=item_atual,
        defaults={
            "status_resultado": ResultadoItem.STATUS_DESERTO,
            "primeiro_fornecedor": None,
            "primeiro_valor": None,
            "segundo_fornecedor": None,
            "segundo_valor": None,
            "terceiro_fornecedor": None,
            "terceiro_valor": None,
        },
    )

    item_atual.status = PregaoItem.STATUS_DESERTO
    item_atual.encerrado_em = timezone.now()
    item_atual.save(update_fields=["status", "encerrado_em"])

    messages.success(request, "Item marcado como deserto.")
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)


def marcar_fracassado(request, pregao, item_atual):
    if item_atual.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(request, "Este item não está em disputa.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    ResultadoItem.objects.update_or_create(
        pregao=pregao,
        pregao_item=item_atual,
        defaults={
            "status_resultado": ResultadoItem.STATUS_FRACASSADO,
            "primeiro_fornecedor": None,
            "primeiro_valor": None,
            "segundo_fornecedor": None,
            "segundo_valor": None,
            "terceiro_fornecedor": None,
            "terceiro_valor": None,
        },
    )

    item_atual.status = PregaoItem.STATUS_FRACASSADO
    item_atual.encerrado_em = timezone.now()
    item_atual.save(update_fields=["status", "encerrado_em"])

    messages.success(request, "Item marcado como fracassado.")
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)



def usuario_pode_alterar_lances_conferencia(request, pregao, item):
    if pregao.status == Pregao.STATUS_FINALIZADO:
        messages.error(request, "Este pregão já foi finalizado oficialmente e não permite alteração de lances.")
        return False

    modo_conferencia = request.session.get(
        f"conferencia_alteracao_item_{pregao.id}_{item.id}",
        False,
    )

    if not modo_conferencia:
        messages.error(
            request,
            "A alteração de lances só fica disponível quando o item é aberto pela Conferência Final do Pregão.",
        )
        return False

    if item.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(request, "Para alterar lances, o item precisa estar reaberto em disputa.")
        return False

    return True


def alterar_lance_conferencia(request, pregao_id, item_id, lance_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)
    item = get_object_or_404(PregaoItem, id=item_id, pregao=pregao)
    lance = get_object_or_404(Lance, id=lance_id, pregao=pregao, pregao_item=item)

    if request.method != "POST":
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)

    if not usuario_pode_alterar_lances_conferencia(request, pregao, item):
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)

    valor_texto = (request.POST.get("valor_lance") or "").strip()

    if not valor_texto:
        messages.error(request, "Informe o novo valor do lance.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)

    try:
        novo_valor = Decimal(valor_texto.replace(".", "").replace(",", ".") if "," in valor_texto else valor_texto)

        if novo_valor <= 0:
            messages.error(request, "O valor do lance deve ser maior que zero.")
            return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)

    except InvalidOperation:
        messages.error(request, "Valor do lance inválido.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)

    lance.valor_lance = novo_valor
    lance.save(update_fields=["valor_lance"])

    # Como o item foi reaberto pela conferência, o resultado será recalculado ao finalizar novamente.
    ResultadoItem.objects.filter(pregao=pregao, pregao_item=item).delete()

    messages.success(request, "Lance alterado com sucesso. Finalize o item novamente para recalcular os vencedores.")
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)


def excluir_lance_conferencia(request, pregao_id, item_id, lance_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)
    item = get_object_or_404(PregaoItem, id=item_id, pregao=pregao)
    lance = get_object_or_404(Lance, id=lance_id, pregao=pregao, pregao_item=item)

    if request.method != "POST":
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)

    if not usuario_pode_alterar_lances_conferencia(request, pregao, item):
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)

    ordem_excluida = lance.ordem_lance
    lance.delete()

    # Reordena os lances restantes do item para evitar buracos na numeração.
    lances_restantes = Lance.objects.filter(
        pregao=pregao,
        pregao_item=item,
    ).order_by("ordem_lance", "id")

    for nova_ordem, lance_restante in enumerate(lances_restantes, start=1):
        if lance_restante.ordem_lance != nova_ordem:
            lance_restante.ordem_lance = nova_ordem
            lance_restante.save(update_fields=["ordem_lance"])

    ResultadoItem.objects.filter(pregao=pregao, pregao_item=item).delete()

    messages.success(
        request,
        f"Lance nº {ordem_excluida} excluído com sucesso. Finalize o item novamente para recalcular os vencedores.",
    )
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)


def proximo_item(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    proximo = PregaoItem.objects.filter(
        pregao=pregao,
        status=PregaoItem.STATUS_PENDENTE,
    ).order_by("ordem").first()

    if proximo:
        proximo.status = PregaoItem.STATUS_EM_DISPUTA
        proximo.iniciado_em = timezone.now()
        proximo.rodada_atual = 1
        proximo.fornecedor_atual = None
        proximo.save(update_fields=["status", "iniciado_em", "rodada_atual", "fornecedor_atual"])

        definir_fornecedor_atual_se_necessario(pregao, proximo)

        return redirect(
            "execucao:tela_lances",
            pregao_id=pregao.id,
            item_id=proximo.id,
        )

    return redirect("execucao:fim_pregao", pregao_id=pregao.id)



def montar_linhas_conferencia_pregao(pregao):
    """
    Monta o resumo final do pregão para conferência antes da finalização.
    Exibe item, marca, unidade, quantidade e os 3 primeiros vencedores.
    """
    itens = (
        PregaoItem.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("ordem", "item__nome_item")
    )

    resultados = {
        resultado.pregao_item_id: resultado
        for resultado in ResultadoItem.objects.filter(pregao=pregao)
        .select_related(
            "pregao_item",
            "primeiro_fornecedor",
            "segundo_fornecedor",
            "terceiro_fornecedor",
        )
    }

    propostas = {
        (proposta.pregao_item_id, proposta.fornecedor_id): proposta
        for proposta in PropostaInicialItem.objects.filter(pregao=pregao)
        .select_related("pregao_item", "fornecedor")
    }

    linhas = []

    for item_pregao in itens:
        resultado = resultados.get(item_pregao.id)

        primeiro = {"fornecedor": "-", "marca": "-", "valor": None}
        segundo = {"fornecedor": "-", "marca": "-", "valor": None}
        terceiro = {"fornecedor": "-", "marca": "-", "valor": None}

        marca_principal = "-"

        if item_pregao.status == PregaoItem.STATUS_DESERTO:
            primeiro["fornecedor"] = "DESERTO"
            marca_principal = "DESERTO"

        elif item_pregao.status == PregaoItem.STATUS_FRACASSADO:
            primeiro["fornecedor"] = "FRACASSADO"
            marca_principal = "FRACASSADO"

        elif resultado:
            if resultado.primeiro_fornecedor:
                proposta = propostas.get((item_pregao.id, resultado.primeiro_fornecedor_id))
                marca = proposta.marca if proposta else ""
                primeiro = {
                    "fornecedor": resultado.primeiro_fornecedor.razao_social,
                    "marca": marca or "-",
                    "valor": resultado.primeiro_valor,
                }
                marca_principal = marca or "-"

            if resultado.segundo_fornecedor:
                proposta = propostas.get((item_pregao.id, resultado.segundo_fornecedor_id))
                segundo = {
                    "fornecedor": resultado.segundo_fornecedor.razao_social,
                    "marca": proposta.marca if proposta and proposta.marca else "-",
                    "valor": resultado.segundo_valor,
                }

            if resultado.terceiro_fornecedor:
                proposta = propostas.get((item_pregao.id, resultado.terceiro_fornecedor_id))
                terceiro = {
                    "fornecedor": resultado.terceiro_fornecedor.razao_social,
                    "marca": proposta.marca if proposta and proposta.marca else "-",
                    "valor": resultado.terceiro_valor,
                }

        linhas.append(
            {
                "item_pregao": item_pregao,
                "ordem": item_pregao.ordem,
                "produto": item_pregao.item.nome_item,
                "marca": marca_principal,
                "unidade": item_pregao.item.get_unidade_medida_display(),
                "quantidade": item_pregao.quantidade_total,
                "status": item_pregao.get_status_display(),
                "resultado": resultado,
                "primeiro": primeiro,
                "segundo": segundo,
                "terceiro": terceiro,
            }
        )

    return linhas


def conferencia_pregao(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    total_itens = PregaoItem.objects.filter(pregao=pregao).count()
    itens_encerrados = PregaoItem.objects.filter(
        pregao=pregao,
        status__in=[
            PregaoItem.STATUS_ENCERRADO,
            PregaoItem.STATUS_DESERTO,
            PregaoItem.STATUS_FRACASSADO,
        ],
    ).count()

    itens_abertos = PregaoItem.objects.filter(
        pregao=pregao,
        status__in=[
            PregaoItem.STATUS_PENDENTE,
            PregaoItem.STATUS_EM_DISPUTA,
        ],
    ).exists()

    linhas_conferencia = montar_linhas_conferencia_pregao(pregao)

    return render(
        request,
        "execucao/conferencia_pregao.html",
        {
            "pregao": pregao,
            "total_itens": total_itens,
            "itens_encerrados": itens_encerrados,
            "itens_abertos": itens_abertos,
            "linhas_conferencia": linhas_conferencia,
        },
    )


def alterar_item_conferencia(request, pregao_id, item_id):
    """
    Reabre um item encerrado para conferência/correção de lances.
    Os lances e propostas já registrados são mantidos para edição/continuidade.
    O resultado anterior do item é removido, pois será recalculado ao finalizar novamente.
    """
    pregao = get_object_or_404(Pregao, id=pregao_id)
    item = get_object_or_404(PregaoItem, id=item_id, pregao=pregao)

    if pregao.status == Pregao.STATUS_FINALIZADO:
        messages.error(request, "Este pregão já foi finalizado oficialmente e não pode ser alterado pela conferência.")
        return redirect("execucao:conferencia_pregao", pregao_id=pregao.id)

    if request.method != "POST":
        return redirect("execucao:conferencia_pregao", pregao_id=pregao.id)

    PregaoItem.objects.filter(
        pregao=pregao,
        status=PregaoItem.STATUS_EM_DISPUTA,
    ).exclude(id=item.id).update(
        status=PregaoItem.STATUS_ENCERRADO,
        fornecedor_atual=None,
    )

    ResultadoItem.objects.filter(
        pregao=pregao,
        pregao_item=item,
    ).delete()

    item.status = PregaoItem.STATUS_EM_DISPUTA
    item.encerrado_em = None
    item.iniciado_em = item.iniciado_em or timezone.now()
    item.fornecedor_atual = None
    item.save(update_fields=["status", "encerrado_em", "iniciado_em", "fornecedor_atual"])

    definir_fornecedor_atual_se_necessario(pregao, item)

    request.session[f"conferencia_alteracao_item_{pregao.id}_{item.id}"] = True
    request.session.modified = True

    messages.success(
        request,
        "Item reaberto para conferência. Ajuste os lances/propostas e finalize o item novamente.",
    )
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item.id)


def fim_pregao(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    total_itens = PregaoItem.objects.filter(pregao=pregao).count()
    itens_encerrados = PregaoItem.objects.filter(
        pregao=pregao,
        status__in=[
            PregaoItem.STATUS_ENCERRADO,
            PregaoItem.STATUS_DESERTO,
            PregaoItem.STATUS_FRACASSADO,
        ],
    ).count()

    return render(
        request,
        "execucao/fim_pregao.html",
        {
            "pregao": pregao,
            "total_itens": total_itens,
            "itens_encerrados": itens_encerrados,
        },
    )


def finalizar_pregao(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if request.method != "POST":
        return redirect("execucao:conferencia_pregao", pregao_id=pregao.id)

    itens_abertos = PregaoItem.objects.filter(
        pregao=pregao,
        status__in=[
            PregaoItem.STATUS_PENDENTE,
            PregaoItem.STATUS_EM_DISPUTA,
        ],
    ).exists()

    if itens_abertos:
        messages.error(
            request,
            "Não é possível finalizar o pregão porque ainda existem itens pendentes ou em disputa.",
        )
        return redirect("execucao:conferencia_pregao", pregao_id=pregao.id)

    pregao.status = Pregao.STATUS_FINALIZADO
    pregao.finalizado_em = timezone.now()
    pregao.save(update_fields=["status", "finalizado_em", "atualizado_em"])

    messages.success(request, "Pregão finalizado com sucesso.")
    return redirect("execucao:pregao_finalizado", pregao_id=pregao.id)


def pregao_finalizado(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    return render(
        request,
        "execucao/pregao_finalizado.html",
        {
            "pregao": pregao,
        },
    )