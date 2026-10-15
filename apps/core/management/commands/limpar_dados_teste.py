from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.contrib.auth import get_user_model

from apps.auditoria.models import RegistroAuditoria
from apps.aquisicoes.models import (
    AquisicaoNotaFiscal,
    AquisicaoNotaFiscalItem,
    ProdutoNotaFiscalMapeamento,
)
from apps.cadastros.models import (
    Escola,
    Fornecedor,
    Item,
    Municipio,
    UnidadeMedida,
)
from apps.documentos.models import (
    ContratoGerado,
    ContratoItemGerado,
    DistratoContrato,
    DistratoContratoItem,
    ProjetoVenda,
    ProjetoVendaItem,
    RealinhamentoPreco,
    RealinhamentoPrecoItem,
    ResultadoChamadaPublicaItem,
    TrocaMarca,
    TrocaMarcaItem,
)
from apps.execucao.models import BeneficioMEEPP
from apps.pregoes.models import (
    DesistenciaItem,
    Lance,
    Pregao,
    PregaoFornecedor,
    PregaoItem,
    PregaoMunicipio,
    PropostaDigital,
    PropostaDigitalLink,
    PropostaInicialItem,
    QuantitativoEscola,
    QuantitativoPregao,
    ResultadoItem,
)
from apps.usuarios.models import PerfilUsuario


# Ordem deliberada para respeitar ForeignKeys com PROTECT.
DELETE_ORDER = [
    # Aquisições / NF-e
    AquisicaoNotaFiscalItem,
    AquisicaoNotaFiscal,
    ProdutoNotaFiscalMapeamento,

    # Chamada pública / documentos
    ResultadoChamadaPublicaItem,
    ProjetoVendaItem,
    ProjetoVenda,

    # Contratos e alterações contratuais
    TrocaMarcaItem,
    TrocaMarca,
    RealinhamentoPrecoItem,
    RealinhamentoPreco,
    DistratoContratoItem,
    DistratoContrato,
    ContratoItemGerado,
    ContratoGerado,

    # Execução
    BeneficioMEEPP,

    # Propostas digitais precisam sair antes do link (FK PROTECT)
    PropostaDigital,
    PropostaDigitalLink,

    # Pregão / propostas / lances / resultados
    ResultadoItem,
    DesistenciaItem,
    Lance,
    PropostaInicialItem,
    PregaoItem,
    QuantitativoEscola,
    QuantitativoPregao,
    PregaoFornecedor,
    PregaoMunicipio,
    Pregao,

    # Auditoria dos testes
    RegistroAuditoria,

    # Cadastros básicos
    Item,
    UnidadeMedida,
    Escola,
    Fornecedor,
    Municipio,
]


class Command(BaseCommand):
    help = (
        "Remove os dados operacionais/de teste do SIGA Escolar, "
        "preservando usuários, grupos, permissões e PerfilUsuario."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--simular",
            action="store_true",
            help="Mostra o que será apagado, sem alterar o banco.",
        )
        parser.add_argument(
            "--confirmar",
            type=str,
            default="",
            help='Para executar de verdade, informe: --confirmar LIMPAR-SIGA',
        )
        parser.add_argument(
            "--manter-unidades-medida",
            action="store_true",
            help="Preserva o cadastro de UnidadeMedida.",
        )

    def handle(self, *args, **options):
        simular = options["simular"]
        confirmacao = (options["confirmar"] or "").strip()
        manter_unidades = options["manter_unidades_medida"]

        User = get_user_model()

        modelos = [
            model
            for model in DELETE_ORDER
            if not (manter_unidades and model is UnidadeMedida)
        ]

        usuarios = User.objects.count()
        perfis = PerfilUsuario.objects.count()
        perfis_com_escola = PerfilUsuario.objects.exclude(escola__isnull=True).count()

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("=" * 72))
        self.stdout.write(self.style.WARNING(" LIMPEZA DOS DADOS DE TESTE - SIGA ESCOLAR"))
        self.stdout.write(self.style.WARNING("=" * 72))
        self.stdout.write("")
        self.stdout.write(f"Usuários que serão PRESERVADOS: {usuarios}")
        self.stdout.write(f"Perfis de usuário que serão PRESERVADOS: {perfis}")
        self.stdout.write(
            f"Perfis atualmente vinculados a escola: {perfis_com_escola}"
        )
        if perfis_com_escola:
            self.stdout.write(
                self.style.WARNING(
                    "ATENÇÃO: esses perfis terão apenas o vínculo com a escola removido, "
                    "pois as escolas serão apagadas. O usuário e seu perfil serão mantidos."
                )
            )

        self.stdout.write("")
        self.stdout.write("Registros encontrados para limpeza:")
        self.stdout.write("-" * 72)

        total_previsto = 0
        contagens = {}
        for model in modelos:
            qtd = model.objects.count()
            contagens[model] = qtd
            total_previsto += qtd
            self.stdout.write(
                f"{model._meta.app_label}.{model.__name__}: {qtd}"
            )

        self.stdout.write("-" * 72)
        self.stdout.write(f"Total de registros listados: {total_previsto}")
        self.stdout.write(
            "Unidades de Medida: "
            + ("PRESERVADAS" if manter_unidades else "SERÃO APAGADAS")
        )
        self.stdout.write("")

        if simular:
            self.stdout.write(
                self.style.SUCCESS(
                    "SIMULAÇÃO CONCLUÍDA. Nenhum dado foi alterado."
                )
            )
            return

        if confirmacao != "LIMPAR-SIGA":
            raise CommandError(
                "Operação cancelada. Para apagar de verdade, use "
                "--confirmar LIMPAR-SIGA. Antes disso, execute --simular."
            )

        self.stdout.write(
            self.style.WARNING(
                "Executando limpeza. Não interrompa o processo..."
            )
        )

        excluidos_por_modelo = {}

        with transaction.atomic():
            # PerfilUsuario deve ser preservado, mas Escola usa PROTECT.
            # Portanto removemos somente o vínculo do perfil com a escola.
            perfis_desvinculados = (
                PerfilUsuario.objects
                .exclude(escola__isnull=True)
                .update(escola=None)
            )

            for model in modelos:
                # delete() pode também contar cascatas. Como os dependentes já
                # são apagados antes, normalmente este total corresponde ao modelo.
                total_delete, detalhes = model.objects.all().delete()
                excluidos_por_modelo[model] = {
                    "total_delete": total_delete,
                    "detalhes": detalhes,
                }

            # Reinicia somente as sequências dos modelos apagados.
            # NÃO inclui User nem PerfilUsuario.
            sql_list = connection.ops.sequence_reset_sql(no_style(), modelos)
            if sql_list:
                with connection.cursor() as cursor:
                    for sql in sql_list:
                        cursor.execute(sql)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 72))
        self.stdout.write(self.style.SUCCESS(" LIMPEZA CONCLUÍDA COM SUCESSO"))
        self.stdout.write(self.style.SUCCESS("=" * 72))
        self.stdout.write(f"Usuários preservados: {User.objects.count()}")
        self.stdout.write(f"Perfis preservados: {PerfilUsuario.objects.count()}")
        self.stdout.write(f"Perfis desvinculados de escola: {perfis_desvinculados}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Observação: arquivos físicos de mídia (por exemplo XML de NF-e) "
                "não foram apagados. Este comando limpa os registros do banco."
            )
        )
