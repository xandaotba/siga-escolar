import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import dj_database_url
from django.utils import timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .models import HistoricoBackup


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


class BackupBancoErro(RuntimeError):
    pass


def status_integracao_backup():
    return {
        "pg_dump": bool(shutil.which("pg_dump")),
        "credencial_google": bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()),
        "pasta_drive": bool(os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()),
        "database_url": bool(os.environ.get("DATABASE_URL", "").strip()),
    }


def integracao_backup_pronta():
    status = status_integracao_backup()
    return all(status.values())


def _credenciais_google():
    valor_b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()
    if not valor_b64:
        raise BackupBancoErro("A variável GOOGLE_SERVICE_ACCOUNT_JSON_B64 não está configurada.")

    try:
        dados = base64.b64decode(valor_b64).decode("utf-8")
        info = json.loads(dados)
    except Exception as exc:
        raise BackupBancoErro("Não foi possível ler a credencial da Service Account armazenada no Railway.") from exc

    try:
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=[DRIVE_SCOPE],
        )
    except Exception as exc:
        raise BackupBancoErro("A credencial da Service Account do Google é inválida.") from exc


def _servico_drive():
    credenciais = _credenciais_google()
    return build("drive", "v3", credentials=credenciais, cache_discovery=False)


def _configuracao_banco():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise BackupBancoErro("A variável DATABASE_URL não está disponível neste serviço do Railway.")

    try:
        config = dj_database_url.parse(database_url)
    except Exception as exc:
        raise BackupBancoErro("Não foi possível interpretar a DATABASE_URL do PostgreSQL.") from exc

    if not config.get("NAME"):
        raise BackupBancoErro("A DATABASE_URL não contém o nome do banco de dados.")

    return config


def _gerar_dump(caminho_destino):
    executavel = shutil.which("pg_dump")
    if not executavel:
        raise BackupBancoErro("pg_dump não foi encontrado no container. Verifique o Dockerfile.")

    config = _configuracao_banco()

    comando = [
        executavel,
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(caminho_destino),
    ]

    if config.get("HOST"):
        comando.extend(["--host", str(config["HOST"])])
    if config.get("PORT"):
        comando.extend(["--port", str(config["PORT"])])
    if config.get("USER"):
        comando.extend(["--username", str(config["USER"])])

    comando.extend(["--dbname", str(config["NAME"])])

    ambiente = os.environ.copy()
    if config.get("PASSWORD"):
        ambiente["PGPASSWORD"] = str(config["PASSWORD"])

    options = config.get("OPTIONS") or {}
    sslmode = options.get("sslmode")
    if sslmode:
        ambiente["PGSSLMODE"] = str(sslmode)

    try:
        resultado = subprocess.run(
            comando,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupBancoErro("O pg_dump ultrapassou o limite de 30 minutos.") from exc
    except Exception as exc:
        raise BackupBancoErro("Falha ao executar o pg_dump.") from exc

    if resultado.returncode != 0:
        detalhe = (resultado.stderr or resultado.stdout or "Erro não informado pelo pg_dump.").strip()
        raise BackupBancoErro(f"pg_dump falhou: {detalhe[:3000]}")

    arquivo = Path(caminho_destino)
    if not arquivo.exists() or arquivo.stat().st_size <= 0:
        raise BackupBancoErro("O pg_dump terminou sem gerar um arquivo de backup válido.")

    return arquivo.stat().st_size


def _validar_pasta_drive(servico, folder_id):
    try:
        pasta = servico.files().get(
            fileId=folder_id,
            fields="id,name,mimeType",
            supportsAllDrives=True,
        ).execute()
    except Exception as exc:
        raise BackupBancoErro(
            "A Service Account não conseguiu acessar a pasta configurada no Google Drive. "
            "Confirme se ela foi adicionada ao Drive compartilhado como Gerenciador de conteúdo."
        ) from exc

    if pasta.get("mimeType") != "application/vnd.google-apps.folder":
        raise BackupBancoErro("GOOGLE_DRIVE_FOLDER_ID não aponta para uma pasta do Google Drive.")

    return pasta


def _enviar_para_drive(caminho_arquivo, nome_arquivo):
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if not folder_id:
        raise BackupBancoErro("A variável GOOGLE_DRIVE_FOLDER_ID não está configurada.")

    servico = _servico_drive()
    _validar_pasta_drive(servico, folder_id)

    media = MediaFileUpload(
        str(caminho_arquivo),
        mimetype="application/octet-stream",
        resumable=True,
    )

    try:
        retorno = servico.files().create(
            body={
                "name": nome_arquivo,
                "parents": [folder_id],
            },
            media_body=media,
            fields="id,name,size,webViewLink",
            supportsAllDrives=True,
        ).execute()
    except Exception as exc:
        raise BackupBancoErro("Falha ao enviar o arquivo de backup para o Google Drive.") from exc

    if not retorno.get("id"):
        raise BackupBancoErro("O Google Drive não retornou o ID do arquivo enviado.")

    return retorno


def executar_backup_postgresql(tipo, usuario=None):
    if tipo not in {HistoricoBackup.TIPO_MANUAL, HistoricoBackup.TIPO_AUTOMATICO}:
        raise ValueError("Tipo de backup inválido.")

    inicio = timezone.now()
    momento_local = timezone.localtime(inicio)
    nome_arquivo = momento_local.strftime("SIGA_PostgreSQL_%Y-%m-%d_%H-%M-%S.dump")

    try:
        with tempfile.TemporaryDirectory(prefix="siga_backup_") as diretorio:
            caminho = Path(diretorio) / nome_arquivo
            tamanho = _gerar_dump(caminho)
            drive = _enviar_para_drive(caminho, nome_arquivo)

        fim = timezone.now()
        historico = HistoricoBackup.objects.create(
            tipo=tipo,
            status=HistoricoBackup.STATUS_CONCLUIDO,
            iniciado_em=inicio,
            concluido_em=fim,
            nome_arquivo=nome_arquivo,
            tamanho_bytes=tamanho,
            drive_file_id=drive.get("id", ""),
            drive_web_view_link=drive.get("webViewLink", ""),
            solicitado_por=usuario,
        )
        return historico

    except Exception as exc:
        fim = timezone.now()
        mensagem = str(exc) or exc.__class__.__name__

        HistoricoBackup.objects.create(
            tipo=tipo,
            status=HistoricoBackup.STATUS_ERRO,
            iniciado_em=inicio,
            concluido_em=fim,
            nome_arquivo=nome_arquivo,
            mensagem_erro=mensagem[:5000],
            solicitado_por=usuario,
        )

        if isinstance(exc, BackupBancoErro):
            raise
        raise BackupBancoErro(mensagem) from exc
