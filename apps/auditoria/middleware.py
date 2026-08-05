from .models import RegistroAuditoria
from .utils import classificar_acao_por_request, modulo_por_request, registrar_auditoria


class AuditoriaMiddleware:
    """
    Registra automaticamente requisições que alteram dados.

    Para evitar excesso de registros, somente métodos POST/PUT/PATCH/DELETE
    com resposta bem-sucedida são gravados.
    """

    METODOS_AUDITADOS = {"POST", "PUT", "PATCH", "DELETE"}

    CAMINHOS_IGNORADOS = [
        "/admin/jsi18n/",
        "/auditoria/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        try:
            if request.method.upper() not in self.METODOS_AUDITADOS:
                return response

            if any(request.path.startswith(caminho) for caminho in self.CAMINHOS_IGNORADOS):
                return response

            if getattr(response, "status_code", 500) >= 400:
                return response

            if not getattr(request, "user", None) or not request.user.is_authenticated:
                return response

            acao = classificar_acao_por_request(request)
            modulo = modulo_por_request(request)

            descricao = f"{request.method.upper()} em {request.path}"

            registrar_auditoria(
                request=request,
                acao=acao,
                modulo=modulo,
                descricao=descricao,
                dados={
                    "status_code": getattr(response, "status_code", None),
                    "content_type": response.get("Content-Type", "") if hasattr(response, "get") else "",
                },
            )
        except Exception:
            return response

        return response
