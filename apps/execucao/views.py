from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.db import transaction
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
from .models import BeneficioMEEPP

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




def _melhores_valores_fornecedores(pregao, item_atual):
    return list(
        Lance.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
        )
        .values("fornecedor")
        .annotate(melhor_valor=Min("valor_lance"))
        .order_by("melhor_valor", "fornecedor")
    )


def _sincronizar_beneficios_me_epp(pregao, item_atual, classificados):
    BeneficioMEEPP.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    ).delete()

    if not classificados:
        return None

    vencedor_id = classificados[0]["fornecedor"]
    valor_referencia = classificados[0]["melhor_valor"]

    vencedor = PregaoFornecedor.objects.filter(
        pregao=pregao,
        fornecedor_id=vencedor_id,
        ativo_no_pregao=True,
    ).select_related("fornecedor").first()

    if not vencedor or getattr(vencedor.fornecedor, "fornecedor_me_epp", False):
        return None

    limite = valor_referencia * Decimal("1.05")
    candidatos = []

    for classificado in classificados[1:]:
        vinculo = PregaoFornecedor.objects.filter(
            pregao=pregao,
            fornecedor_id=classificado["fornecedor"],
            ativo_no_pregao=True,
        ).select_related("fornecedor").first()

        if not vinculo:
            continue

        fornecedor = vinculo.fornecedor

        if not getattr(fornecedor, "fornecedor_me_epp", False):
            continue

        if classificado["melhor_valor"] <= limite:
            candidatos.append((fornecedor, classificado["melhor_valor"]))

    for ordem, (fornecedor, valor) in enumerate(candidatos, start=1):
        BeneficioMEEPP.objects.create(
            pregao=pregao,
            pregao_item=item_atual,
            fornecedor=fornecedor,
            ordem_convocacao=ordem,
            valor_referencia=valor_referencia,
            valor_original_me_epp=valor,
            percentual_margem=Decimal("5.00"),
            status=BeneficioMEEPP.STATUS_PENDENTE,
        )

    return _beneficio_me_epp_atual(pregao, item_atual)


def _beneficio_me_epp_atual(pregao, item_atual):
    return (
        BeneficioMEEPP.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
            status=BeneficioMEEPP.STATUS_PENDENTE,
        )
        .select_related("fornecedor")
        .order_by("ordem_convocacao", "id")
        .first()
    )


def _resultado_apos_beneficio(pregao, item_atual, beneficio, nova_oferta):
    resultado = get_object_or_404(
        ResultadoItem,
        pregao=pregao,
        pregao_item=item_atual,
    )

    classificados = _melhores_valores_fornecedores(pregao, item_atual)
    antigo_primeiro_id = resultado.primeiro_fornecedor_id
    antigo_primeiro_valor = resultado.primeiro_valor

    nova_classificacao = [
        {"fornecedor": beneficio.fornecedor_id, "melhor_valor": nova_oferta}
    ]

    if antigo_primeiro_id and antigo_primeiro_id != beneficio.fornecedor_id:
        nova_classificacao.append(
            {"fornecedor": antigo_primeiro_id, "melhor_valor": antigo_primeiro_valor}
        )

    for classificado in classificados:
        if classificado["fornecedor"] in {beneficio.fornecedor_id, antigo_primeiro_id}:
            continue
        nova_classificacao.append(classificado)

    nova_classificacao = nova_classificacao[:3]

    resultado.primeiro_fornecedor = None
    resultado.primeiro_valor = None
    resultado.segundo_fornecedor = None
    resultado.segundo_valor = None
    resultado.terceiro_fornecedor = None
    resultado.terceiro_valor = None

    if len(nova_classificacao) >= 1:
        resultado.primeiro_fornecedor_id = nova_classificacao[0]["fornecedor"]
        resultado.primeiro_valor = nova_classificacao[0]["melhor_valor"]
    if len(nova_classificacao) >= 2:
        resultado.segundo_fornecedor_id = nova_classificacao[1]["fornecedor"]
        resultado.segundo_valor = nova_classificacao[1]["melhor_valor"]
    if len(nova_classificacao) >= 3:
        resultado.terceiro_fornecedor_id = nova_classificacao[2]["fornecedor"]
        resultado.terceiro_valor = nova_classificacao[2]["melhor_valor"]

    resultado.save()

    item_atual.fornecedor_vencedor = beneficio.fornecedor
    item_atual.menor_lance = nova_oferta
    item_atual.save(update_fields=["fornecedor_vencedor", "menor_lance"])

    return resultado


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

    # Ao navegar diretamente para um item ainda pendente, ele passa
    # automaticamente a ficar disponível para disputa.
    # Isso permite pular itens, disputar fornecedores específicos e
    # retornar depois aos itens anteriores.
    if (
        pregao.status != Pregao.STATUS_FINALIZADO
        and item_atual.status == PregaoItem.STATUS_PENDENTE
    ):
        item_atual.status = PregaoItem.STATUS_EM_DISPUTA
        item_atual.iniciado_em = timezone.now()
        item_atual.rodada_atual = 1
        item_atual.fornecedor_atual = None
        item_atual.save(
            update_fields=[
                "status",
                "iniciado_em",
                "rodada_atual",
                "fornecedor_atual",
            ]
        )

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

    # Navegação livre entre os itens do pregão.
    # Esta navegação é apenas visual: abrir um item pendente não altera seu status.
    itens_navegacao = list(
        PregaoItem.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("ordem", "item__nome_item")
    )

    item_anterior = (
        PregaoItem.objects.filter(
            pregao=pregao,
            ordem__lt=item_atual.ordem,
        )
        .select_related("item")
        .order_by("-ordem")
        .first()
    )

    item_proximo_navegacao = (
        PregaoItem.objects.filter(
            pregao=pregao,
            ordem__gt=item_atual.ordem,
        )
        .select_related("item")
        .order_by("ordem")
        .first()
    )

    pode_editar_lances_execucao = (
        pregao.status != Pregao.STATUS_FINALIZADO
        and item_atual.status == PregaoItem.STATUS_EM_DISPUTA
    )

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

    beneficio_me_epp_atual = _beneficio_me_epp_atual(pregao, item_atual)
    beneficios_me_epp = list(
        BeneficioMEEPP.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
        )
        .select_related("fornecedor")
        .order_by("ordem_convocacao", "id")
    )
    beneficio_me_epp_pendente = beneficio_me_epp_atual is not None

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
            "beneficio_me_epp_atual": beneficio_me_epp_atual,
            "beneficios_me_epp": beneficios_me_epp,
            "beneficio_me_epp_pendente": beneficio_me_epp_pendente,
            "itens_navegacao": itens_navegacao,
            "item_anterior": item_anterior,
            "item_proximo_navegacao": item_proximo_navegacao,
            "pode_editar_lances_execucao": pode_editar_lances_execucao,
            "exibir_acoes_lance": (
                modo_conferencia_alteracao or pode_editar_lances_execucao
            ),
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
        # Se a disputa ainda não começou, remove qualquer fornecedor atual
        # gravado pela ordenação antiga. A função abaixo recalculará o primeiro
        # fornecedor usando a maior proposta inicial.
        if not Lance.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
        ).exists():
            item_atual.fornecedor_atual = None
            item_atual.rodada_atual = 1
            item_atual.save(
                update_fields=["fornecedor_atual", "rodada_atual"]
            )

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

    classificados = _melhores_valores_fornecedores(pregao, item_atual)

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
        defaults={"status_resultado": ResultadoItem.STATUS_ADJUDICADO},
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

    item_atual.fornecedor_vencedor_id = classificados[0]["fornecedor"]
    item_atual.menor_lance = classificados[0]["melhor_valor"]
    item_atual.status = PregaoItem.STATUS_ENCERRADO
    item_atual.encerrado_em = timezone.now()
    item_atual.save(
        update_fields=["fornecedor_vencedor", "menor_lance", "status", "encerrado_em"]
    )

    beneficio_atual = _sincronizar_beneficios_me_epp(pregao, item_atual, classificados)

    if beneficio_atual:
        messages.warning(
            request,
            "Item finalizado provisoriamente. Existe ME/EPP dentro da faixa de até 5% do melhor valor. Resolva o benefício ME/EPP antes de avançar para o próximo item.",
        )
    else:
        messages.success(request, "Item finalizado com sucesso.")

    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)


@transaction.atomic
def exercer_beneficio_me_epp(request, pregao_id, item_id, beneficio_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)
    item_atual = get_object_or_404(PregaoItem, id=item_id, pregao=pregao)
    beneficio = get_object_or_404(
        BeneficioMEEPP.objects.select_related("fornecedor"),
        id=beneficio_id,
        pregao=pregao,
        pregao_item=item_atual,
    )

    if request.method != "POST":
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    beneficio_atual = _beneficio_me_epp_atual(pregao, item_atual)
    if not beneficio_atual or beneficio_atual.id != beneficio.id:
        messages.error(request, "Este fornecedor não é o próximo ME/EPP habilitado para exercer o benefício.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    valor_texto = (request.POST.get("nova_oferta_me_epp") or "").strip()
    if not valor_texto:
        messages.error(request, "Informe a nova oferta da ME/EPP.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    try:
        nova_oferta = Decimal(
            valor_texto.replace(".", "").replace(",", ".") if "," in valor_texto else valor_texto
        )
    except InvalidOperation:
        messages.error(request, "Nova oferta ME/EPP inválida.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    if nova_oferta <= 0:
        messages.error(request, "A nova oferta ME/EPP deve ser maior que zero.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    if nova_oferta >= beneficio.valor_referencia:
        messages.error(
            request,
            f"Para exercer o benefício, a nova oferta da ME/EPP deve ser inferior ao melhor valor atual de R$ {beneficio.valor_referencia:.2f}.",
        )
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    # Registra a oferta vencedora do benefício também como Lance.
    # Assim ela passa a integrar o histórico oficial e a Planilha de Lances,
    # sem perder o registro específico do benefício ME/EPP.
    ultima_ordem = Lance.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    ).aggregate(Max("ordem_lance"))["ordem_lance__max"]

    proxima_ordem = 1 if ultima_ordem is None else ultima_ordem + 1

    Lance.objects.create(
        pregao=pregao,
        pregao_item=item_atual,
        fornecedor=beneficio.fornecedor,
        valor_lance=nova_oferta,
        ordem_lance=proxima_ordem,
        registrado_por=request.user if request.user.is_authenticated else None,
    )

    beneficio.nova_oferta = nova_oferta
    beneficio.status = BeneficioMEEPP.STATUS_EXERCIDO
    beneficio.registrado_por = request.user if request.user.is_authenticated else None
    beneficio.save(update_fields=["nova_oferta", "status", "registrado_por", "atualizado_em"])

    BeneficioMEEPP.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
        status=BeneficioMEEPP.STATUS_PENDENTE,
    ).exclude(id=beneficio.id).update(status=BeneficioMEEPP.STATUS_ENCERRADO)

    _resultado_apos_beneficio(pregao, item_atual, beneficio, nova_oferta)

    messages.success(
        request,
        f"Benefício ME/EPP exercido por {beneficio.fornecedor.razao_social}. A classificação do item foi atualizada.",
    )
    return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)


@transaction.atomic
def recusar_beneficio_me_epp(request, pregao_id, item_id, beneficio_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)
    item_atual = get_object_or_404(PregaoItem, id=item_id, pregao=pregao)
    beneficio = get_object_or_404(
        BeneficioMEEPP.objects.select_related("fornecedor"),
        id=beneficio_id,
        pregao=pregao,
        pregao_item=item_atual,
    )

    if request.method != "POST":
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    beneficio_atual = _beneficio_me_epp_atual(pregao, item_atual)
    if not beneficio_atual or beneficio_atual.id != beneficio.id:
        messages.error(request, "Este fornecedor não é o próximo ME/EPP habilitado para responder ao benefício.")
        return redirect("execucao:tela_lances", pregao_id=pregao.id, item_id=item_atual.id)

    beneficio.status = BeneficioMEEPP.STATUS_RECUSADO
    beneficio.registrado_por = request.user if request.user.is_authenticated else None
    beneficio.save(update_fields=["status", "registrado_por", "atualizado_em"])

    proximo = _beneficio_me_epp_atual(pregao, item_atual)
    if proximo:
        messages.warning(
            request,
            f"{beneficio.fornecedor.razao_social} não exerceu o benefício. A próxima ME/EPP habilitada é {proximo.fornecedor.razao_social}.",
        )
    else:
        messages.success(
            request,
            "A ME/EPP não exerceu o benefício e não há outra ME/EPP habilitada. Mantida a classificação original do item.",
        )

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
    """
    Permite corrigir lances durante a execução normal e também no fluxo
    de reabertura pela Conferência Final.
    """
    if pregao.status == Pregao.STATUS_FINALIZADO:
        messages.error(
            request,
            "Este pregão já foi finalizado oficialmente e não permite alteração de lances.",
        )
        return False

    if item.status != PregaoItem.STATUS_EM_DISPUTA:
        messages.error(
            request,
            "Para alterar um lance, o item precisa estar em disputa.",
        )
        return False

    if pregao.status == Pregao.STATUS_EM_ANDAMENTO:
        return True

    modo_conferencia = request.session.get(
        f"conferencia_alteracao_item_{pregao.id}_{item.id}",
        False,
    )

    if modo_conferencia:
        return True

    messages.error(
        request,
        "Este item não está disponível para alteração de lances.",
    )
    return False


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

    resultado_anterior_excluido = ResultadoItem.objects.filter(
        pregao=pregao,
        pregao_item=item,
    ).delete()[0] > 0

    if resultado_anterior_excluido:
        messages.success(
            request,
            "Lance alterado com sucesso. Finalize o item novamente para recalcular os vencedores.",
        )
    else:
        messages.success(
            request,
            "Lance alterado com sucesso. O menor lance e o vencedor atual foram recalculados.",
        )

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

    beneficio_pendente = (
        BeneficioMEEPP.objects.filter(
            pregao=pregao,
            status=BeneficioMEEPP.STATUS_PENDENTE,
        )
        .select_related("pregao_item")
        .order_by("pregao_item__ordem", "ordem_convocacao")
        .first()
    )
    if beneficio_pendente:
        messages.error(
            request,
            "Resolva o benefício ME/EPP pendente antes de avançar para o próximo item.",
        )
        return redirect(
            "execucao:tela_lances",
            pregao_id=pregao.id,
            item_id=beneficio_pendente.pregao_item_id,
        )

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

    beneficios_por_item = {}
    for beneficio in (
        BeneficioMEEPP.objects.filter(pregao=pregao)
        .select_related("fornecedor")
        .order_by("pregao_item_id", "ordem_convocacao", "id")
    ):
        beneficios_por_item.setdefault(beneficio.pregao_item_id, []).append(beneficio)

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
                "beneficios_me_epp": beneficios_por_item.get(item_pregao.id, []),
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

    BeneficioMEEPP.objects.filter(
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

    if BeneficioMEEPP.objects.filter(
        pregao=pregao,
        status=BeneficioMEEPP.STATUS_PENDENTE,
    ).exists():
        messages.error(
            request,
            "Não é possível finalizar oficialmente o pregão enquanto houver benefício ME/EPP pendente.",
        )
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