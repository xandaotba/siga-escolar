from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .models import RegistroAuditoria
from .utils import registrar_auditoria


@receiver(user_logged_in)
def registrar_login(sender, request, user, **kwargs):
    registrar_auditoria(
        request=request,
        usuario=user,
        acao=RegistroAuditoria.ACAO_LOGIN,
        modulo="usuarios",
        descricao="Usuário realizou login no sistema.",
    )


@receiver(user_logged_out)
def registrar_logout(sender, request, user, **kwargs):
    registrar_auditoria(
        request=request,
        usuario=user,
        acao=RegistroAuditoria.ACAO_LOGOUT,
        modulo="usuarios",
        descricao="Usuário saiu do sistema.",
    )
