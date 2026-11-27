#!/usr/bin/env python3
"""Gera CCDFs e tabela para o relatório a partir das pastas do coletor."""
import argparse
from bisect import bisect_right
from collections import Counter
import csv
import hashlib
import json
import math
from statistics import median
from pathlib import Path
import sys

STATES = ('no_prazo', 'atrasada', 'nao_admitida_ocupado', 'sem_resposta_ao_encerrar')
CONDITIONS = {
    'E1': ('Convencional', 'Sem carga'), 'E2': ('Convencional', 'CPU + memória'),
    'E3': ('PREEMPT_RT', 'Sem carga'), 'E4': ('PREEMPT_RT', 'CPU + memória'),
}


def percentile(values, p):
    """Percentil empírico por posto: x[ceil(p*n)-1], sem interpolação."""
    return values[math.ceil(p * len(values)) - 1] if values else None


def ccdf(values):
    """P(R > x), após todos os empates; inclui zero real no maior valor."""
    return [(x, (len(values) - bisect_right(values, x)) / len(values))
            for x in sorted({0, *values})] if values else []


def load_run(folder, condition):
    folder = Path(folder)
    summary = json.loads((folder / 'resumo.json').read_text())
    if summary.get('valida') is not True or summary.get('erros') != []:
        raise ValueError(f'{condition}: captura inválida; examine resumo.json')
    meta = summary['metadados']
    if (meta['periodo_us'], meta['deadline_us']) not in ((5000, 3000), (10000, 10000)):
        raise ValueError(f'{condition}: protocolo desconhecido: esperado T=5000/D=3000 µs ou legado T=D=10000 µs')
    n, warmup = meta['ofertas'], meta['aquecimento_ofertas']
    if not isinstance(n, int) or n <= 0 or not isinstance(warmup, int) or warmup < 0:
        raise ValueError(f'{condition}: total de ofertas inválido')
    integrity = summary['integridade']
    for key in ('overflow', 'ack_inesperado', 'ofertas_perdidas_gerador', 'erros_gpio_timer', 'flags'):
        if integrity[key] != 0:
            raise ValueError(f'{condition}: falha de integridade ({key})')
    if summary['crc_erros'] != 0 or summary['bytes_inesperados'] != 0:
        raise ValueError(f'{condition}: erros de comunicação')
    seen = {'coleta': set(), 'aquecimento': set()}
    counts = Counter()
    responses = []
    with (folder / 'ofertas.csv').open(newline='') as source:
        for row in csv.DictReader(source):
            phase = row['fase']
            if phase not in seen or row['execucao'] != summary['execucao']:
                raise ValueError(f'{condition}: fase ou execução divergente no CSV')
            index = int(row['indice'])
            limit = n if phase == 'coleta' else warmup
            if index in seen[phase] or not 0 <= index < limit:
                raise ValueError(f'{condition}: índice duplicado ou fora do intervalo')
            seen[phase].add(index)
            state = row['estado']
            if state not in STATES or int(row['falha_gerador']) != 0:
                raise ValueError(f'{condition}: estado inválido ou falha do gerador')
            response = None
            if state in STATES[:2]:
                req, ack, response = (int(row[k]) for k in ('emissao_us', 'ack_us', 'resposta_us'))
                if response < 0 or ack - req != response:
                    raise ValueError(f'{condition}: resposta incompatível com REQ/ACK')
                if (response <= meta['deadline_us']) != (state == 'no_prazo'):
                    raise ValueError(f'{condition}: estado incompatível com deadline')
            elif row['resposta_us'] or row['ack_us']:
                raise ValueError(f'{condition}: resposta em oferta sem ACK')
            if phase == 'coleta':
                counts[state] += 1
                if response is not None:
                    responses.append(response)
    if len(seen['coleta']) != n or len(seen['aquecimento']) != warmup:
        raise ValueError(f'{condition}: CSV incompleto')
    if any(counts[state] != summary['contagens'][state] for state in STATES):
        raise ValueError(f'{condition}: contagens divergem do resumo')
    responses.sort()
    v = len(responses)
    if v != summary['respostas_observadas']:
        raise ValueError(f'{condition}: número de respostas diverge do resumo')
    row = dict(caso=condition, execucao=summary['execucao'], kernel=CONDITIONS[condition][0],
               carga=CONDITIONS[condition][1], periodo_us=meta['periodo_us'], deadline_us=meta['deadline_us'], ofertas=n, respostas=v, fracao_respostas=v/n,
               **{state: counts[state] for state in STATES},
               parcela_atrasada=counts['atrasada']/n,
               parcela_nao_admitida=counts['nao_admitida_ocupado']/n,
               parcela_sem_resposta=counts['sem_resposta_ao_encerrar']/n,
               taxa_global_falha=sum(counts[state] for state in STATES[1:])/n,
               mediana_us=median(responses) if v else None, p99_us=percentile(responses, .99),
               p99_9_us=percentile(responses, .999), maximo_observado_us=responses[-1] if v else None)
    return dict(folder=folder, meta=meta, row=row, responses=responses, points=ccdf(responses))


def write_csv(path, rows, fields):
    with path.open('x', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plot(runs, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    deadline = next(iter(runs.values()))['meta']['deadline_us']
    max_response = max((r['responses'][-1] for r in runs.values() if r['responses']), default=0)
    max_n = max((len(r['responses']) for r in runs.values()), default=1)
    # As duas figuras usam os mesmos limites; o menor passo empírico é 1/|V|.
    xmax, ymin = max(deadline, max_response) * 1.05, 1 / (max(1, max_n) * 2)
    for pair, title, filename in [(('E1', 'E3'), 'Sem carga', 'ccdf_sem_carga'),
                                  (('E2', 'E4'), 'CPU + memória', 'ccdf_com_carga')]:
        fig, ax = plt.subplots(figsize=(9, 5.5), layout='constrained')
        missing = []
        notes = []
        for case, color in zip(pair, ('#1764ab', '#bf431b')):
            if case not in runs:
                missing.append(case)
                continue
            run = runs[case]
            row = run['row']
            label = f"{case}: {row['kernel']} — |V|/N={row['respostas']}/{row['ofertas']}"
            points = run['points']
            if points:
                ax.step([x for x, y in points], [y for x, y in points], where='post',
                        color=color, label=label)
            else:
                notes.append(f'{case}: sem respostas observadas; CCDF indefinida')
        ax.axvline(deadline, color='#444444', linestyle='--', label=f'Deadline: {deadline:,} µs'.replace(',', '.'))
        ax.set_yscale('log', nonpositive='mask')
        ax.set(xlim=(0, xmax), ylim=(ymin, 1.1), xlabel='Tempo de resposta R (µs)',
               ylabel='P(R > x) — respostas observadas',
               title=f'CCDF empírica — {title}' + (' (dados parciais)' if missing else ''))
        ax.grid(True, which='both', alpha=.2)
        ax.legend(loc='upper right', fontsize=9)
        if missing:
            notes.append('Casos não fornecidos: ' + ', '.join(missing))
        if notes:
            ax.text(.02, .03, '\n'.join(notes), transform=ax.transAxes, fontsize=9,
                    bbox=dict(facecolor='white', alpha=.85, edgecolor='none'))
        for extension in ('png', 'pdf', 'svg'):
            fig.savefig(output / f'{filename}.{extension}', dpi=200)
        plt.close(fig)
    return matplotlib.__version__


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for case in CONDITIONS:
        parser.add_argument('--' + case.lower(), type=Path, help=f'pasta com ofertas.csv e resumo.json de {case}')
    parser.add_argument('--saida', type=Path, required=True, help='pasta nova para gráficos e tabelas')
    args = parser.parse_args(argv)
    folders = {case: getattr(args, case.lower()) for case in CONDITIONS if getattr(args, case.lower())}
    if not folders:
        parser.error('informe ao menos um caso: --e1, --e2, --e3 ou --e4')
    if len({p.resolve() for p in folders.values()}) != len(folders):
        parser.error('não use a mesma pasta como condições diferentes')
    try:
        runs = {case: load_run(folder, case) for case, folder in folders.items()}
        first = next(iter(runs.values()))['meta']
        for case, run in runs.items():
            for key in ('ofertas', 'aquecimento_ofertas', 'periodo_us', 'deadline_us',
                        'janela_final_us', 'req_gpio', 'ack_gpio'):
                if run['meta'][key] != first[key]:
                    raise ValueError(f'{case}: {key} difere dos demais casos')
        try:
            import matplotlib  # noqa: F401 — verificar dependência antes de criar a saída
        except ImportError:
            raise ValueError('instale as dependências: .venv/bin/python -m pip install -r requisitos-analise.txt') from None
        args.saida.mkdir(parents=True, exist_ok=False)
        rows = [run['row'] for run in runs.values()]
        write_csv(args.saida / 'estatisticas.csv', rows, list(rows[0]))
        for case, run in runs.items():
            write_csv(args.saida / f'ccdf_{case}.csv',
                      [dict(tempo_us=x, probabilidade_maior=y) for x, y in run['points']],
                      ['tempo_us', 'probabilidade_maior'])
        version = plot(runs, args.saida)
        manifest = dict(comando=sys.argv if argv is None else argv, matplotlib=version,
                        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        convencao='Aula: E1 convencional sem carga; E2 convencional com carga; '
                                  'E3 RT sem carga; E4 RT com carga.',
                        percentis='Posto empírico x[ceil(p*n)-1], sem interpolação.',
                        ccdf='P(R>x) sobre respostas observadas da fase coleta; inclui tardias. '
                             'Zero preservado no CSV e omitido no eixo logarítmico.',
                        entradas={case: dict(pasta=str(run['folder'].resolve()), metadados=run['meta'],
                            sha256={name: hashlib.sha256((run['folder']/name).read_bytes()).hexdigest()
                                    for name in ('ofertas.csv', 'resumo.json')}) for case, run in runs.items()})
        (args.saida / 'analise.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
        print(f'Arquivos gerados em {args.saida}')
        for row in rows:
            print(f"{row['caso']}: {row['respostas']}/{row['ofertas']} respostas; "
                  f"falha global={row['taxa_global_falha']:.6%}; máximo={row['maximo_observado_us']} µs")
        if len(runs) < 4:
            print('Análise parcial: os casos ausentes estão identificados nos gráficos.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
