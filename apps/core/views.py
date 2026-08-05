from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from apps.cadastros.models import Escola, Fornecedor, Item
from apps.pregoes.models import Pregao, QuantitativoEscola
from apps.documentos.models import ContratoGerado, RealinhamentoPreco


def usuario_eh_consulta_escola_dashboard(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False

    if request.user.is_superuser:
        return False

    perfil = getattr(request.user, "perfil_acesso", None)

    return bool(perfil and perfil.perfil == "consulta_escola")


def escola_vinculada_dashboard(request):
    perfil = getattr(request.user, "perfil_acesso", None)

    if not perfil:
        return None

    return perfil.escola


@login_required
def dashboard(request):
    usuario_consulta_escola = usuario_eh_consulta_escola_dashboard(request)
    escola_vinculada = escola_vinculada_dashboard(request) if usuario_consulta_escola else None

    alertas = []

    if usuario_consulta_escola:
        if not escola_vinculada:
            total_fornecedores = 0
            total_escolas = 0
            total_itens = 0

            pregoes_base = Pregao.objects.none()
            contratos_base = ContratoGerado.objects.none()
            realinhamentos_base = RealinhamentoPreco.objects.none()

            pregoes_nao_iniciados = 0
            pregoes_em_andamento = 0
            pregoes_finalizados = 0
            total_pregoes = 0

            total_contratos = 0
            contratos_ativos = 0
            contratos_parcialmente_distratados = 0
            contratos_distratados = 0

            realinhamentos_registrados = 0
            realinhamentos_pendentes_termo = 0

            pregoes_recentes = []

            alertas.append(
                {
                    "tipo": "warning",
                    "titulo": "Escola não vinculada",
                    "descricao": "Seu usuário Consulta/Escola não possui escola vinculada. Solicite o vínculo ao administrador.",
                }
            )

        else:
            pregoes_base = (
                Pregao.objects.filter(
                    Q(quantitativos_escola__escola=escola_vinculada)
                    | Q(contratos_gerados__escola=escola_vinculada)
                )
                .distinct()
            )

            contratos_base = ContratoGerado.objects.filter(
                escola=escola_vinculada
            )

            realinhamentos_base = RealinhamentoPreco.objects.filter(
                contrato__escola=escola_vinculada
            )

            total_fornecedores = (
                Fornecedor.objects.filter(
                    contratos_gerados__escola=escola_vinculada
                )
                .distinct()
                .count()
            )

            total_escolas = 1

            total_itens = (
                Item.objects.filter(
                    quantitativos_escola__escola=escola_vinculada,
                    quantitativos_escola__quantidade__gt=0,
                )
                .distinct()
                .count()
            )

            pregoes_nao_iniciados = pregoes_base.filter(
                status=Pregao.STATUS_NAO_INICIADO
            ).count()

            pregoes_em_andamento = pregoes_base.filter(
                status=Pregao.STATUS_EM_ANDAMENTO
            ).count()

            pregoes_finalizados = pregoes_base.filter(
                status=Pregao.STATUS_FINALIZADO
            ).count()

            total_pregoes = pregoes_base.count()

            total_contratos = contratos_base.count()

            contratos_ativos = contratos_base.filter(
                status__in=[
                    ContratoGerado.STATUS_GERADO,
                    ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
                ]
            ).count()

            contratos_parcialmente_distratados = contratos_base.filter(
                status=ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO
            ).count()

            contratos_distratados = contratos_base.filter(
                status__in=[
                    ContratoGerado.STATUS_DISTRATADO,
                    ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
                ]
            ).count()

            realinhamentos_registrados = realinhamentos_base.filter(
                status=RealinhamentoPreco.STATUS_REGISTRADO
            ).count()

            realinhamentos_pendentes_termo = (
                realinhamentos_base.filter(
                    status=RealinhamentoPreco.STATUS_REGISTRADO
                )
                .filter(
                    Q(numero_termo_aditivo="") | Q(data_termo_aditivo__isnull=True)
                )
                .count()
            )

            pregoes_recentes = (
                pregoes_base
                .prefetch_related("municipios", "fornecedores")
                .order_by("-ano", "-numero")[:5]
            )

            if pregoes_em_andamento:
                alertas.append(
                    {
                        "tipo": "info",
                        "titulo": "Certames em andamento da sua escola",
                        "descricao": f"{pregoes_em_andamento} certame(s) vinculado(s) à sua escola ainda estão em andamento.",
                    }
                )

            if contratos_parcialmente_distratados:
                alertas.append(
                    {
                        "tipo": "warning",
                        "titulo": "Contratos parcialmente distratados da sua escola",
                        "descricao": f"{contratos_parcialmente_distratados} contrato(s) da sua escola possuem distrato parcial registrado.",
                    }
                )

            if realinhamentos_pendentes_termo:
                alertas.append(
                    {
                        "tipo": "warning",
                        "titulo": "Termos aditivos pendentes da sua escola",
                        "descricao": f"{realinhamentos_pendentes_termo} realinhamento(s) da sua escola ainda não têm termo aditivo registrado.",
                    }
                )

    else:
        total_fornecedores = Fornecedor.objects.count()
        total_escolas = Escola.objects.count()
        total_itens = Item.objects.count()

        pregoes_nao_iniciados = Pregao.objects.filter(
            status=Pregao.STATUS_NAO_INICIADO
        ).count()

        pregoes_em_andamento = Pregao.objects.filter(
            status=Pregao.STATUS_EM_ANDAMENTO
        ).count()

        pregoes_finalizados = Pregao.objects.filter(
            status=Pregao.STATUS_FINALIZADO
        ).count()

        total_pregoes = Pregao.objects.count()

        total_contratos = ContratoGerado.objects.count()

        contratos_ativos = ContratoGerado.objects.filter(
            status__in=[
                ContratoGerado.STATUS_GERADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ]
        ).count()

        contratos_parcialmente_distratados = ContratoGerado.objects.filter(
            status=ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO
        ).count()

        contratos_distratados = ContratoGerado.objects.filter(
            status__in=[
                ContratoGerado.STATUS_DISTRATADO,
                ContratoGerado.STATUS_PARCIALMENTE_DISTRATADO,
            ]
        ).count()

        realinhamentos_registrados = RealinhamentoPreco.objects.filter(
            status=RealinhamentoPreco.STATUS_REGISTRADO
        ).count()

        realinhamentos_pendentes_termo = RealinhamentoPreco.objects.filter(
            status=RealinhamentoPreco.STATUS_REGISTRADO
        ).filter(
            Q(numero_termo_aditivo="") | Q(data_termo_aditivo__isnull=True)
        ).count()

        pregoes_recentes = (
            Pregao.objects.all()
            .prefetch_related("municipios", "fornecedores")
            .order_by("-ano", "-numero")[:5]
        )

        if pregoes_em_andamento:
            alertas.append(
                {
                    "tipo": "info",
                    "titulo": "Pregões em andamento",
                    "descricao": f"{pregoes_em_andamento} pregão(ões) ainda estão em fase de execução.",
                }
            )

        if contratos_parcialmente_distratados:
            alertas.append(
                {
                    "tipo": "warning",
                    "titulo": "Contratos parcialmente distratados",
                    "descricao": f"{contratos_parcialmente_distratados} contrato(s) possuem distrato parcial registrado.",
                }
            )

        if realinhamentos_pendentes_termo:
            alertas.append(
                {
                    "tipo": "warning",
                    "titulo": "Termos aditivos pendentes",
                    "descricao": f"{realinhamentos_pendentes_termo} realinhamento(s) ainda não têm termo aditivo registrado.",
                }
            )

    if not alertas:
        if usuario_consulta_escola and escola_vinculada:
            descricao_alerta = "Nenhum alerta operacional importante para sua escola no momento."
        else:
            descricao_alerta = "Nenhum alerta operacional importante no momento."

        alertas.append(
            {
                "tipo": "success",
                "titulo": "Tudo em ordem",
                "descricao": descricao_alerta,
            }
        )

    return render(
        request,
        "core/dashboard.html",
        {
            "usuario_consulta_escola": usuario_consulta_escola,
            "escola_vinculada": escola_vinculada,
            "total_fornecedores": total_fornecedores,
            "total_escolas": total_escolas,
            "total_itens": total_itens,
            "total_pregoes": total_pregoes,
            "pregoes_nao_iniciados": pregoes_nao_iniciados,
            "pregoes_em_andamento": pregoes_em_andamento,
            "pregoes_finalizados": pregoes_finalizados,
            "pregoes_recentes": pregoes_recentes,
            "total_contratos": total_contratos,
            "contratos_ativos": contratos_ativos,
            "contratos_distratados": contratos_distratados,
            "contratos_parcialmente_distratados": contratos_parcialmente_distratados,
            "realinhamentos_registrados": realinhamentos_registrados,
            "realinhamentos_pendentes_termo": realinhamentos_pendentes_termo,
            "alertas": alertas,
        },
    )
