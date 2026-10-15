from datetime import datetime, time as dt_time, timezone as dt_timezone

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.importacoes.backup_service import executar_backup_postgresql
from apps.importacoes.models import ConfiguracaoBackup, HistoricoBackup


class Command(BaseCommand):
    help = "Executa o backup diário quando o horário configurado no SIGA já foi atingido."

    def handle(self, *args, **options):
        with transaction.atomic():
            configuracao = ConfiguracaoBackup.objects.select_for_update().filter(pk=1).first()

            if not configuracao:
                configuracao = ConfiguracaoBackup.obter()

            if not configuracao.ativo:
                self.stdout.write("Backup automático está desativado no SIGA.")
                return

            fuso = ZoneInfo(configuracao.timezone or "America/Cuiaba")
            agora_local = timezone.now().astimezone(fuso)
            horario = configuracao.horario_diario or dt_time(2, 0)
            alvo_local = datetime.combine(agora_local.date(), horario, tzinfo=fuso)

            if agora_local < alvo_local:
                self.stdout.write(
                    f"Ainda não chegou o horário de hoje ({horario.strftime('%H:%M')})."
                )
                return

            inicio_dia_local = datetime.combine(
                agora_local.date(),
                dt_time.min,
                tzinfo=fuso,
            )
            fim_dia_local = datetime.combine(
                agora_local.date(),
                dt_time.max,
                tzinfo=fuso,
            )

            ja_concluido = HistoricoBackup.objects.filter(
                tipo=HistoricoBackup.TIPO_AUTOMATICO,
                status=HistoricoBackup.STATUS_CONCLUIDO,
                iniciado_em__gte=inicio_dia_local.astimezone(dt_timezone.utc),
                iniciado_em__lte=fim_dia_local.astimezone(dt_timezone.utc),
            ).exists()

            if ja_concluido:
                self.stdout.write("O backup automático de hoje já foi concluído.")
                return

        # Executa fora da transação para não manter lock durante pg_dump/upload.
        historico = executar_backup_postgresql(
            tipo=HistoricoBackup.TIPO_AUTOMATICO,
            usuario=None,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Backup automático concluído: {historico.nome_arquivo}"
            )
        )
