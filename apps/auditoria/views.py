from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.shortcuts import render

from .models import RegistroAuditoria


def usuario_pode_ver_auditoria(user):
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    perfil = getattr(user, "perfil_acesso", None)

    return bool(
        perfil
        and perfil.perfil == "administrador"
        and user.is_active
    )


@login_required
@user_passes_test(usuario_pode_ver_auditoria, login_url="dashboard")
def historico(request):
    registros = RegistroAuditoria.objects.select_related("usuario").all()

    usuario = request.GET.get("usuario", "").strip()
    acao = request.GET.get("acao", "").strip()
    modulo = request.GET.get("modulo", "").strip()
    busca = request.GET.get("busca", "").strip()
    data_inicial = request.GET.get("data_inicial", "").strip()
    data_final = request.GET.get("data_final", "").strip()

    if usuario:
        registros = registros.filter(usuario_username__icontains=usuario)

    if acao:
        registros = registros.filter(acao=acao)

    if modulo:
        registros = registros.filter(modulo__icontains=modulo)

    if busca:
        registros = registros.filter(descricao__icontains=busca)

    if data_inicial:
        registros = registros.filter(criado_em__date__gte=data_inicial)

    if data_final:
        registros = registros.filter(criado_em__date__lte=data_final)

    paginator = Paginator(registros, 30)
    pagina = request.GET.get("page")
    page_obj = paginator.get_page(pagina)

    modulos = (
        RegistroAuditoria.objects.exclude(modulo="")
        .values_list("modulo", flat=True)
        .distinct()
        .order_by("modulo")
    )

    return render(
        request,
        "auditoria/historico.html",
        {
            "page_obj": page_obj,
            "acoes": RegistroAuditoria.ACAO_CHOICES,
            "modulos": modulos,
            "filtros": {
                "usuario": usuario,
                "acao": acao,
                "modulo": modulo,
                "busca": busca,
                "data_inicial": data_inicial,
                "data_final": data_final,
            },
        },
    )
