from django.db import migrations, models
import apps.cadastros.models


class Migration(migrations.Migration):

    dependencies = [
        ("pregoes", "0009_pregao_numero_processo"),
    ]

    operations = [
        migrations.AddField(
            model_name="pregao",
            name="nome_ordenador_despesas",
            field=models.CharField(
                blank=True,
                help_text="Nome completo do ordenador de despesas vinculado ao certame.",
                max_length=255,
                verbose_name="Nome do Ordenador de Despesas",
            ),
        ),
        migrations.AddField(
            model_name="pregao",
            name="rg_ordenador_despesas",
            field=models.CharField(
                blank=True,
                max_length=50,
                verbose_name="RG do Ordenador de Despesas",
            ),
        ),
        migrations.AddField(
            model_name="pregao",
            name="cpf_ordenador_despesas",
            field=models.CharField(
                blank=True,
                max_length=14,
                validators=[apps.cadastros.models.validar_cpf],
                verbose_name="CPF do Ordenador de Despesas",
            ),
        ),
    ]
