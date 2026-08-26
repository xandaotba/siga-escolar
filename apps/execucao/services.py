from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.pregoes.models import (
    DesistenciaItem,
    Lance,
    Pregao,
    PregaoFornecedor,
    PregaoItem,
    PropostaInicialItem,
    QuantitativoPregao,
)


def fornecedores_participantes_do_item(pregao, item_atual):
    """
    Retorna os fornecedores vinculados ao pregão que participam do item.

    Ordem dos lances:
    - Quando existem propostas iniciais cadastradas, os fornecedores participantes
      são ordenados do MAIOR para o MENOR preço inicial.
    - Em caso de empate no preço inicial, usa a ordem_inicial do fornecedor no
      pregão e, depois, a razão social como critérios de desempate.
    - Se ainda não existe nenhuma proposta inicial cadastrada para o item,
      mantém a ordem original do vínculo do pregão.
    """
    vinculos = list(
        PregaoFornecedor.objects.filter(
            pregao=pregao,
            ativo_no_pregao=True,
        )
        .select_related("fornecedor")
        .order_by("ordem_inicial", "fornecedor__razao_social")
    )

    propostas_item = list(
        PropostaInicialItem.objects.filter(
            pregao=pregao,
            pregao_item=item_atual,
        )
        .select_related("fornecedor")
    )

    # Sem propostas cadastradas, preserva o comportamento anterior.
    if not propostas_item:
        return [v.fornecedor for v in vinculos]

    propostas_participantes = {
        proposta.fornecedor_id: proposta
        for proposta in propostas_item
        if proposta.participa
    }

    vinculos_participantes = [
        vinculo
        for vinculo in vinculos
        if vinculo.fornecedor_id in propostas_participantes
    ]

    def chave_ordenacao(vinculo):
        proposta = propostas_participantes[vinculo.fornecedor_id]

        # Preço ausente vai para o final. Normalmente isso não ocorre porque
        # a proposta participante exige preço inicial no cadastro.
        sem_preco = proposta.preco_inicial is None
        preco = proposta.preco_inicial or 0

        ordem_inicial = (
            vinculo.ordem_inicial
            if vinculo.ordem_inicial is not None
            else 999999
        )

        return (
            sem_preco,
            -preco,
            ordem_inicial,
            vinculo.fornecedor.razao_social.lower(),
        )

    vinculos_participantes.sort(key=chave_ordenacao)

    return [v.fornecedor for v in vinculos_participantes]


def fornecedores_ativos_do_item(pregao, item_atual):
    """
    Retorna os fornecedores participantes do item que ainda não desistiram.
    A ordem usada é a ordem_inicial definida no vínculo do pregão.
    """
    desistentes_ids = DesistenciaItem.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    ).values_list("fornecedor_id", flat=True)

    fornecedores = fornecedores_participantes_do_item(pregao, item_atual)

    return [
        fornecedor
        for fornecedor in fornecedores
        if fornecedor.id not in desistentes_ids
    ]


def definir_fornecedor_atual_se_necessario(pregao, item_atual):
    """
    Define o fornecedor atual conforme a ordem da disputa.

    Antes do primeiro lance:
    - força o primeiro fornecedor da lista ordenada pelas propostas iniciais;
    - isso corrige itens que já tinham fornecedor_atual gravado pela regra antiga.

    Depois que já existe pelo menos um lance:
    - preserva o fornecedor atual da rodada;
    - somente corrige se ele deixou de participar ou desistiu.
    """
    fornecedores_ativos = fornecedores_ativos_do_item(pregao, item_atual)

    if not fornecedores_ativos:
        item_atual.fornecedor_atual = None
        item_atual.save(update_fields=["fornecedor_atual"])
        return None

    existe_lance = Lance.objects.filter(
        pregao=pregao,
        pregao_item=item_atual,
    ).exists()

    # Antes do primeiro lance, o primeiro fornecedor deve SEMPRE ser
    # o de maior proposta inicial, mesmo que fornecedor_atual tenha
    # ficado gravado anteriormente pela ordenação antiga.
    if not existe_lance:
        primeiro_fornecedor = fornecedores_ativos[0]

        if item_atual.fornecedor_atual_id != primeiro_fornecedor.id:
            item_atual.fornecedor_atual = primeiro_fornecedor
            item_atual.rodada_atual = 1
            item_atual.save(
                update_fields=["fornecedor_atual", "rodada_atual"]
            )

        return primeiro_fornecedor

    fornecedor_atual_valido = False

    if item_atual.fornecedor_atual:
        fornecedor_atual_valido = any(
            f.id == item_atual.fornecedor_atual_id
            for f in fornecedores_ativos
        )

    if not item_atual.fornecedor_atual or not fornecedor_atual_valido:
        item_atual.fornecedor_atual = fornecedores_ativos[0]
        item_atual.save(update_fields=["fornecedor_atual"])

    return item_atual.fornecedor_atual


def proximo_fornecedor_da_rodada(pregao, item_atual):
    """
    Avança o fornecedor atual para o próximo fornecedor participante e ativo.
    Quando chega ao último, volta para o primeiro e aumenta a rodada.
    """
    fornecedores_ativos = fornecedores_ativos_do_item(pregao, item_atual)

    if not fornecedores_ativos:
        item_atual.fornecedor_atual = None
        item_atual.save(update_fields=["fornecedor_atual"])
        return None

    if not item_atual.fornecedor_atual:
        item_atual.fornecedor_atual = fornecedores_ativos[0]
        item_atual.save(update_fields=["fornecedor_atual"])
        return item_atual.fornecedor_atual

    ids = [f.id for f in fornecedores_ativos]

    if item_atual.fornecedor_atual_id not in ids:
        item_atual.fornecedor_atual = fornecedores_ativos[0]
        item_atual.save(update_fields=["fornecedor_atual"])
        return item_atual.fornecedor_atual

    indice_atual = ids.index(item_atual.fornecedor_atual_id)
    proximo_indice = indice_atual + 1

    if proximo_indice >= len(fornecedores_ativos):
        proximo_indice = 0
        item_atual.rodada_atual += 1

    item_atual.fornecedor_atual = fornecedores_ativos[proximo_indice]
    item_atual.save(update_fields=["fornecedor_atual", "rodada_atual"])

    return item_atual.fornecedor_atual


def obter_proximo_fornecedor_visual(pregao, item_atual):
    """
    Apenas calcula quem será o próximo fornecedor participante, sem salvar nada.
    Usado para mostrar na tela.
    """
    fornecedores_ativos = fornecedores_ativos_do_item(pregao, item_atual)

    if not fornecedores_ativos:
        return None

    if not item_atual.fornecedor_atual:
        return fornecedores_ativos[0]

    ids = [f.id for f in fornecedores_ativos]

    if item_atual.fornecedor_atual_id not in ids:
        return fornecedores_ativos[0]

    indice_atual = ids.index(item_atual.fornecedor_atual_id)
    proximo_indice = indice_atual + 1

    if proximo_indice >= len(fornecedores_ativos):
        proximo_indice = 0

    return fornecedores_ativos[proximo_indice]


@transaction.atomic
def iniciar_ou_continuar_pregao(pregao):
    """
    Inicia um pregão ainda não iniciado ou continua um pregão em andamento.
    """

    if pregao.status == Pregao.STATUS_FINALIZADO:
        raise ValidationError("Este pregão já foi finalizado.")

    quantitativos = QuantitativoPregao.objects.filter(pregao=pregao).select_related("item")

    if not quantitativos.exists():
        raise ValidationError("Este pregão não possui quantitativos cadastrados.")

    if not pregao.fornecedores.exists():
        raise ValidationError("Este pregão não possui fornecedores vinculados.")

    if not PregaoItem.objects.filter(pregao=pregao).exists():
        ordem = 1

        for q in quantitativos.order_by("item__nome_item"):
            PregaoItem.objects.create(
                pregao=pregao,
                item=q.item,
                ordem=ordem,
                status=PregaoItem.STATUS_PENDENTE,
                quantidade_total=q.quantidade,
            )
            ordem += 1

    if pregao.status == Pregao.STATUS_NAO_INICIADO:
        pregao.status = Pregao.STATUS_EM_ANDAMENTO
        pregao.save(update_fields=["status", "atualizado_em"])

    item_atual = PregaoItem.objects.filter(
        pregao=pregao,
        status=PregaoItem.STATUS_EM_DISPUTA,
    ).order_by("ordem").first()

    if not item_atual:
        item_atual = PregaoItem.objects.filter(
            pregao=pregao,
            status=PregaoItem.STATUS_PENDENTE,
        ).order_by("ordem").first()

        if item_atual:
            item_atual.status = PregaoItem.STATUS_EM_DISPUTA
            item_atual.iniciado_em = timezone.now()
            item_atual.rodada_atual = 1
            item_atual.save(update_fields=["status", "iniciado_em", "rodada_atual"])

    if not item_atual:
        raise ValidationError("Não há itens pendentes para executar neste pregão.")

    definir_fornecedor_atual_se_necessario(pregao, item_atual)

    return item_atual
