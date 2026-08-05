from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


PERFIL_ADMINISTRADOR = "administrador"
PERFIL_PREGOEIRO = "pregoeiro"
PERFIL_CONSULTA_ESCOLA = "consulta_escola"


def perfil_usuario(user):
    if not user.is_authenticated:
        return None

    if user.is_superuser:
        return PERFIL_ADMINISTRADOR

    perfil = getattr(user, "perfil_acesso", None)

    if not perfil:
        return None

    return perfil.perfil


def is_administrador(user):
    return user.is_authenticated and (
        user.is_superuser or perfil_usuario(user) == PERFIL_ADMINISTRADOR
    )


def is_pregoeiro(user):
    return user.is_authenticated and perfil_usuario(user) == PERFIL_PREGOEIRO


def is_consulta_escola(user):
    return user.is_authenticated and perfil_usuario(user) == PERFIL_CONSULTA_ESCOLA


def requer_perfil(*perfis_permitidos):
    """
    Decorator opcional para views específicas.
    O middleware já faz o bloqueio geral por URL, mas este decorator
    pode ser usado em novas views quando for necessário um controle pontual.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            perfil = perfil_usuario(request.user)

            if perfil in perfis_permitidos:
                return view_func(request, *args, **kwargs)

            messages.error(
                request,
                "Você não tem permissão para executar esta ação.",
            )
            return redirect("dashboard")

        return wrapper

    return decorator
