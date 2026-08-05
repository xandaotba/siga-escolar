from .models import RegistroAuditoria


def obter_ip_request(request):
    if not request:
        return None

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")

    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def obter_user_agent(request):
    if not request:
        return ""

    return request.META.get("HTTP_USER_AGENT", "")


def usuario_para_campos(user):
    if not user or not getattr(user, "is_authenticated", False):
        return {
            "usuario": None,
            "usuario_nome": "",
            "usuario_username": "",
        }

    return {
        "usuario": user,
        "usuario_nome": user.get_full_name() or user.username,
        "usuario_username": user.username,
    }


def registrar_auditoria(
    *,
    request=None,
    usuario=None,
    acao,
    modulo="",
    descricao="",
    objeto=None,
    objeto_tipo="",
    objeto_id="",
    objeto_repr="",
    dados=None,
):
    """
    Registra um evento de auditoria sem interromper o fluxo do sistema.

    Use esta função dentro de views quando quiser registrar algo específico.
    O middleware já registra automaticamente POST/PUT/PATCH/DELETE bem-sucedidos.
    """

    try:
        user = usuario or (request.user if request and getattr(request, "user", None) else None)
        campos_usuario = usuario_para_campos(user)

        metodo = ""
        caminho = ""
        view_name = ""
        ip = None
        user_agent = ""

        if request:
            metodo = request.method
            caminho = request.path
            ip = obter_ip_request(request)
            user_agent = obter_user_agent(request)

            try:
                resolver_match = request.resolver_match
                if resolver_match:
                    namespace = resolver_match.namespace or ""
                    url_name = resolver_match.url_name or ""
                    view_name = f"{namespace}:{url_name}" if namespace else url_name
            except Exception:
                view_name = ""

        if objeto is not None:
            objeto_tipo = objeto_tipo or objeto.__class__.__name__
            objeto_id = objeto_id or str(getattr(objeto, "id", ""))
            objeto_repr = objeto_repr or str(objeto)

        return RegistroAuditoria.objects.create(
            **campos_usuario,
            acao=acao,
            modulo=modulo or "",
            descricao=descricao or "",
            objeto_tipo=objeto_tipo or "",
            objeto_id=str(objeto_id or ""),
            objeto_repr=str(objeto_repr or "")[:255],
            metodo=metodo,
            caminho=caminho[:500],
            view_name=view_name[:180],
            ip=ip,
            user_agent=user_agent,
            dados=dados or {},
        )
    except Exception:
        # Auditoria nunca deve derrubar o sistema principal.
        return None


def classificar_acao_por_request(request):
    caminho = (request.path or "").lower()
    view_name = ""

    try:
        resolver_match = request.resolver_match
        if resolver_match:
            namespace = resolver_match.namespace or ""
            url_name = resolver_match.url_name or ""
            view_name = f"{namespace}:{url_name}" if namespace else url_name
    except Exception:
        view_name = ""

    alvo = f"{caminho} {view_name}".lower()

    if "/importacoes/" in alvo:
        return RegistroAuditoria.ACAO_IMPORTACAO

    if "/exportacoes/" in alvo or "backup" in alvo:
        return RegistroAuditoria.ACAO_EXPORTACAO

    if "finalizar" in alvo or "finalizacao" in alvo:
        return RegistroAuditoria.ACAO_FINALIZACAO

    if "distrato" in alvo or "rescisao" in alvo:
        return RegistroAuditoria.ACAO_DISTRATO

    if "realinhamento" in alvo:
        return RegistroAuditoria.ACAO_REALINHAMENTO

    if "troca-marca" in alvo or "marca" in alvo and "documentos" in alvo:
        return RegistroAuditoria.ACAO_TROCA_MARCA

    if "cancelar" in alvo or "cancelamento" in alvo:
        return RegistroAuditoria.ACAO_CANCELAMENTO

    if "gerar" in alvo or "word" in alvo or "pdf" in alvo or "contrato" in alvo:
        return RegistroAuditoria.ACAO_GERACAO_DOCUMENTO

    if "excluir" in alvo or "delete" in alvo:
        return RegistroAuditoria.ACAO_EXCLUSAO

    if "editar" in alvo or "alterar" in alvo:
        return RegistroAuditoria.ACAO_EDICAO

    if "criar" in alvo or "cadastrar" in alvo or "novo" in alvo:
        return RegistroAuditoria.ACAO_CRIACAO

    return RegistroAuditoria.ACAO_POST


def modulo_por_request(request):
    caminho = (request.path or "").strip("/")

    if not caminho:
        return "core"

    return caminho.split("/")[0]
