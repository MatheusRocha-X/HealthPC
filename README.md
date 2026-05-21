# HealthPC

Aplicação desktop em Python para analisar e limpar arquivos temporários, caches e logs no Windows com mais controle antes da remoção.

O projeto foi pensado para praticidade no dia a dia: centralizar em uma única interface a análise dos principais locais que costumam acumular arquivos desnecessários, com visualização prévia e opção de agendamento.

## Tecnologias

- Python
- `pywebview`
- HTML, CSS e JavaScript
- `psutil`
- `PyInstaller`

## Recursos

- Interface desktop com frontend web embarcado.
- Análise prévia antes de excluir qualquer arquivo.
- Seleção manual dos locais que entram na limpeza.
- Resumo por categoria do que foi encontrado.
- Proteção de arquivos recentes por padrão.
- Histórico local das limpezas realizadas.
- Agendamento automático no Windows.
- Remoção de arquivos com tentativa de limpeza de pastas vazias ao final.

## Locais analisados

O HealthPC pode detectar automaticamente categorias como:

- Temp do usuário
- Arquivos temporários do Windows
- Cache de navegador
- Cache da GPU/DirectX
- Prefetch
- Logs antigos
- Lixeira
- Cache do Windows Update

## Cuidados de segurança

- A ferramenta faz análise antes da remoção.
- Arquivos recentes podem ser preservados por configuração.
- Links simbólicos e junctions são ignorados para evitar sair das pastas permitidas.
- Locais mais sensíveis ficam desmarcados por padrão.
- Arquivos em uso, bloqueados ou sem permissão são ignorados e reportados como aviso.

## Como executar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Gerar executável

Se o comando `pyinstaller` não estiver disponível no `PATH`, use o módulo diretamente:

```powershell
python -m PyInstaller --noconsole --onefile --name HealthPC --icon ui\icon.png --add-data "ui;ui" main.py
```

O executável será gerado em `dist\HealthPC.exe`.

## Estrutura do projeto

- `main.py`: inicialização da aplicação, API entre interface e backend, histórico e agendamento.
- `cleaner_core.py`: descoberta dos diretórios, análise dos arquivos e rotina de limpeza.
- `ui/`: interface HTML, estilos e comportamento da aplicação.

## Observações

- O foco atual do projeto é Windows.
- Algumas pastas do sistema podem exigir privilégios de administrador.
- A limpeza é permanente. Revise a análise antes de confirmar a remoção.
