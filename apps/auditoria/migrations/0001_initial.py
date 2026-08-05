# Generated manually for Etapa 76 — Auditoria

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RegistroAuditoria",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("usuario_nome", models.CharField(blank=True, max_length=255, verbose_name="Nome do usuário")),
                ("usuario_username", models.CharField(blank=True, max_length=150, verbose_name="Login do usuário")),
                ("acao", models.CharField(choices=[("login", "Login"), ("logout", "Logout"), ("acesso_bloqueado", "Acesso bloqueado"), ("criacao", "Criação"), ("edicao", "Edição"), ("exclusao", "Exclusão"), ("importacao", "Importação"), ("exportacao", "Exportação"), ("geracao_documento", "Geração de documento"), ("cancelamento", "Cancelamento"), ("distrato", "Distrato"), ("realinhamento", "Realinhamento de preço"), ("troca_marca", "Troca de marca"), ("finalizacao", "Finalização"), ("post", "Alteração")], max_length=40, verbose_name="Ação")),
                ("modulo", models.CharField(blank=True, max_length=100, verbose_name="Módulo")),
                ("descricao", models.TextField(verbose_name="Descrição")),
                ("objeto_tipo", models.CharField(blank=True, max_length=120, verbose_name="Tipo do objeto")),
                ("objeto_id", models.CharField(blank=True, max_length=80, verbose_name="ID do objeto")),
                ("objeto_repr", models.CharField(blank=True, max_length=255, verbose_name="Objeto")),
                ("metodo", models.CharField(blank=True, max_length=10, verbose_name="Método HTTP")),
                ("caminho", models.CharField(blank=True, max_length=500, verbose_name="Caminho")),
                ("view_name", models.CharField(blank=True, max_length=180, verbose_name="View")),
                ("ip", models.GenericIPAddressField(blank=True, null=True, verbose_name="IP")),
                ("user_agent", models.TextField(blank=True, verbose_name="Navegador/Dispositivo")),
                ("dados", models.JSONField(blank=True, default=dict, verbose_name="Dados extras")),
                ("criado_em", models.DateTimeField(auto_now_add=True, verbose_name="Data/Hora")),
                ("usuario", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="registros_auditoria", to=settings.AUTH_USER_MODEL, verbose_name="Usuário")),
            ],
            options={
                "verbose_name": "Registro de Auditoria",
                "verbose_name_plural": "Registros de Auditoria",
                "ordering": ["-criado_em"],
            },
        ),
        migrations.AddIndex(
            model_name="registroauditoria",
            index=models.Index(fields=["-criado_em"], name="auditoria_r_criado__4f86db_idx"),
        ),
        migrations.AddIndex(
            model_name="registroauditoria",
            index=models.Index(fields=["acao"], name="auditoria_r_acao_f6286a_idx"),
        ),
        migrations.AddIndex(
            model_name="registroauditoria",
            index=models.Index(fields=["modulo"], name="auditoria_r_modulo_141baa_idx"),
        ),
        migrations.AddIndex(
            model_name="registroauditoria",
            index=models.Index(fields=["usuario"], name="auditoria_r_usuario_5818cd_idx"),
        ),
    ]
