from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


def obter_perfil_usuario(user):
    if not user.is_authenticated:
        return None

    if user.is_superuser:
        return "administrador"

    perfil = getattr(user, "perfil_acesso", None)

    if not perfil:
        return None

    return perfil.perfil


def usuario_pode_acessar_caminho(user, request):
    """
    Controle central de permissões por perfil.

    Administrador:
    - acesso total.

    Pregoeiro:
    - dashboard;
    - pregões;
    - execução;
    - documentos de apoio do pregão e contratos em modo operacional;
    - não acessa cadastros nem usuários.

    Consulta/Escola:
    - dashboard;
    - documentos, contratos e relatórios apenas em modo consulta/download;
    - não pode enviar POST para criar/alterar/excluir dados.
    """

    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    perfil = obter_perfil_usuario(user)
    caminho = request.path
    metodo = request.method.upper()

    caminhos_gerais = [
        reverse("dashboard"),
        reverse("logout"),
    ]

    if any(caminho == item or caminho.startswith(item) for item in caminhos_gerais):
        return True

    if perfil == "administrador":
        return True

    if not perfil:
        return False

    if perfil == "pregoeiro":
        caminhos_permitidos = [
            "/pregoes/",
            "/execucao/",
            "/documentos/pregoes-finalizados/",
            "/documentos/contratos-gerados/",
            "/documentos/contratos/",
        ]

        caminhos_bloqueados = [
            "/usuarios/",
            "/cadastros/",
            "/documentos/realinhamento-precos/",
            "/documentos/realinhamentos/",
            "/documentos/troca-marca/",
        ]

        if any(caminho.startswith(item) for item in caminhos_bloqueados):
            return False

        trechos_bloqueados_contratos = [
            "/distrato/",
            "/cancelar/",
        ]

        if caminho.startswith("/documentos/contratos/") and any(
            trecho in caminho for trecho in trechos_bloqueados_contratos
        ):
            return False

        return any(caminho.startswith(item) for item in caminhos_permitidos)

    if perfil == "consulta_escola":
        if metodo not in ["GET", "HEAD", "OPTIONS"]:
            return False

        caminhos_permitidos = [
            "/documentos/pregoes-finalizados/",
            "/documentos/contratos-gerados/",
            "/documentos/contratos/",
            "/pregoes/relatorio-distribuicao/",
        ]

        caminhos_bloqueados = [
            "/documentos/realinhamento-precos/",
            "/documentos/realinhamentos/",
            "/documentos/troca-marca/",
            "/documentos/contratos-lote/",
            "/documentos/ajax/",
            "/usuarios/",
            "/cadastros/",
            "/execucao/",
        ]

        if any(caminho.startswith(item) for item in caminhos_bloqueados):
            return False

        trechos_bloqueados_contratos = [
            "/distrato/",
            "/cancelar/",
        ]

        if caminho.startswith("/documentos/contratos/") and any(
            trecho in caminho for trecho in trechos_bloqueados_contratos
        ):
            return False

        return any(caminho.startswith(item) for item in caminhos_permitidos)

    return False


class LoginRequiredMiddleware:
    """
    Bloqueia acesso sem login e aplica permissões por perfil.

    Exceções públicas:
    - login;
    - logout;
    - admin do Django;
    - arquivos estáticos;
    - arquivos de mídia, caso existam.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        caminho = request.path

        caminhos_livres = [
            reverse("login"),
            reverse("logout"),
            "/admin/",
            settings.STATIC_URL,
        ]

        media_url = getattr(settings, "MEDIA_URL", None)
        if media_url:
            caminhos_livres.append(media_url)

        acesso_livre = any(
            caminho == caminho_livre or caminho.startswith(caminho_livre)
            for caminho_livre in caminhos_livres
        )

        if not request.user.is_authenticated and not acesso_livre:
            login_url = reverse("login")
            return redirect(f"{login_url}?next={caminho}")

        if request.user.is_authenticated and not acesso_livre:
            if not usuario_pode_acessar_caminho(request.user, request):
                messages.error(
                    request,
                    "Você não tem permissão para acessar esta área do sistema.",
                )
                return redirect("dashboard")

        return self.get_response(request)
