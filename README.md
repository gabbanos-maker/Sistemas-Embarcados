# Sistemas Embarcados — Trabalho Final 2026/2

Avaliação de tempos de resposta e cumprimento de deadlines com `SCHED_FIFO` em
Linux convencional e `PREEMPT_RT` (Raspberry Pi 3B+ e ESP32-WROOM).

Este repositório reúne o código, as configurações, os *scripts* de análise e os
dados usados nos gráficos e tabelas do relatório. O benchmark executado na
Raspberry Pi está em <https://github.com/crmoratelli/rpi_benchmark.git>.

## Estrutura

```
.
├── README.md
├── laboratorio-esp32-rpi/
│   ├── firmware_esp32/            # firmware do ESP32 (ESP-IDF)
│   ├── ferramentas/
│   │   ├── capture.py             # coletor (USB-UART) e decodificador
│   │   └── gerar_ccdf.py          # gera CCDFs, estatísticas e tabela
│   ├── requisitos-coleta.txt
│   ├── requisitos-analise.txt
│   ├── dados/
│   │   ├── E1-5ms-001/            # raw.bin, ofertas.csv, resumo.json
│   │   ├── E2-5ms-001/
│   │   ├── E3-5ms-001/
│   │   └── E4-5ms-001/
│   └── graficos/relatorio-5ms-001/  # saída do gerar_ccdf.py
└── relatorio/                     # fontes LaTeX (main.tex, secoes/, figuras/)
```

Também devem constar no repositório as configurações do Buildroot usadas nas
imagens (`configs/` e `custom-scripts/`).

## Casos e parâmetros

Período T = 5 ms, deadline D = 3 ms (contado da emissão efetiva de REQ),
120.000 ofertas por caso, 6.000 ofertas de aquecimento (excluídas da análise)
e janela final de 1 s. Política `SCHED_FIFO`, prioridade 40, afinidade ao
núcleo 2, nos quatro casos. REQ = GPIO25 (ESP32) → pino físico 11 / BCM17;
ACK = pino físico 13 / BCM27 → GPIO26; GND = pino físico 6.

| Caso | Pasta de dados | Kernel | Interferência |
|------|----------------|--------|---------------|
| E1 | `dados/E1-5ms-001` | Convencional | Sem carga |
| E2 | `dados/E2-5ms-001` | Convencional | CPU + memória (`stress-ng`) |
| E3 | `dados/E3-5ms-001` | PREEMPT_RT | Sem carga |
| E4 | `dados/E4-5ms-001` | PREEMPT_RT | CPU + memória (`stress-ng`) |

## Reproduzir os gráficos e as tabelas

Requer Python 3. Execute na pasta `laboratorio-esp32-rpi/`.

**Passo 1.** Criar o ambiente e instalar a dependência de análise (Matplotlib 3.10.9 foi a
versão usada):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requisitos-analise.txt
```

**Passo 2** (opcional). Conferir a integridade dos arquivos de entrada e do *script*:

```bash
sha256sum -c <<'EOF'
333ccbe3e6ffb36864b63d8e4dab4314379b8845f1c91df6f6d08121a5b00b13  dados/E1-5ms-001/ofertas.csv
9412ba68482e89d017879c6d1ba95b533237fa30c0d524a51be9638056950b9c  dados/E1-5ms-001/resumo.json
b78598371f44afc006a3f4390865d85e8d4188270b4602eb5d72494fd8e8202a  dados/E2-5ms-001/ofertas.csv
bf76577029564c335f3005de890c8f7989a7f401db06d03ae70c9ca01f2f2029  dados/E2-5ms-001/resumo.json
a17d54fa255d55523cbfd50fb0a61a62f5044372d403c09dc6617ac519e271ed  dados/E3-5ms-001/ofertas.csv
6e11a436112f914c1fef8209eaaa5ed9b0326b97968e1242b02a70d691186153  dados/E3-5ms-001/resumo.json
2ac84dcab3be3693f541e6d1a0fbf0b776ef3607ac006ed94781aaef7b064791  dados/E4-5ms-001/ofertas.csv
a35617b80ca452428d8109e0758516267f1d354da0fe793697a9a905ee326dd8  dados/E4-5ms-001/resumo.json
133dc1cafb63490b8c87b867a11f557ca9bc4fa681be8a7219c70ad254363754  ferramentas/gerar_ccdf.py
EOF
```

**Passo 3.** Gerar as figuras e a tabela (a pasta de saída **não pode existir**; para
repetir, use outro nome):

```bash
.venv/bin/python ferramentas/gerar_ccdf.py \
  --e1 dados/E1-5ms-001 \
  --e2 dados/E2-5ms-001 \
  --e3 dados/E3-5ms-001 \
  --e4 dados/E4-5ms-001 \
  --saida graficos/relatorio-5ms-001
```

Arquivos gerados em `graficos/relatorio-5ms-001/`:

| Arquivo | Uso no relatório |
|---------|------------------|
| `ccdf_sem_carga.png/.pdf/.svg` | CCDF de E1 e E3 (sem carga) |
| `ccdf_com_carga.png/.pdf/.svg` | CCDF de E2 e E4 (CPU + memória) |
| `estatisticas.csv` | Tabela comparativa: ofertas, respostas, mediana, p99, p99,9, máximo, falhas |
| `ccdf_E1.csv` … `ccdf_E4.csv` | Coordenadas das curvas |
| `analise.json` | Comando, versão do Matplotlib e hashes SHA-256 dos arquivos usados |

O `gerar_ccdf.py` exclui o aquecimento, usa as respostas observadas
(inclusive as tardias) e recusa capturas com `valida: false`. Ele não
verifica o kernel, o `SCHED_FIFO` nem a presença da carga: essas condições
constam nos registros do ensaio e no relatório.

Os arquivos `ccdf_sem_carga.png` e `ccdf_com_carga.png` e os valores de
`estatisticas.csv` são os que aparecem no relatório (Seção Resultados).

## Redecodificar uma captura bruta

```bash
.venv/bin/python ferramentas/capture.py \
  --decode dados/E1-5ms-001/raw.bin \
  --run-id E1-5ms-001 --output dados/E1-5ms-001-redecodificado
```

## Refazer as coletas

O procedimento completo (gravação do firmware, ligações, calibração do FIR,
perfil do `stress-ng` e execução dos quatro casos) está no tutorial da
disciplina, *Tutorial: ESP32-WROOM e Raspberry Pi*, versão 1.1. Em resumo:

```bash
# Computador: abrir o coletor antes do benchmark
.venv/bin/python ferramentas/capture.py --port /dev/ttyACM0 --baud 115200 \
  --run-id E1-5ms-001 --offers 120000 --warmup 6000 \
  --wait-ready --output dados/E1-5ms-001

# Raspberry (mesmo comando nos quatro casos)
benchmark --chip /dev/gpiochip0 --req 17 --ack 27 \
  --cpu 2 --policy fifo --priority 40 $FROZEN_ARGS
```

Nos casos E2 e E4, a carga é iniciada antes do benchmark:

```bash
chrt -o 0 taskset -c 0-3 stress-ng \
  --cpu 4 --cpu-method matrixprod \
  --vm 4 --vm-bytes 384M \
  --cache 8 --cache-no-affinity \
  --timeout 15m --metrics-brief
```

Ordem fixa E1 → E2 → E3 → E4, com troca única de imagem (convencional para
RT) entre E2 e E3. Cada nova coleta exige pasta e identificador novos.

## Imagens Linux

Imagens ARM64 geradas com o Buildroot (`buildroot-2026.08.1`, kernel
Raspberry Pi 6.12.61): `sdcard-noRT.img` (convencional) e `sdcard-RT.img`
(`PREEMPT_RT`). Por serem grandes, não ficam no repositório; estão em
<https://drive.google.com/drive/folders/1Vsl0iW_Q2c15tMrOMf364UWYJp8VyBAe?usp=sharing>.

## Compilar o relatório

Na pasta `relatorio/`, com `logo.png` presente, compile `main.tex` duas vezes
(por exemplo, `pdflatex main.tex` duas vezes) para resolver as referências.

## Versão usada nos experimentos

Commit/tag: **PREENCHER** (identificar aqui o commit usado nas quatro coletas).
