from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render

from django.http import HttpResponse, JsonResponse
from apps.cadastros.models import Escola, Item
from .forms import OrdenadorDespesasForm, PregaoForm

from io import BytesIO
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from docx import Document

def paginar_queryset(request, queryset, por_pagina=25):
    paginator = Paginator(queryset, por_pagina)
    numero_pagina = request.GET.get("page")
    return paginator.get_page(numero_pagina)


from .models import (
    Pregao,
    PregaoItem,
    PregaoFornecedor,
    QuantitativoEscola,
    QuantitativoPregao,
    PropostaInicialItem,
)



def _substituir_texto_celula_preservando_rotulo(cell, valor, rotulo=None):
    """
    Preenche uma célula do modelo mantendo o rótulo em negrito quando existir.
    """
    valor = str(valor or "").strip() or "-"

    paragraph = cell.paragraphs[0]

    if rotulo:
        # Mantém o primeiro run (rótulo) do modelo e substitui apenas o conteúdo.
        if not paragraph.runs:
            run_rotulo = paragraph.add_run(rotulo)
            run_rotulo.bold = True
            paragraph.add_run(f" {valor}")
            return

        paragraph.runs[0].text = rotulo
        paragraph.runs[0].bold = True

        if len(paragraph.runs) >= 2:
            paragraph.runs[1].text = f" {valor}"
            for run in paragraph.runs[2:]:
                run.text = ""
        else:
            paragraph.add_run(f" {valor}")
    else:
        if paragraph.runs:
            paragraph.runs[0].text = valor
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            paragraph.add_run(valor)


def _preencher_tabela_fornecedor(table, fornecedor):
    _substituir_texto_celula_preservando_rotulo(
        table.cell(0, 1),
        fornecedor.razao_social,
    )
    _substituir_texto_celula_preservando_rotulo(
        table.cell(1, 1),
        fornecedor.cnpj,
    )
    _substituir_texto_celula_preservando_rotulo(
        table.cell(2, 1),
        fornecedor.endereco,
    )
    _substituir_texto_celula_preservando_rotulo(
        table.cell(3, 1),
        fornecedor.representante_legal,
        "NOME:",
    )
    _substituir_texto_celula_preservando_rotulo(
        table.cell(4, 1),
        fornecedor.cpf_representante,
        "CPF:",
    )
    identidade = fornecedor.rg_representante or ""
    orgao = fornecedor.orgao_expedidor_representante or ""

    if identidade and orgao:
        identidade_completa = f"{identidade} - {orgao}"
    else:
        identidade_completa = identidade or orgao

    _substituir_texto_celula_preservando_rotulo(
        table.cell(5, 1),
        identidade_completa,
        "IDENTIDADE:",
    )
    _substituir_texto_celula_preservando_rotulo(
        table.cell(6, 1),
        fornecedor.telefone,
    )


def relacao_fornecedores_word(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    # Este documento é exclusivo dos Pregões Presenciais.
    if not pregao.eh_pregao_presencial:
        messages.error(
            request,
            "A Relação de Fornecedores está disponível somente para Pregões Presenciais.",
        )
        return redirect("pregoes:pregoes")

    fornecedores_pregao = list(
        PregaoFornecedor.objects.filter(
            pregao=pregao,
            ativo_no_pregao=True,
        )
        .select_related("fornecedor")
        .order_by("ordem_inicial", "fornecedor__razao_social")
    )

    if not fornecedores_pregao:
        messages.warning(
            request,
            "Não há fornecedores cadastrados para este pregão.",
        )
        return redirect("pregoes:pregoes")

    caminho_modelo = (
        Path(settings.BASE_DIR)
        / "templates"
        / "documentos"
        / "modelo_relacao_fornecedores_pregao.docx"
    )

    if not caminho_modelo.exists():
        messages.error(
            request,
            "O modelo Word da Relação de Fornecedores não foi encontrado no servidor.",
        )
        return redirect("pregoes:pregoes")

    document = Document(caminho_modelo)

    if not document.tables:
        messages.error(
            request,
            "O modelo Word da Relação de Fornecedores não possui a tabela esperada.",
        )
        return redirect("pregoes:pregoes")

    tabela_modelo = document.tables[0]

    # Guarda uma cópia da tabela original antes do preenchimento.
    tabela_xml_modelo = deepcopy(tabela_modelo._tbl)

    # Primeiro fornecedor usa a própria tabela existente no modelo.
    _preencher_tabela_fornecedor(
        tabela_modelo,
        fornecedores_pregao[0].fornecedor,
    )

    # Os demais fornecedores recebem uma cópia idêntica da tabela do modelo.
    elemento_anterior = tabela_modelo._tbl

    for vinculo in fornecedores_pregao[1:]:
        # Espaço entre as tabelas.
        paragrafo_espaco = document.add_paragraph()
        elemento_anterior.addnext(paragrafo_espaco._p)
        elemento_anterior = paragrafo_espaco._p

        nova_tabela_xml = deepcopy(tabela_xml_modelo)
        elemento_anterior.addnext(nova_tabela_xml)
        elemento_anterior = nova_tabela_xml

        # Localiza a tabela recém-inserida para preencher seus dados.
        nova_tabela = document.tables[-1]
        _preencher_tabela_fornecedor(
            nova_tabela,
            vinculo.fornecedor,
        )

    arquivo = BytesIO()
    document.save(arquivo)
    arquivo.seek(0)

    nome_arquivo = (
        f"Relacao_Fornecedores_Pregao_{pregao.numero}_{pregao.ano}.docx"
        .replace("/", "-")
        .replace("\\", "-")
    )

    response = HttpResponse(
        arquivo.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'

    return response



def pregoes(request):
    if request.method == "POST":
        form = PregaoForm(request.POST)

        if form.is_valid():
            form.save(user=request.user)
            messages.success(request, "Certame cadastrado com sucesso.")
            return redirect("pregoes:pregoes")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = PregaoForm()

    pregoes_queryset = (
        Pregao.objects.all()
        .prefetch_related("municipios", "fornecedores")
        .order_by("-ano", "-numero")
    )
    pregoes_pagina = paginar_queryset(request, pregoes_queryset)

    return render(
        request,
        "pregoes/pregoes.html",
        {
            "form": form,
            "pregoes": pregoes_pagina,
            "modo_edicao": False,
            "pregao_edicao": None,
        },
    )


def editar_pregao(request, pregao_id):
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if pregao.status != Pregao.STATUS_NAO_INICIADO:
        messages.error(
            request,
            "Este certame não pode ser editado porque já está em andamento ou finalizado.",
        )
        return redirect("pregoes:pregoes")

    if request.method == "POST":
        form = PregaoForm(request.POST, instance=pregao)

        if form.is_valid():
            form.save(user=request.user)
            messages.success(request, "Certame atualizado com sucesso.")
            return redirect("pregoes:pregoes")

        messages.error(request, "Verifique os dados informados no formulário.")
    else:
        form = PregaoForm(instance=pregao)

    pregoes_queryset = (
        Pregao.objects.all()
        .prefetch_related("municipios", "fornecedores")
        .order_by("-ano", "-numero")
    )
    pregoes_pagina = paginar_queryset(request, pregoes_queryset)

    return render(
        request,
        "pregoes/pregoes.html",
        {
            "form": form,
            "pregoes": pregoes_pagina,
            "modo_edicao": True,
            "pregao_edicao": pregao,
        },
    )



def editar_ordenador_despesas(request, pregao_id):
    """
    Permite cadastrar/alterar somente Nome, RG e CPF do ordenador de despesas.

    Esta edição permanece disponível para certames em qualquer status porque
    não altera dados da disputa, quantitativos, fornecedores, municípios ou
    resultados do certame.
    """
    pregao = get_object_or_404(Pregao, id=pregao_id)

    if request.method == "POST":
        form = OrdenadorDespesasForm(request.POST, instance=pregao)

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Dados do ordenador de despesas atualizados com sucesso.",
            )
            return redirect("pregoes:pregoes")

        messages.error(
            request,
            "Verifique os dados informados para o ordenador de despesas.",
        )
    else:
        form = OrdenadorDespesasForm(instance=pregao)

    return render(
        request,
        "pregoes/ordenador_despesas.html",
        {
            "form": form,
            "pregao": pregao,
        },
    )


def quantitativo_pregao(request):
    pregoes_lista = Pregao.objects.filter(
        status=Pregao.STATUS_NAO_INICIADO
    ).order_by("-ano", "-numero")

    pregao_id = request.GET.get("pregao") or request.POST.get("pregao")
    pregao = None
    itens = Item.objects.filter(ativo=True).order_by("nome_item")
    quantitativos_salvos = {}

    if pregao_id:
        pregao = get_object_or_404(Pregao, id=pregao_id)

        quantitativos_salvos = {
            q.item_id: q.quantidade
            for q in QuantitativoPregao.objects.filter(pregao=pregao)
        }

    if request.method == "POST":
        if not pregao:
            messages.error(request, "Selecione um pregão.")
            return redirect("pregoes:quantitativo_pregao")

        if pregao.status != Pregao.STATUS_NAO_INICIADO:
            messages.error(
                request,
                "Não é permitido alterar quantitativos de pregão já iniciado ou finalizado.",
            )
            return redirect("pregoes:quantitativo_pregao")

        QuantitativoPregao.objects.filter(pregao=pregao).delete()

        total_salvos = 0

        for item in itens:
            quantidade = request.POST.get(f"quantidade_{item.id}")

            if quantidade:
                quantidade = quantidade.replace(",", ".")

                try:
                    quantidade_float = float(quantidade)
                except ValueError:
                    quantidade_float = 0

                if quantidade_float > 0:
                    QuantitativoPregao.objects.create(
                        pregao=pregao,
                        item=item,
                        quantidade=quantidade,
                    )
                    total_salvos += 1

        messages.success(
            request,
            f"Quantitativos salvos com sucesso. Itens gravados: {total_salvos}.",
        )
        return redirect(f"{request.path}?pregao={pregao.id}")

    return render(
        request,
        "pregoes/quantitativo_pregao.html",
        {
            "pregoes": pregoes_lista,
            "pregao": pregao,
            "itens": itens,
            "quantitativos_salvos": quantitativos_salvos,
        },
    )


def quantitativo_escola(request):
    pregoes_lista = Pregao.objects.filter(
        status=Pregao.STATUS_NAO_INICIADO
    ).order_by("-ano", "-numero")

    pregao_id = request.GET.get("pregao") or request.POST.get("pregao")
    escola_id = request.GET.get("escola") or request.POST.get("escola")

    pregao = None
    escola = None
    escolas = Escola.objects.none()
    itens_pregao = []
    quantitativos_salvos = {}
    resumo_itens = []

    if pregao_id:
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

    if escola_id:
        escola = get_object_or_404(Escola, id=escola_id)

    # Validação de segurança:
    # Só valida quando já existe pregão e escola selecionados.
    if pregao and escola:
        if not pregao.municipios.filter(id=escola.municipio_id).exists():
            messages.error(
                request,
                "A escola selecionada não pertence aos municípios vinculados a este pregão.",
            )
            return redirect("pregoes:quantitativo_escola")

    if pregao:
        itens_pregao = (
            QuantitativoPregao.objects.filter(pregao=pregao)
            .select_related("item")
            .order_by("item__nome_item")
        )

    if pregao and escola:
        quantitativos_salvos = {
            q.item_id: q.quantidade
            for q in QuantitativoEscola.objects.filter(
                pregao=pregao,
                escola=escola,
            )
        }

        for qp in itens_pregao:
            item = qp.item
            quantidade_total_pregao = qp.quantidade

            quantidade_escola_atual = quantitativos_salvos.get(
                item.id,
                Decimal("0"),
            )

            distribuido_todas_escolas = (
                QuantitativoEscola.objects.filter(
                    pregao=pregao,
                    item=item,
                )
                .aggregate(total=Sum("quantidade"))
                .get("total")
            )

            if distribuido_todas_escolas is None:
                distribuido_todas_escolas = Decimal("0")

            saldo_restante_real = quantidade_total_pregao - distribuido_todas_escolas

            resumo_itens.append(
                {
                    "item": item,
                    "quantidade_total_pregao": quantidade_total_pregao,
                    "quantidade_escola_atual": quantidade_escola_atual,
                    "distribuido_todas_escolas": distribuido_todas_escolas,
                    "saldo_restante_real": saldo_restante_real,
                }
            )

    if request.method == "POST":
        if not pregao or not escola:
            messages.error(request, "Selecione o pregão e a escola.")
            return redirect("pregoes:quantitativo_escola")

        if pregao.status != Pregao.STATUS_NAO_INICIADO:
            messages.error(
                request,
                "Não é permitido alterar quantitativos de escola em pregão já iniciado ou finalizado.",
            )
            return redirect("pregoes:quantitativo_escola")

        if not pregao.municipios.filter(id=escola.municipio_id).exists():
            messages.error(
                request,
                "A escola selecionada não pertence aos municípios vinculados a este pregão.",
            )
            return redirect("pregoes:quantitativo_escola")

        quantidades_digitadas = {}
        erros_validacao = []

        for qp in itens_pregao:
            item = qp.item
            quantidade_texto = request.POST.get(f"quantidade_{item.id}", "").strip()

            if not quantidade_texto:
                continue

            quantidade_texto = quantidade_texto.replace(",", ".")

            try:
                quantidade_nova = Decimal(quantidade_texto)
            except InvalidOperation:
                erros_validacao.append(
                    f"A quantidade informada para o item '{item.nome_item}' é inválida."
                )
                continue

            if quantidade_nova <= 0:
                continue

            quantidade_total_pregao = qp.quantidade

            soma_outras_escolas = (
                QuantitativoEscola.objects.filter(
                    pregao=pregao,
                    item=item,
                )
                .exclude(escola=escola)
                .aggregate(total=Sum("quantidade"))
                .get("total")
            )

            if soma_outras_escolas is None:
                soma_outras_escolas = Decimal("0")

            soma_final = soma_outras_escolas + quantidade_nova

            if soma_final > quantidade_total_pregao:
                saldo_permitido_para_esta_escola = quantidade_total_pregao - soma_outras_escolas

                erros_validacao.append(
                    f"O item '{item.nome_item}' ultrapassou o total do pregão. "
                    f"Total do pregão: {quantidade_total_pregao}. "
                    f"Quantidade máxima permitida para esta escola: {saldo_permitido_para_esta_escola}."
                )
            else:
                quantidades_digitadas[item.id] = quantidade_nova

        if erros_validacao:
            for erro in erros_validacao:
                messages.error(request, erro)

            resumo_itens = []

            for qp in itens_pregao:
                item = qp.item
                quantidade_total_pregao = qp.quantidade

                quantidade_escola_atual = quantidades_digitadas.get(
                    item.id,
                    Decimal("0"),
                )

                distribuido_todas_escolas = (
                    QuantitativoEscola.objects.filter(
                        pregao=pregao,
                        item=item,
                    )
                    .exclude(escola=escola)
                    .aggregate(total=Sum("quantidade"))
                    .get("total")
                )

                if distribuido_todas_escolas is None:
                    distribuido_todas_escolas = Decimal("0")

                distribuido_todas_escolas = distribuido_todas_escolas + quantidade_escola_atual
                saldo_restante_real = quantidade_total_pregao - distribuido_todas_escolas

                resumo_itens.append(
                    {
                        "item": item,
                        "quantidade_total_pregao": quantidade_total_pregao,
                        "quantidade_escola_atual": quantidade_escola_atual,
                        "distribuido_todas_escolas": distribuido_todas_escolas,
                        "saldo_restante_real": saldo_restante_real,
                    }
                )

            return render(
                request,
                "pregoes/quantitativo_escola.html",
                {
                    "pregoes": pregoes_lista,
                    "escolas": escolas,
                    "pregao": pregao,
                    "escola": escola,
                    "itens_pregao": itens_pregao,
                    "quantitativos_salvos": quantidades_digitadas,
                    "resumo_itens": resumo_itens,
                },
            )

        QuantitativoEscola.objects.filter(
            pregao=pregao,
            escola=escola,
        ).delete()

        total_salvos = 0

        for item_id, quantidade in quantidades_digitadas.items():
            QuantitativoEscola.objects.create(
                pregao=pregao,
                escola=escola,
                item_id=item_id,
                quantidade=quantidade,
            )
            total_salvos += 1

        messages.success(
            request,
            f"Quantitativos da escola salvos com sucesso. Itens gravados: {total_salvos}.",
        )

        return redirect(f"{request.path}?pregao={pregao.id}&escola={escola.id}")

    return render(
        request,
        "pregoes/quantitativo_escola.html",
        {
            "pregoes": pregoes_lista,
            "escolas": escolas,
            "pregao": pregao,
            "escola": escola,
            "itens_pregao": itens_pregao,
            "quantitativos_salvos": quantitativos_salvos,
            "resumo_itens": resumo_itens,
        },
    )

def usuario_eh_consulta_escola(request):
    perfil = getattr(request.user, "perfil_acesso", None)

    return bool(
        request.user.is_authenticated
        and perfil
        and perfil.perfil == "consulta_escola"
    )


def obter_escola_consulta_usuario(request):
    perfil = getattr(request.user, "perfil_acesso", None)

    if not perfil:
        return None

    return perfil.escola


def relatorio_distribuicao(request):
    consulta_escola = usuario_eh_consulta_escola(request)
    escola_consulta = obter_escola_consulta_usuario(request) if consulta_escola else None

    if consulta_escola:
        if escola_consulta:
            pregoes_ids = (
                QuantitativoEscola.objects.filter(
                    escola=escola_consulta,
                    quantidade__gt=0,
                )
                .values_list("pregao_id", flat=True)
                .distinct()
            )

            pregoes_lista = Pregao.objects.filter(
                id__in=pregoes_ids,
            ).order_by("-ano", "-numero")
        else:
            pregoes_lista = Pregao.objects.none()

            messages.warning(
                request,
                "Seu usuário está com o perfil Consulta/Escola, mas não possui escola vinculada. "
                "Solicite ao administrador que vincule seu usuário a uma escola.",
            )
    else:
        pregoes_lista = Pregao.objects.all().order_by("-ano", "-numero")

    pregao_id = request.GET.get("pregao")
    pregao = None
    relatorio = []

    total_itens = 0
    itens_fechados = 0
    itens_com_saldo = 0
    itens_excedidos = 0

    if pregao_id:
        # Segurança: usuário Consulta/Escola só pode abrir relatório de pregões
        # que possuam quantitativo vinculado à sua própria escola.
        if consulta_escola:
            if not escola_consulta:
                messages.error(
                    request,
                    "Seu usuário não possui escola vinculada para consultar relatórios.",
                )
                return redirect("pregoes:relatorio_distribuicao")

            pregao_permitido = QuantitativoEscola.objects.filter(
                pregao_id=pregao_id,
                escola=escola_consulta,
                quantidade__gt=0,
            ).exists()

            if not pregao_permitido:
                messages.error(
                    request,
                    "Você não tem permissão para consultar relatórios deste pregão.",
                )
                return redirect("pregoes:relatorio_distribuicao")

        pregao = get_object_or_404(Pregao, id=pregao_id)

        itens_pregao = (
            QuantitativoPregao.objects.filter(pregao=pregao)
            .select_related("item")
            .order_by("item__nome_item")
        )

        for qp in itens_pregao:
            item = qp.item
            total_pregao = qp.quantidade

            distribuicoes_todas = (
                QuantitativoEscola.objects.filter(
                    pregao=pregao,
                    item=item,
                )
                .select_related("escola", "escola__municipio")
                .order_by("escola__nome_escola")
            )

            total_distribuido = distribuicoes_todas.aggregate(
                total=Sum("quantidade")
            ).get("total")

            if total_distribuido is None:
                total_distribuido = Decimal("0")

            # Para Consulta/Escola, exibe somente a linha da escola vinculada.
            # O total/status do item continua considerando a distribuição geral do pregão,
            # mas a lista detalhada não mostra outras escolas.
            if consulta_escola and escola_consulta:
                distribuicoes = distribuicoes_todas.filter(escola=escola_consulta)

                # Se a escola não possui este item, não mostra o item no relatório dela.
                if not distribuicoes.exists():
                    continue
            else:
                distribuicoes = distribuicoes_todas

            saldo = total_pregao - total_distribuido

            total_itens += 1

            if saldo == 0:
                status = "Fechado"
                status_classe = "badge-success"
                status_linha = "status-fechado"
                itens_fechados += 1
            elif saldo > 0:
                status = "Com saldo"
                status_classe = "badge-warning"
                status_linha = "status-saldo"
                itens_com_saldo += 1
            else:
                status = "Excedido"
                status_classe = "badge-danger"
                status_linha = "status-excedido"
                itens_excedidos += 1

            relatorio.append(
                {
                    "item": item,
                    "total_pregao": total_pregao,
                    "distribuicoes": distribuicoes,
                    "total_distribuido": total_distribuido,
                    "saldo": saldo,
                    "status": status,
                    "status_classe": status_classe,
                    "status_linha": status_linha,
                }
            )

    return render(
        request,
        "pregoes/relatorio_distribuicao.html",
        {
            "pregoes": pregoes_lista,
            "pregao": pregao,
            "relatorio": relatorio,
            "total_itens": total_itens,
            "itens_fechados": itens_fechados,
            "itens_com_saldo": itens_com_saldo,
            "itens_excedidos": itens_excedidos,
            "consulta_escola": consulta_escola,
            "escola_consulta": escola_consulta,
        },
    )

def escolas_por_pregao(request, pregao_id):
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

def itens_por_pregao(request, pregao_id):
    pregao = get_object_or_404(
        Pregao,
        id=pregao_id,
        tipo_certame=Pregao.TIPO_PREGAO_PRESENCIAL,
    )

    # Se ainda não houver PregaoItem, tenta criar a partir dos quantitativos,
    # mas somente se já existir quantitativo por escola.
    if not PregaoItem.objects.filter(pregao=pregao).exists():
        garantir_itens_pregao_para_propostas(pregao)

    itens = (
        PregaoItem.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("ordem", "item__nome_item")
    )

    dados = []

    for item_pregao in itens:
        dados.append(
            {
                "id": item_pregao.id,
                "ordem": item_pregao.ordem,
                "nome": item_pregao.item.nome_item,
            }
        )

    return JsonResponse({"itens": dados})

def garantir_itens_pregao_para_propostas(pregao):
    """
    Para pregões ainda não iniciados, pode não existir PregaoItem.
    Esta função cria os PregaoItem a partir do QuantitativoPregao,
    mas somente se já existir pelo menos um QuantitativoEscola para o pregão.
    """

    existe_quantitativo_escola = QuantitativoEscola.objects.filter(
        pregao=pregao
    ).exists()

    if not existe_quantitativo_escola:
        return False

    quantitativos_pregao = (
        QuantitativoPregao.objects.filter(pregao=pregao)
        .select_related("item")
        .order_by("item__nome_item")
    )

    ordem = 1

    for qp in quantitativos_pregao:
        PregaoItem.objects.get_or_create(
            pregao=pregao,
            item=qp.item,
            defaults={
                "ordem": ordem,
                "quantidade_total": qp.quantidade,
            },
        )

        ordem += 1

    return True


def media_precos(request):
    pregoes_lista = (
        Pregao.objects.filter(
            status__in=[
                Pregao.STATUS_NAO_INICIADO,
                Pregao.STATUS_EM_ANDAMENTO,
            ]
        )
        .order_by("-ano", "-numero")
    )

    pregao_id = request.GET.get("pregao") or request.POST.get("pregao")
    pregao = None
    itens_pregao = []

    if pregao_id:
        pregao = get_object_or_404(Pregao, id=pregao_id)

        if pregao.status not in [
            Pregao.STATUS_NAO_INICIADO,
            Pregao.STATUS_EM_ANDAMENTO,
        ]:
            messages.error(
                request,
                "Só é possível cadastrar médias de preços em certames não iniciados ou em andamento.",
            )
            return redirect("pregoes:media_precos")

        if not PregaoItem.objects.filter(pregao=pregao).exists():
            garantir_itens_pregao_para_propostas(pregao)

        itens_pregao = (
            PregaoItem.objects.filter(pregao=pregao)
            .select_related("item")
            .order_by("ordem", "item__nome_item")
        )

    if request.method == "POST":
        if not pregao:
            messages.error(request, "Selecione um certame.")
            return redirect("pregoes:media_precos")

        if pregao.status not in [
            Pregao.STATUS_NAO_INICIADO,
            Pregao.STATUS_EM_ANDAMENTO,
        ]:
            messages.error(
                request,
                "Só é possível alterar médias de preços em certames não iniciados ou em andamento.",
            )
            return redirect("pregoes:media_precos")

        erros = []
        atualizados = 0

        # Percentual único do certame
        percentual_texto = request.POST.get("percentual_alerta_media", "").strip()

        if percentual_texto:
            percentual_texto = percentual_texto.replace(",", ".")

            try:
                percentual_alerta = Decimal(percentual_texto)
            except InvalidOperation:
                erros.append("O percentual de alerta do certame é inválido.")
                percentual_alerta = None

            if percentual_alerta is not None:
                if percentual_alerta < 0:
                    erros.append("O percentual de alerta do certame não pode ser negativo.")
                elif percentual_alerta > 100:
                    erros.append("O percentual de alerta do certame não pode ser maior que 100%.")
        else:
            percentual_alerta = Decimal("50")

        # Médias continuam sendo individuais por item
        for item_pregao in itens_pregao:
            media_texto = request.POST.get(f"media_preco_{item_pregao.id}", "").strip()
            media_preco = None

            if media_texto:
                media_texto = media_texto.replace(".", "").replace(",", ".")

                try:
                    media_preco = Decimal(media_texto)
                except InvalidOperation:
                    erros.append(
                        f"A média de preço do item '{item_pregao.item.nome_item}' é inválida."
                    )
                    continue

                if media_preco <= 0:
                    erros.append(
                        f"A média de preço do item '{item_pregao.item.nome_item}' deve ser maior que zero."
                    )
                    continue

            item_pregao.media_preco = media_preco
            item_pregao.save(update_fields=["media_preco"])
            atualizados += 1

        if erros:
            for erro in erros:
                messages.error(request, erro)

            return redirect(f"{request.path}?pregao={pregao.id}")

        pregao.percentual_alerta_media = percentual_alerta
        pregao.save(update_fields=["percentual_alerta_media", "atualizado_em"])

        messages.success(
            request,
            f"Médias de preços salvas com sucesso. Itens atualizados: {atualizados}. "
            f"Percentual de alerta do certame: {percentual_alerta:.2f}%.",
        )

        return redirect(f"{request.path}?pregao={pregao.id}")

    return render(
        request,
        "pregoes/media_precos.html",
        {
            "pregoes": pregoes_lista,
            "pregao": pregao,
            "itens_pregao": itens_pregao,
        },
    )


def propostas_iniciais(request):
    pregoes_lista = (
        Pregao.objects.filter(
            tipo_certame=Pregao.TIPO_PREGAO_PRESENCIAL,
            status__in=[
                Pregao.STATUS_NAO_INICIADO,
                Pregao.STATUS_EM_ANDAMENTO,
            ],
        )
        .order_by("-ano", "-numero")
    )

    modo = request.GET.get("modo") or request.POST.get("modo") or "item"
    if modo not in ["item", "fornecedor"]:
        modo = "item"

    pregao_id = request.GET.get("pregao") or request.POST.get("pregao")
    item_id = request.GET.get("item") or request.POST.get("item")
    fornecedor_id = request.GET.get("fornecedor") or request.POST.get("fornecedor")

    pregao = None
    item_pregao = None
    fornecedor_selecionado = None
    itens_pregao = []
    fornecedores = []
    propostas_salvas = {}

    if pregao_id:
        pregao = get_object_or_404(
            Pregao,
            id=pregao_id,
            tipo_certame=Pregao.TIPO_PREGAO_PRESENCIAL,
        )

        if pregao.status not in [
            Pregao.STATUS_NAO_INICIADO,
            Pregao.STATUS_EM_ANDAMENTO,
        ]:
            messages.error(
                request,
                "Só é possível cadastrar propostas iniciais em pregões não iniciados ou em andamento.",
            )
            return redirect("pregoes:propostas_iniciais")

        if not PregaoItem.objects.filter(pregao=pregao).exists():
            garantir_itens_pregao_para_propostas(pregao)

        itens_pregao = list(
            PregaoItem.objects.filter(pregao=pregao)
            .select_related("item")
            .order_by("ordem", "item__nome_item")
        )

        fornecedores = [
            vinculo.fornecedor
            for vinculo in (
                PregaoFornecedor.objects.filter(
                    pregao=pregao,
                    ativo_no_pregao=True,
                )
                .select_related("fornecedor")
                .order_by("ordem_inicial", "fornecedor__razao_social")
            )
        ]

    # ============================================================
    # MODO 1: CADASTRO POR ITEM
    # ============================================================
    if modo == "item" and pregao and item_id:
        item_pregao = get_object_or_404(
            PregaoItem.objects.select_related("item"),
            id=item_id,
            pregao=pregao,
        )

        propostas_salvas = {
            proposta.fornecedor_id: proposta
            for proposta in PropostaInicialItem.objects.filter(
                pregao=pregao,
                pregao_item=item_pregao,
            )
        }

    # ============================================================
    # MODO 2: CADASTRO POR FORNECEDOR
    # ============================================================
    if modo == "fornecedor" and pregao and fornecedor_id:
        fornecedor_selecionado = next(
            (
                fornecedor
                for fornecedor in fornecedores
                if str(fornecedor.id) == str(fornecedor_id)
            ),
            None,
        )

        if not fornecedor_selecionado:
            messages.error(
                request,
                "O fornecedor selecionado não está ativo neste pregão.",
            )
            return redirect(
                f"{request.path}?modo=fornecedor&pregao={pregao.id}"
            )

        propostas_salvas = {
            proposta.pregao_item_id: proposta
            for proposta in PropostaInicialItem.objects.filter(
                pregao=pregao,
                fornecedor=fornecedor_selecionado,
            )
        }

    if request.method == "POST":
        if not pregao:
            messages.error(request, "Selecione o pregão.")
            return redirect("pregoes:propostas_iniciais")

        if pregao.status not in [
            Pregao.STATUS_NAO_INICIADO,
            Pregao.STATUS_EM_ANDAMENTO,
        ]:
            messages.error(
                request,
                "Só é possível alterar propostas iniciais em pregões não iniciados ou em andamento.",
            )
            return redirect("pregoes:propostas_iniciais")

        # --------------------------------------------------------
        # SALVAR POR ITEM
        # --------------------------------------------------------
        if modo == "item":
            if not item_pregao:
                messages.error(request, "Selecione o pregão e o item.")
                return redirect(
                    f"{request.path}?modo=item&pregao={pregao.id}"
                )

            erros = []
            salvos = 0

            for fornecedor in fornecedores:
                participa = request.POST.get(
                    f"participa_{fornecedor.id}"
                ) == "on"
                marca = request.POST.get(
                    f"marca_{fornecedor.id}",
                    "",
                ).strip()
                preco_texto = request.POST.get(
                    f"preco_inicial_{fornecedor.id}",
                    "",
                ).strip()

                if not participa:
                    PropostaInicialItem.objects.update_or_create(
                        pregao=pregao,
                        pregao_item=item_pregao,
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
                    erros.append(
                        f"Informe a marca para o fornecedor "
                        f"{fornecedor.razao_social}, ou desmarque a opção Participa."
                    )
                    continue

                if not preco_texto:
                    erros.append(
                        f"Informe o preço inicial para o fornecedor "
                        f"{fornecedor.razao_social}, ou desmarque a opção Participa."
                    )
                    continue

                preco_texto = preco_texto.replace(",", ".")

                try:
                    preco_inicial = Decimal(preco_texto)
                except InvalidOperation:
                    erros.append(
                        f"Preço inicial inválido para o fornecedor "
                        f"{fornecedor.razao_social}."
                    )
                    continue

                if preco_inicial <= 0:
                    erros.append(
                        f"O preço inicial do fornecedor "
                        f"{fornecedor.razao_social} deve ser maior que zero."
                    )
                    continue

                PropostaInicialItem.objects.update_or_create(
                    pregao=pregao,
                    pregao_item=item_pregao,
                    fornecedor=fornecedor,
                    defaults={
                        "participa": True,
                        "marca": marca,
                        "preco_inicial": preco_inicial,
                    },
                )
                salvos += 1

            if erros:
                for erro in erros:
                    messages.error(request, erro)

                propostas_salvas = {
                    proposta.fornecedor_id: proposta
                    for proposta in PropostaInicialItem.objects.filter(
                        pregao=pregao,
                        pregao_item=item_pregao,
                    )
                }

                return render(
                    request,
                    "pregoes/propostas_iniciais.html",
                    {
                        "pregoes": pregoes_lista,
                        "modo": modo,
                        "pregao": pregao,
                        "itens_pregao": itens_pregao,
                        "item_pregao": item_pregao,
                        "fornecedores": fornecedores,
                        "fornecedor_selecionado": None,
                        "propostas_salvas": propostas_salvas,
                    },
                )

            messages.success(
                request,
                f"Propostas iniciais salvas com sucesso. Registros salvos: {salvos}.",
            )

            acao = request.POST.get("acao")

            if acao == "salvar_avancar":
                proximo_item = (
                    PregaoItem.objects.filter(
                        pregao=pregao,
                        ordem__gt=item_pregao.ordem,
                    )
                    .select_related("item")
                    .order_by("ordem", "item__nome_item")
                    .first()
                )

                if proximo_item:
                    return redirect(
                        f"{request.path}?modo=item&pregao={pregao.id}"
                        f"&item={proximo_item.id}"
                    )

                messages.success(
                    request,
                    "Este era o último item do pregão. Não há próximo item para avançar.",
                )

            return redirect(
                f"{request.path}?modo=item&pregao={pregao.id}"
                f"&item={item_pregao.id}"
            )

        # --------------------------------------------------------
        # SALVAR POR FORNECEDOR
        # --------------------------------------------------------
        if modo == "fornecedor":
            if not fornecedor_selecionado:
                messages.error(
                    request,
                    "Selecione o pregão e o fornecedor.",
                )
                return redirect(
                    f"{request.path}?modo=fornecedor&pregao={pregao.id}"
                )

            erros = []
            dados_validados = []

            # Primeiro valida tudo. Só grava depois que todas as linhas
            # selecionadas estiverem corretas.
            for pi in itens_pregao:
                participa = request.POST.get(
                    f"participa_item_{pi.id}"
                ) == "on"
                marca = request.POST.get(
                    f"marca_item_{pi.id}",
                    "",
                ).strip()
                preco_texto = request.POST.get(
                    f"preco_item_{pi.id}",
                    "",
                ).strip()

                if not participa:
                    dados_validados.append(
                        {
                            "pregao_item": pi,
                            "participa": False,
                            "marca": "",
                            "preco_inicial": None,
                        }
                    )
                    continue

                if not marca:
                    erros.append(
                        f"Informe a marca do item {pi.ordem} - "
                        f"{pi.item.nome_item}, ou desmarque a participação."
                    )
                    continue

                if not preco_texto:
                    erros.append(
                        f"Informe o preço inicial do item {pi.ordem} - "
                        f"{pi.item.nome_item}, ou desmarque a participação."
                    )
                    continue

                preco_normalizado = preco_texto.replace(",", ".")

                try:
                    preco_inicial = Decimal(preco_normalizado)
                except InvalidOperation:
                    erros.append(
                        f"Preço inicial inválido para o item {pi.ordem} - "
                        f"{pi.item.nome_item}."
                    )
                    continue

                if preco_inicial <= 0:
                    erros.append(
                        f"O preço inicial do item {pi.ordem} - "
                        f"{pi.item.nome_item} deve ser maior que zero."
                    )
                    continue

                dados_validados.append(
                    {
                        "pregao_item": pi,
                        "participa": True,
                        "marca": marca,
                        "preco_inicial": preco_inicial,
                    }
                )

            if erros:
                for erro in erros:
                    messages.error(request, erro)

                # Mantém na tela exatamente o que o usuário acabou de digitar.
                propostas_digitadas = {}
                for pi in itens_pregao:
                    participa = request.POST.get(
                        f"participa_item_{pi.id}"
                    ) == "on"

                    propostas_digitadas[pi.id] = {
                        "participa": participa,
                        "marca": request.POST.get(
                            f"marca_item_{pi.id}",
                            "",
                        ).strip(),
                        "preco_inicial": request.POST.get(
                            f"preco_item_{pi.id}",
                            "",
                        ).strip(),
                    }

                return render(
                    request,
                    "pregoes/propostas_iniciais.html",
                    {
                        "pregoes": pregoes_lista,
                        "modo": modo,
                        "pregao": pregao,
                        "itens_pregao": itens_pregao,
                        "item_pregao": None,
                        "fornecedores": fornecedores,
                        "fornecedor_selecionado": fornecedor_selecionado,
                        "propostas_salvas": propostas_digitadas,
                    },
                )

            salvos = 0
            participantes = 0

            for dados in dados_validados:
                PropostaInicialItem.objects.update_or_create(
                    pregao=pregao,
                    pregao_item=dados["pregao_item"],
                    fornecedor=fornecedor_selecionado,
                    defaults={
                        "participa": dados["participa"],
                        "marca": dados["marca"],
                        "preco_inicial": dados["preco_inicial"],
                    },
                )
                salvos += 1
                if dados["participa"]:
                    participantes += 1

            messages.success(
                request,
                f"Propostas do fornecedor {fornecedor_selecionado.razao_social} "
                f"salvas com sucesso. Itens participantes: {participantes}. "
                f"Registros processados: {salvos}.",
            )

            return redirect(
                f"{request.path}?modo=fornecedor&pregao={pregao.id}"
                f"&fornecedor={fornecedor_selecionado.id}"
            )

    return render(
        request,
        "pregoes/propostas_iniciais.html",
        {
            "pregoes": pregoes_lista,
            "modo": modo,
            "pregao": pregao,
            "itens_pregao": itens_pregao,
            "item_pregao": item_pregao,
            "fornecedores": fornecedores,
            "fornecedor_selecionado": fornecedor_selecionado,
            "propostas_salvas": propostas_salvas,
        },
    )

