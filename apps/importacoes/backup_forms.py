from datetime import time

from django import forms

from .models import ConfiguracaoBackup


HORARIOS_BACKUP = [(f"{hora:02d}:00", f"{hora:02d}:00") for hora in range(24)]


class ConfiguracaoBackupForm(forms.ModelForm):
    horario_diario = forms.ChoiceField(
        label="Horário diário",
        choices=HORARIOS_BACKUP,
        help_text="Horário de Mato Grosso (America/Cuiaba).",
    )

    class Meta:
        model = ConfiguracaoBackup
        fields = ["ativo", "horario_diario"]
        widgets = {
            "ativo": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk and self.instance.horario_diario:
            self.initial["horario_diario"] = self.instance.horario_diario.strftime("%H:%M")

    def clean_horario_diario(self):
        valor = self.cleaned_data["horario_diario"]
        hora = int(valor.split(":", 1)[0])
        return time(hora, 0)
